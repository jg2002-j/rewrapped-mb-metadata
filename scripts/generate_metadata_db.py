import duckdb
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime

from queries import BUILD_LINK_LOOKUP_SQL, CREATE_TEXT_HOLDING_TABLE_SQL, GET_CHUNKED_TEXT_LOOKUP_SQL, GET_SQLITE_EXPORT_CHUNK_SQL
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
    if os.path.exists(".duckdb_tmp"):
        shutil.rmtree(".duckdb_tmp", ignore_errors=True)

    con = duckdb.connect(DUCKDB_TMP)

    # Strictly optimized for 7GB RAM GitHub Runner limits
    con.execute("SET max_memory='5GB';")
    con.execute("SET temp_directory='.duckdb_tmp';")
    con.execute("SET threads=2;")
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
                       recording_mbid      TEXT    NOT NULL,
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
                       recording_mbid      TEXT    NOT NULL,
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

                    cast_exprs = []
                    for col in columns:
                        if col == "id" or col.endswith("_credit") or col in ["release", "medium", "artist", "entity0",
                                                                             "entity1", "recording"]:
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

    is_test_env = "test_mbdump.tar.bz2" in TAR_FILES

    baselines = {
        "raw_artist": 10 if is_test_env else 2800000,
        "raw_release_group": 10 if is_test_env else 4300000,
        "raw_release": 10 if is_test_env else 5500000,
        "raw_medium": 10 if is_test_env else 6000000,
        "raw_url": 10 if is_test_env else 19000000,
        "raw_track": 10 if is_test_env else 55000000,
        "raw_recording": 10 if is_test_env else 35000000,
        "raw_l_recording_url": 5 if is_test_env else 5000000
    }

    for table_name, expected_minimum in baselines.items():
        table_exists = con.execute(f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}';").fetchone()
        if not table_exists:
            if is_test_env:
                log(f"   ⚠️ {table_name}: Missing from test archive environment (Skipped safety count check)")
                continue
            else:
                raise AssertionError(f"Critical Error! Production table '{table_name}' was not processed.")

        result = con.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()
        actual_count = result[0] if result else 0

        if actual_count >= expected_minimum:
            log(f"   ✅ {table_name}: Passed ({actual_count:,} records found, minimum is {expected_minimum:,})")
        else:
            raise AssertionError(
                f"Data Leakage Detected! Table '{table_name}' only contains {actual_count:,} rows. "
                f"Expected at least {expected_minimum:,}."
            )


def execute_analytics_and_export(con):
    required_tables = ["raw_track", "raw_recording", "raw_medium", "raw_release", "raw_release_group", "raw_l_recording_url"]
    for t in required_tables:
        exists = con.execute(f"SELECT 1 FROM information_schema.tables WHERE table_name = '{t}';").fetchone()
        if not exists:
            log(f"❌ Aborting pipeline export processing step: Table '{t}' is completely missing.")
            return

    log("⚡ Indexing memory tables to accelerate relational queries...")
    con.execute("CREATE INDEX IF NOT EXISTS idx_duck_track_id ON raw_track(id);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_duck_track_recording ON raw_track(recording);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_duck_track_medium ON raw_track(medium);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_duck_medium_release ON raw_medium(release);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_duck_release_rg ON raw_release(release_group);")

    log("🔗 Processing Link Canonical Cache (Grouping Primary Keys natively in DuckDB)...")
    con.execute(BUILD_LINK_LOOKUP_SQL)

    log("🔗 Processing Text Canonical Cache sequentially via relational slicing...")
    con.execute(CREATE_TEXT_HOLDING_TABLE_SQL)

    max_track_res = con.execute("SELECT MAX(id) FROM raw_track;").fetchone()
    max_track_id = max_track_res[0] if max_track_res and max_track_res[0] else 60000000

    chunk_step = 5000000
    for offset in range(0, max_track_id + 1, chunk_step):
        log(f"   ↳ Aggregating tracks slice natively: {offset:,} -> {offset + chunk_step:,}")
        chunk_sql = GET_CHUNKED_TEXT_LOOKUP_SQL(offset, offset + chunk_step)
        con.execute(chunk_sql)

    log("🔌 Attaching targeted SQLite container directly to DuckDB environment...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB_NAME}' AS sqlite_db (TYPE SQLITE);")

    log("🚚 Streaming finalized link structures out to SQLite container...")
    con.execute("INSERT INTO sqlite_db.link_canonical_lookup SELECT * FROM duck_link_lookup;")

    log("🚚 Streaming text cache blocks out to SQLite container using partitioned blocks...")

    # Slice using alphabetical string segments to limit concurrent memory usage during the final GROUP BY
    export_partitions = [
        ("a-c", "clean_artist >= 'a' AND clean_artist < 'd'"),
        ("d-f", "clean_artist >= 'd' AND clean_artist < 'g'"),
        ("g-i", "clean_artist >= 'g' AND clean_artist < 'j'"),
        ("j-l", "clean_artist >= 'j' AND clean_artist < 'm'"),
        ("m-o", "clean_artist >= 'm' AND clean_artist < 'p'"),
        ("p-r", "clean_artist >= 'p' AND clean_artist < 's'"),
        ("s-u", "clean_artist >= 's' AND clean_artist < 'v'"),
        ("v-z+", "clean_artist >= 'v' OR clean_artist < 'a' OR clean_artist IS NULL")
    ]

    for label, criteria in export_partitions:
        log(f"   ↳ Streaming text partition block: [{label}]")
        partition_sql = GET_SQLITE_EXPORT_CHUNK_SQL(criteria)
        con.execute(partition_sql)

    con.execute("DETACH sqlite_db;")
    log("🎉 Relational consolidation processing complete.")


def optimize_final_sqlite():
    if not os.path.exists(DB_NAME):
        return
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
        if os.path.exists(".duckdb_tmp"):
            shutil.rmtree(".duckdb_tmp", ignore_errors=True)