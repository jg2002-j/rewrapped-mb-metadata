import duckdb
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime

from queries import BUILD_LINK_LOOKUP_SQL, BUILD_TEXT_LOOKUP_SQL
from schema import TABLE_SCHEMAS, TABLE_MAPPING

DB_NAME = "metadata_core.db"
DUCKDB_TMP = "duckdb_working.db"
TEMP_TSV = "temp_extract.tsv"

# PROD FILES
TAR_FILES = ["mbdump.tar.bz2"]
# TEST FILES
# TAR_FILES = ["test_mbdump.tar.bz2"]


def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


def init_duckdb():
    log("Initializing DuckDB engine for GitHub Actions environment...")
    if os.path.exists(DUCKDB_TMP):
        os.remove(DUCKDB_TMP)

    con = duckdb.connect(DUCKDB_TMP)

    # Strictly optimized for 7GB RAM GitHub Runner limits
    con.execute("SET max_memory='6GB';")
    con.execute("SET threads=2;")  # Keep threads low to minimize parallel memory allocation
    con.execute("SET preserve_insertion_order=false;")

    return con


def init_target_sqlite():
    log(f"Initializing target SQLite asset container: {DB_NAME}")
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
                   CREATE TABLE link_canonical_lookup
                   (
                       streaming_link      TEXT PRIMARY KEY,
                       track_title         TEXT    NOT NULL,
                       duration_ms         INTEGER NOT NULL,
                       album_mbid          TEXT    NOT NULL,
                       album_title         TEXT    NOT NULL,
                       release_group_mbid  TEXT    NOT NULL,
                       release_group_title TEXT    NOT NULL,
                       release_group_type  TEXT    NOT NULL,
                       artist_mbid         TEXT    NOT NULL,
                       artist_name         TEXT    NOT NULL,
                       artist_wikidata_id  TEXT
                   );
                   """)

    cursor.execute("""
                   CREATE TABLE text_canonical_lookup
                   (
                       clean_track         TEXT    NOT NULL,
                       clean_album         TEXT    NOT NULL,
                       clean_artist        TEXT    NOT NULL,
                       track_title         TEXT    NOT NULL,
                       duration_ms         INTEGER NOT NULL,
                       album_mbid          TEXT    NOT NULL,
                       album_title         TEXT    NOT NULL,
                       release_group_mbid  TEXT    NOT NULL,
                       release_group_title TEXT    NOT NULL,
                       release_group_type  TEXT    NOT NULL,
                       artist_mbid         TEXT    NOT NULL,
                       artist_name         TEXT    NOT NULL,
                       artist_wikidata_id  TEXT,
                       PRIMARY KEY (clean_track, clean_album, clean_artist)
                   );
                   """)
    conn.commit()
    conn.close()


def stream_tar_to_duckdb(con):
    for tar_name in TAR_FILES:
        if not os.path.exists(tar_name):
            log(f"⚠️ Archive {tar_name} missing. Skipping package...")
            continue

        log(f"📦 Unpacking and processing data archive: {tar_name}")
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                clean_name = member.name.lstrip("./").strip()

                if clean_name in TABLE_MAPPING:
                    target_table = TABLE_MAPPING[clean_name]
                    columns = TABLE_SCHEMAS[clean_name]

                    log(f"▶️ Spooling & Extracting Named Schema: {member.name} -> {target_table}")

                    with tar.extractfile(member) as source, open(TEMP_TSV, "wb") as target:
                        shutil.copyfileobj(source, target)

                    # 1. Load raw data cleanly as text
                    con.execute(f"""
                        CREATE TABLE temp_{target_table} AS 
                        SELECT * FROM read_csv('{TEMP_TSV}', 
                                               header=False, 
                                               delim='\t', 
                                               quote='', 
                                               escape='', 
                                               nullstr='\\N', 
                                               names={columns},
                                               all_varchar=True,
                                               null_padding=True,
                                               sample_size=-1);
                    """)

                    # 2. Re-create the table casting all relational ID columns to explicit INTEGERS
                    cast_exprs = []
                    for col in columns:
                        if col == "id" or col.endswith("_credit") or col in ["release", "medium", "artist", "entity0",
                                                                             "entity1"]:
                            cast_exprs.append(f"TRY_CAST({col} AS INTEGER) AS {col}")
                        else:
                            cast_exprs.append(col)

                    select_clause = ", ".join(cast_exprs)
                    con.execute(f"CREATE TABLE {target_table} AS SELECT {select_clause} FROM temp_{target_table};")
                    con.execute(f"DROP TABLE temp_{target_table};")

                    os.remove(TEMP_TSV)
                    log(f"   ✅ Memory-optimized load complete for {target_table}.")


def verify_extracted_counts(con):
    log("📋 Validating ingested DuckDB record volume against official global baselines...")

    # Official live production benchmarks (from musicbrainz.org/statistics)
    baselines = {
        "raw_artist": 2800000,
        "raw_release_group": 4300000,
        "raw_release": 5500000,
        "raw_medium": 6000000,
        "raw_url": 19000000,
        "raw_track": 55000000
    }
    # baselines = {
    #     "raw_artist": 10,
    #     "raw_release_group": 10,
    #     "raw_release": 10,
    #     "raw_medium": 10,
    #     "raw_url": 10,
    #     "raw_track": 10
    # }

    for table_name, expected_minimum in baselines.items():
        # Query total record allocations for the given table
        result = con.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()
        actual_count = result[0] if result else 0

        if actual_count >= expected_minimum:
            log(f"   ✅ {table_name}: Passed ({actual_count:,} records found, minimum is {expected_minimum:,})")
        else:
            raise AssertionError(
                f"Data Leakage Detected! Table '{table_name}' only contains {actual_count:,} rows. "
                f"Expected at least {expected_minimum:,} based on standard MusicBrainz distribution sizes."
            )


def execute_analytics_and_export(con):
    log("🔌 Applying performance pragmas via native SQLite driver...")
    sqlite_conn = sqlite3.connect(DB_NAME)
    sqlite_cursor = sqlite_conn.cursor()
    sqlite_cursor.execute("PRAGMA synchronous = OFF;")
    sqlite_cursor.execute("PRAGMA journal_mode = MEMORY;")
    sqlite_cursor.execute("PRAGMA mmap_size = 2147483648;")
    sqlite_conn.commit()
    sqlite_conn.close()

    log("🔌 Attaching optimized SQLite container to DuckDB environment...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB_NAME}' AS sqlite_db (TYPE SQLITE);")
    log("⚡ Indexing memory tables to accelerate relational queries...")
    con.execute("CREATE INDEX IF NOT EXISTS idx_raw_track_medium ON raw_track(medium);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_raw_medium_release ON raw_medium(release);")

    log("🔗 Processing Link Canonical Cache (Grouping Primary Keys)...")
    con.execute(BUILD_LINK_LOOKUP_SQL)

    log("🔗 Processing Text Canonical Cache (Grouping Composite Keys)...")
    con.execute(BUILD_TEXT_LOOKUP_SQL)

    con.execute("DETACH sqlite_db;")
    log("🎉 Relational consolidation processing complete.")


def optimize_final_sqlite():
    log("🗂️ Generating structured performance search indexes on SQLite production container...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_link_search ON link_canonical_lookup(streaming_link);")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_text_search ON text_canonical_lookup(clean_track, clean_album, clean_artist);")
    cursor.execute("VACUUM;")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    log("🚀 Starting decoupled, named-schema DuckDB generation engine...")
    init_target_sqlite()
    db_con = init_duckdb()

    try:
        stream_tar_to_duckdb(db_con)

        # Run count checks on ingested data before computing queries
        verify_extracted_counts(db_con)

        execute_analytics_and_export(db_con)
        optimize_final_sqlite()
        log("🎉 Asset compilation completed successfully!")
    except Exception as e:
        log(f"❌ Pipeline Failure: {str(e)}")
        sys.exit(1)
    finally:
        db_con.close()
        if os.path.exists(DUCKDB_TMP):
            os.remove(DUCKDB_TMP)
