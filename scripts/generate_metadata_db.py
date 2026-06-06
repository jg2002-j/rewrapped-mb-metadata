import duckdb
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from datetime import datetime

from queries import BUILD_NORMALIZED_PIPELINE_SQL
from schema import TABLE_SCHEMAS, TABLE_MAPPING

DB_NAME = "metadata_core.db"
TEMP_TSV = "temp_extract.tsv"

START_TIME = time.time()

def get_tar_path():
    is_prod = os.environ.get("GITHUB_ACTIONS") == "true" or "--prod" in sys.argv
    prod_files = ["mbdump.tar.bz2"]
    test_files = ["test_mbdump.tar.bz2"]
    if is_prod:
        return prod_files
    if os.path.exists("test_mbdump.tar.bz2"):
        return test_files
    return prod_files

TAR_FILES = get_tar_path()

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def init_duckdb():
    log("Initializing DuckDB engine for GitHub Actions environment...")
    if os.path.exists(".duckdb_tmp"):
        log("Removing pre-existing DuckDB cache directory...")
        shutil.rmtree(".duckdb_tmp", ignore_errors=True)

    # Use an in-memory database. It will spill to disk (.duckdb_tmp) ONLY when RAM is full,
    # avoiding a massive persistent .db file that never shrinks.
    con = duckdb.connect(':memory:')
    con.execute("SET max_memory='5GB';")
    con.execute("SET temp_directory='.duckdb_tmp';")
    con.execute("SET threads=2;")
    con.execute("SET preserve_insertion_order=false;")
    con.execute("SET enable_progress_bar=true;")

    log("DuckDB memory and thread configurations successfully applied.")
    return con

def init_target_sqlite(db_name):
    log(f"Initializing target SQLite container: {db_name}")
    if os.path.exists(db_name):
        os.remove(db_name)

    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    # Set page size before creating tables so the DB is born optimized
    cursor.execute("PRAGMA page_size = 4096;")

    log("Creating canonical_metadata structural table...")
    cursor.execute("""
                   CREATE TABLE canonical_metadata
                   (
                       id                  INTEGER PRIMARY KEY AUTOINCREMENT,
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

    log("Creating link_lookup structural table...")
    cursor.execute("""
                   CREATE TABLE link_lookup
                   (
                       streaming_link TEXT PRIMARY KEY,
                       metadata_id    INTEGER NOT NULL,
                       FOREIGN KEY (metadata_id) REFERENCES canonical_metadata (id)
                   );
                   """)

    log("Creating text_lookup structural table...")
    cursor.execute("""
                   CREATE TABLE text_lookup
                   (
                       clean_track  TEXT    NOT NULL,
                       clean_album  TEXT    NOT NULL,
                       clean_artist TEXT    NOT NULL,
                       duration_ms  INTEGER NOT NULL,
                       metadata_id  INTEGER NOT NULL,
                       PRIMARY KEY (clean_track, clean_album, clean_artist, duration_ms),
                       FOREIGN KEY (metadata_id) REFERENCES canonical_metadata (id)
                   );
                   """)

    conn.commit()
    conn.close()
    log("Target SQLite schema tables initialized completely.")

def stream_tar_to_duckdb(con):
    for tar_name in TAR_FILES:
        if not os.path.exists(tar_name):
            log(f"Warning: Archive {tar_name} is missing. Skipping package processing...")
            continue

        log(f"Opening data archive stream: {tar_name}")
        file_count = 0
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                clean_name = member.name.lstrip("./").strip()

                if clean_name in TABLE_MAPPING:
                    file_count += 1
                    target_table = TABLE_MAPPING[clean_name]
                    columns = TABLE_SCHEMAS[clean_name]

                    log(f"Spooling and Extracting Named Schema [{file_count}]: {member.name} -> {target_table}")

                    extract_start = time.time()
                    with tar.extractfile(member) as source, open(TEMP_TSV, "wb") as target:
                        shutil.copyfileobj(source, target)
                    log(f"Disk write complete for temp TSV in {time.time() - extract_start:.2f}s. Streaming to DuckDB...")

                    duckdb_load_start = time.time()

                    # Single pass ingestion + cast to prevent double disk utilization
                    cast_exprs = []
                    for col in columns:
                        if col == "id" or col.endswith("_credit") or col in ["release", "medium", "artist", "entity0", "entity1", "recording"]:
                            cast_exprs.append(f"TRY_CAST({col} AS INTEGER) AS {col}")
                        else:
                            cast_exprs.append(col)

                    select_clause = ", ".join(cast_exprs)

                    con.execute(f"""
                        CREATE TABLE {target_table} AS 
                        SELECT {select_clause} 
                        FROM read_csv('{TEMP_TSV}', 
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

                    if os.path.exists(TEMP_TSV):
                        os.remove(TEMP_TSV)

                    log(f"Memory-optimized ingestion complete for {target_table} in {time.time() - duckdb_load_start:.2f}s.")

def verify_extracted_counts(con):
    log("Validating ingested DuckDB record volume against official global baselines...")
    is_test_env = "test_mbdump.tar.bz2" in TAR_FILES

    baselines = {
        "raw_artist": 10 if is_test_env else 2800000,
        "raw_release_group": 10 if is_test_env else 4300000,
        "raw_release": 10 if is_test_env else 5500000,
        "raw_medium": 10 if is_test_env else 6000000,
        "raw_url": 10 if is_test_env else 19000000,
        "raw_track": 10 if is_test_env else 55000000,
        "raw_recording": 10 if is_test_env else 35000000,
    }

    for table_name, expected_minimum in baselines.items():
        table_exists = con.execute(
            f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}';").fetchone()
        if not table_exists:
            if is_test_env:
                continue
            else:
                raise AssertionError(f"Critical Error: Production table '{table_name}' was not processed.")

        result = con.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()
        actual_count = result[0] if result else 0

        if actual_count >= expected_minimum:
            log(f"   Validation Verified -> {table_name}: {actual_count:,} records found.")
        else:
            raise AssertionError(f"Data Leakage Detected: Table '{table_name}' only contains {actual_count:,} rows.")

def execute_analytics_and_export(con, db_name):
    log("Processing and ranking raw tracks with structural hierarchy rules (Running main SQL analytical query)...")
    analytics_start = time.time()
    con.execute(BUILD_NORMALIZED_PIPELINE_SQL)
    log(f"Finished relational grouping logic steps in {time.time() - analytics_start:.2f}s.")

    log("Dropping raw DuckDB tables to aggressively free up disk space before SQLite export...")
    for target_table in TABLE_MAPPING.values():
        con.execute(f"DROP TABLE IF EXISTS {target_table};")

    log("Attaching destination SQLite asset database connection...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{db_name}' AS sqlite_db (TYPE SQLITE);")

    # Disable journaling and synchronization locally to prevent SQLite temp disk explosion
    con.execute("PRAGMA sqlite_db.journal_mode = OFF;")
    con.execute("PRAGMA sqlite_db.synchronous = OFF;")

    log("Migrating deduplicated metadata entities to SQLite...")
    migration_start = time.time()
    con.execute("""
                INSERT INTO sqlite_db.canonical_metadata (id, track_title, duration_ms, recording_mbid, album_mbid,
                                                          album_title,
                                                          release_group_mbid, release_group_title, release_group_type,
                                                          artist_mbid, artist_name, artist_wikidata_id)
                SELECT metadata_id,
                       track_title,
                       duration_ms,
                       recording_mbid,
                       album_mbid,
                       album_title,
                       release_group_mbid,
                       release_group_title,
                       release_group_type,
                       artist_mbid,
                       artist_name,
                       artist_wikidata_id
                FROM final_canonical_metadata;
                """)
    log(f"Finished copying canonical_metadata rows in {time.time() - migration_start:.2f}s.")

    log("Mapping link lookup indices to SQLite...")
    link_start = time.time()
    con.execute("""
                INSERT INTO sqlite_db.link_lookup (streaming_link, metadata_id)
                SELECT wlt.streaming_link, fcm.metadata_id
                FROM winning_link_tracks wlt
                         JOIN final_canonical_metadata fcm ON wlt.track_id = fcm.track_id;
                """)
    log(f"Finished copying link_lookup entries in {time.time() - link_start:.2f}s.")

    log("Mapping text lookup entries with unique duration tracking to SQLite...")
    text_start = time.time()
    con.execute("""
                INSERT INTO sqlite_db.text_lookup (clean_track, clean_album, clean_artist, duration_ms, metadata_id)
                SELECT wtt.clean_track, wtt.clean_album, wtt.clean_artist, wtt.duration_ms, fcm.metadata_id
                FROM winning_text_tracks wtt
                         JOIN final_canonical_metadata fcm ON wtt.track_id = fcm.track_id;
                """)
    log(f"Finished copying text_lookup entries in {time.time() - text_start:.2f}s.")

    con.execute("DETACH sqlite_db;")
    log("Dropping temporary DuckDB scratchpads to free RAM/disk...")
    con.execute("DROP TABLE IF EXISTS final_canonical_metadata;")
    con.execute("DROP TABLE IF EXISTS winning_link_tracks;")
    con.execute("DROP TABLE IF EXISTS winning_text_tracks;")

if __name__ == "__main__":
    log("Starting decoupled, named-schema DuckDB generation engine...")
    init_target_sqlite(DB_NAME)
    db_con = init_duckdb()

    try:
        stream_tar_to_duckdb(db_con)
        verify_extracted_counts(db_con)
        execute_analytics_and_export(db_con, DB_NAME)
        log(f"Asset compilation completed successfully! Total runtime: {time.time() - START_TIME:.2f} seconds.")
    except Exception as e:
        log(f"Pipeline Failure Exception Encountered: {str(e)}")
        if os.path.exists(DB_NAME):
            log(f"Deleting broken target database artifact {DB_NAME} to prevent corruption delivery...")
            os.remove(DB_NAME)
        sys.exit(1)
    finally:
        log("Cleaning up runner workspace scratch environments completely...")
        try:
            db_con.close()
        except Exception:
            pass

        if os.path.exists(TEMP_TSV):
            os.remove(TEMP_TSV)
        if os.path.exists(".duckdb_tmp"):
            shutil.rmtree(".duckdb_tmp", ignore_errors=True)

        log("Workspace scratch files cleared completely. Exit procedures finalized.")