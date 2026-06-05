import duckdb
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime

from queries import (
    PRECOMPUTE_FILTERS_SQL,
    BUILD_LINK_LOOKUP_SQL,
    MATERIALIZE_CLEAN_STRINGS_SQL,
    BUILD_TEXT_LOOKUP_SQL
)
from schema import TABLE_SCHEMAS, TABLE_MAPPING

DB_NAME = "metadata_core.db"
DUCKDB_TMP = "duckdb_working.db"
TEMP_TSV = "temp_extract.tsv"

if os.environ.get("GITHUB_ACTIONS") == "true" or "--prod" in sys.argv:
    TAR_FILES = ["mbdump.tar.bz2"]
elif os.path.exists("test_mbdump.tar.bz2"):
    TAR_FILES = ["test_mbdump.tar.bz2"]
else:
    TAR_FILES = ["mbdump.tar.bz2"]


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

    # Maximize write speed during sequential insertions
    cursor.execute("PRAGMA journal_mode = OFF;")
    cursor.execute("PRAGMA synchronous = 0;")
    cursor.execute("PRAGMA cache_size = 100000;")

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
    if not TAR_FILES:
        raise FileNotFoundError("Critical Error! No .tar.bz2 data archives found in the workspace.")

    for tar_name in TAR_FILES:
        log(f"📦 Unpacking and processing data archive: {tar_name}")
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                clean_name = member.name.lstrip("./").strip()

                if clean_name in TABLE_MAPPING:
                    target_table = TABLE_MAPPING[clean_name]
                    columns = TABLE_SCHEMAS[clean_name]

                    log(f"▶️ Spooling & Extracting: {member.name} -> {target_table}")

                    with tar.extractfile(member) as source, open(TEMP_TSV, "wb") as target:
                        shutil.copyfileobj(source, target)

                    types_dict = {}
                    for col in columns:
                        if col == "id" or col.endswith("_credit") or col in ["release", "medium", "artist", "entity0",
                                                                             "entity1", "recording"]:
                            types_dict[col] = "INTEGER"
                        else:
                            types_dict[col] = "VARCHAR"

                    types_sql = "{" + ", ".join([f"'{k}': '{v}'" for k, v in types_dict.items()]) + "}"

                    con.execute(f"""
                        CREATE TABLE {target_table} AS 
                        SELECT * FROM read_csv('{TEMP_TSV}', 
                                               header=False, 
                                               delim='\t', 
                                               quote='', 
                                               escape='', 
                                               nullstr='\\N', 
                                               names={columns},
                                               types={types_sql},
                                               null_padding=True,
                                               strict_mode=False);
                    """)

                    os.remove(TEMP_TSV)
                    log(f"   ✅ Memory-optimized load complete for {target_table}.")


def verify_extracted_counts(con):
    log("📋 Validating ingested DuckDB record volume...")
    is_test_env = any("test" in f for f in TAR_FILES)

    baselines = {
        "raw_artist": 10 if is_test_env else 2800000,
        "raw_track": 10 if is_test_env else 55000000,
        "raw_recording": 10 if is_test_env else 35000000,
    }

    for table_name, expected_minimum in baselines.items():
        exists = con.execute(f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}';").fetchone()
        if not exists and not is_test_env:
            raise AssertionError(f"Critical Error! Table '{table_name}' was not processed.")

        if exists:
            actual_count = con.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()[0]
            if actual_count < expected_minimum:
                raise AssertionError(f"Leakage! {table_name} has {actual_count:,} rows. Expected {expected_minimum:,}.")
            log(f"   ✅ {table_name}: Passed ({actual_count:,} records)")


def execute_analytics_and_export(con):
    log("⚡ Precomputing filter tables to shrink massive joins...")
    for query in PRECOMPUTE_FILTERS_SQL:
        con.execute(query)

    log("🔗 Processing Link Canonical Cache natively in DuckDB...")
    con.execute(BUILD_LINK_LOOKUP_SQL)

    log("🧹 Materializing Regex-cleaned string vectors...")
    for query in MATERIALIZE_CLEAN_STRINGS_SQL:
        con.execute(query)

    log("🔗 Processing Text Canonical Cache via sequential vector join...")
    con.execute(BUILD_TEXT_LOOKUP_SQL)

    # CRITICAL SPACE CLEANUP: Evict raw source data from memory/disk before generating SQLite asset
    log("🧹 Evicting raw staging tables to reclaim workspace disk space...")
    raw_tables = [
        "raw_track", "raw_recording", "raw_medium", "raw_release", "raw_release_group",
        "raw_artist", "raw_artist_credit_name", "raw_url", "raw_l_recording_url",
        "raw_l_artist_url", "raw_l_release_url", "raw_rg_type", "clean_tracks",
        "clean_releases", "clean_artists", "target_urls", "streaming_links", "wikidata_mapping"
    ]
    for table in raw_tables:
        con.execute(f"DROP TABLE IF EXISTS {table};")

    log("🔌 Attaching targeted SQLite container to DuckDB environment...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB_NAME}' AS sqlite_db (TYPE SQLITE);")

    log("🚚 Streaming finalized structures directly to SQLite container...")
    con.execute("INSERT INTO sqlite_db.link_canonical_lookup SELECT * FROM duck_link_lookup;")
    con.execute("INSERT INTO sqlite_db.text_canonical_lookup SELECT * FROM duck_text_lookup;")

    con.execute("DETACH sqlite_db;")
    log("🎉 Relational consolidation processing complete.")


def optimize_final_sqlite():
    log("🗂️ Generating structured performance search indexes on SQLite...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_link_search ON link_canonical_lookup(streaming_link);")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_text_search ON text_canonical_lookup(clean_track, clean_album, clean_artist);")

    # We omit VACUUM to prevent duplicate disk consumption on the free-tier runner
    cursor.execute("PRAGMA journal_mode = DELETE;")
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
