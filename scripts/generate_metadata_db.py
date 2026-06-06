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
DUCKDB_TMP = "duckdb_working.db"
TEMP_TSV = "temp_extract.tsv"
OPTIMIZED_DB_TMP = "metadata_core.optimized.db"

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
    utc_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{utc_timestamp}] {message}")


def init_duckdb():
    log("Initializing DuckDB engine for GitHub Actions environment...")
    if os.path.exists(DUCKDB_TMP):
        log(f"Removing pre-existing DuckDB file: {DUCKDB_TMP}")
        os.remove(DUCKDB_TMP)
    if os.path.exists(".duckdb_tmp"):
        log("Removing pre-existing DuckDB cache directory...")
        shutil.rmtree(".duckdb_tmp", ignore_errors=True)

    con = duckdb.connect(DUCKDB_TMP)
    con.execute("SET max_memory='5GB';")
    con.execute("SET temp_directory='.duckdb_tmp';")
    con.execute("SET threads=2;")
    con.execute("SET preserve_insertion_order=false;")
    log("DuckDB memory and thread configurations successfully applied.")
    return con


def init_target_sqlite(db_name):
    log(f"Initializing target SQLite container: {db_name}")
    if os.path.exists(db_name):
        log(f"Removing old version of {db_name}...")
        os.remove(db_name)

    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

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
                log(f"Notice: Table '{table_name}' absent from test environment layout.")
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

    log("Attaching destination SQLite asset database connection...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{db_name}' AS sqlite_db (TYPE SQLITE);")

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
    con.execute("DROP TABLE final_canonical_metadata;")
    con.execute("DROP TABLE winning_link_tracks;")
    con.execute("DROP TABLE winning_text_tracks;")


def optimize_final_sqlite():
    if not os.path.exists(DB_NAME):
        return
    log("Vacuuming and optimizing SQLite deployment configuration safely...")

    if os.path.exists(OPTIMIZED_DB_TMP):
        os.remove(OPTIMIZED_DB_TMP)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode = OFF;")
    cursor.execute("PRAGMA page_size = 4096;")

    log(f"Cloned optimization stream running: {DB_NAME} -> {OPTIMIZED_DB_TMP}")
    vacuum_start = time.time()
    cursor.execute(f"VACUUM INTO '{OPTIMIZED_DB_TMP}';")
    conn.close()
    log(f"SQLite safe vacuum task finished in {time.time() - vacuum_start:.2f}s.")

    os.remove(DB_NAME)
    os.rename(OPTIMIZED_DB_TMP, DB_NAME)
    log("Safe vacuum replacement complete. Swapped workspace with production asset.")


if __name__ == "__main__":
    log("Starting decoupled, named-schema DuckDB generation engine...")
    init_target_sqlite(DB_NAME)
    db_con = init_duckdb()

    try:
        stream_tar_to_duckdb(db_con)
        verify_extracted_counts(db_con)
        execute_analytics_and_export(db_con, DB_NAME)
        optimize_final_sqlite()
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

        if os.path.exists(DUCKDB_TMP):
            os.remove(DUCKDB_TMP)
        if os.path.exists(TEMP_TSV):
            os.remove(TEMP_TSV)
        if os.path.exists(OPTIMIZED_DB_TMP):
            os.remove(OPTIMIZED_DB_TMP)
        if os.path.exists(".duckdb_tmp"):
            shutil.rmtree(".duckdb_tmp", ignore_errors=True)

        log(f"Workspace scratch files cleared completely. Exit procedures finalized at total runtime offset: {time.time() - START_TIME:.2f}s.")
