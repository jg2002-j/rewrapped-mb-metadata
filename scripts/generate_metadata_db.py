import duckdb
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from datetime import datetime

from schema import TABLE_SCHEMAS, TABLE_MAPPING, NEEDED_COLUMNS

# Configuration Constants
SQLITE_DB = "metadata_core.db"
TEMP_TSV = "workspace_scratch_stream.tsv"


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


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def init_sqlite_container():
    log(f"Initializing target SQLite container: {SQLITE_DB}")
    if os.path.exists(SQLITE_DB):
        os.remove(SQLITE_DB)

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()

    log("Creating canonical_metadata structural table...")
    cursor.execute("""
                   CREATE TABLE canonical_metadata
                   (
                       track_id           INTEGER PRIMARY KEY,
                       track_gid          TEXT,
                       track_name         TEXT,
                       track_length       INTEGER,
                       medium_position    INTEGER,
                       release_id         INTEGER,
                       release_gid        TEXT,
                       release_name       TEXT,
                       release_group_id   INTEGER,
                       release_group_gid  TEXT,
                       release_group_name TEXT,
                       release_group_type TEXT,
                       artist_id          INTEGER,
                       artist_gid         TEXT,
                       artist_name        TEXT
                   );
                   """)

    log("Creating link_lookup structural table...")
    cursor.execute("""
                   CREATE TABLE link_lookup
                   (
                       entity_id   INTEGER,
                       entity_type TEXT,
                       url_string  TEXT,
                       PRIMARY KEY (entity_id, entity_type, url_string)
                   );
                   """)

    log("Creating text_lookup structural table...")
    cursor.execute("""
                   CREATE TABLE text_lookup
                   (
                       lookup_key   TEXT PRIMARY KEY,
                       lookup_value TEXT
                   );
                   """)

    conn.commit()
    conn.close()
    log("Target SQLite schema tables initialized completely.")


def init_duckdb():
    log("Initializing DuckDB engine for GitHub Actions environment...")
    if os.path.exists(".duckdb_tmp"):
        log("Removing pre-existing DuckDB cache directory...")
        shutil.rmtree(".duckdb_tmp", ignore_errors=True)

    # In-memory execution avoids hitting GitHub Actions disk boundaries entirely
    con = duckdb.connect(':memory:')
    con.execute("SET max_memory='6GB';")
    con.execute("SET temp_directory='.duckdb_tmp';")
    con.execute("SET threads=2;")
    con.execute("SET preserve_insertion_order=false;")

    log("DuckDB memory and thread configurations successfully applied.")
    return con


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

                # OPTIMIZATION 1: Bypass completely unused tables inside archive
                if clean_name == "mbdump/l_release_url":
                    continue

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

                    # OPTIMIZATION 2: Column Projection Pushdown Expression Generator
                    cast_exprs = []
                    for col in columns:
                        if col in NEEDED_COLUMNS.get(target_table, []):
                            if col in ["id", "artist_credit", "release", "medium", "artist", "entity0", "entity1",
                                       "recording", "position", "length"]:
                                cast_exprs.append(f"TRY_CAST({col} AS INTEGER) AS {col}")
                            else:
                                cast_exprs.append(col)

                    select_clause = ", ".join(cast_exprs)

                    # OPTIMIZATION 3: Inline Predicate Filter Pushdown for URL mapping
                    where_clause = ""
                    if target_table == "raw_url":
                        where_clause = """
                            WHERE url LIKE '%wikidata.org/wiki/Q%' 
                               OR url LIKE '%spotify.com%' 
                               OR url LIKE '%music.apple.com%' 
                               OR url LIKE '%tidal.com%'
                        """

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
                                       sample_size=-1)
                        {where_clause};
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


def execute_analytics_and_export(con):
    log("Processing and ranking raw tracks with structural hierarchy rules (Running main SQL analytical query)...")
    analytics_start = time.time()

    con.execute("""
        CREATE TEMPORARY TABLE processed_metadata_layer AS 
        SELECT 
            t.id AS track_id,
            r.gid AS track_gid,
            t.name AS track_name,
            t.length AS track_length,
            t.medium AS medium_position,
            rel.id AS release_id,
            rel.gid AS release_gid,
            rel.name AS release_name,
            rg.id AS release_group_id,
            rg.gid AS release_group_gid,
            rg.name AS release_group_name,
            typ.name AS release_group_type,
            art.id AS artist_id,
            art.gid AS artist_gid,
            art.name AS artist_name
        FROM raw_track t
        JOIN raw_recording r ON t.recording = r.id
        JOIN raw_medium m ON t.medium = m.id
        JOIN raw_release rel ON m.release = rel.id
        JOIN raw_release_group rg ON rel.release_group = rg.id
        LEFT JOIN raw_rg_type typ ON rg.type = typ.id
        JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit AND acn.position = 0
        JOIN raw_artist art ON acn.artist = art.id;
    """)

    log(f"Finished relational grouping logic steps in {time.time() - analytics_start:.2f}s.")

    log("Attaching destination SQLite asset database connection...")
    con.execute(f"LOAD sqlite; ATTACH '{SQLITE_DB}' AS sqlite_db (TYPE SQLITE);")

    log("Migrating deduplicated metadata entities to SQLite...")
    migration_start = time.time()

    # 1. Populate the main canonical tracks
    con.execute("INSERT INTO sqlite_db.canonical_metadata SELECT * FROM processed_metadata_layer;")

    # 2. Populate artist links cleanly without using compound UNION arrays or conflicting modifiers
    con.execute("""
                INSERT INTO sqlite_db.link_lookup
                SELECT DISTINCT link.entity0 AS entity_id, 'artist' AS entity_type, u.url AS url_string
                FROM raw_l_artist_url link
                         JOIN raw_url u ON link.entity1 = u.id;
                """)

    # 3. Populate recording links cleanly without using compound UNION arrays or conflicting modifiers
    con.execute("""
                INSERT INTO sqlite_db.link_lookup
                SELECT DISTINCT link.entity0 AS entity_id, 'recording' AS entity_type, u.url AS url_string
                FROM raw_l_recording_url link
                         JOIN raw_url u ON link.entity1 = u.id;
                """)

    con.execute("DETACH sqlite_db;")
    log(f"Target relational sync verified and committed to SQLite file in {time.time() - migration_start:.2f}s.")


def main():
    pipeline_start = time.time()
    try:
        init_sqlite_container()
        con = init_duckdb()
        stream_tar_to_duckdb(con)
        verify_extracted_counts(con)
        execute_analytics_and_export(con)

        # Cleanup routine for a clean runner exit status
        if os.path.exists(TEMP_TSV):
            os.remove(TEMP_TSV)
        shutil.rmtree(".duckdb_tmp", ignore_errors=True)
        log(f"Workspace scratch files cleared completely. Exit procedures finalized at total runtime offset: {time.time() - pipeline_start:.2f}s.")

    except Exception as e:
        log(f"Pipeline Failure Exception Encountered: {str(e)}")
        if os.path.exists(SQLITE_DB):
            log(f"Deleting broken target database artifact {SQLITE_DB} to prevent corruption delivery...")
            os.remove(SQLITE_DB)
        log("Cleaning up runner workspace scratch environments completely...")
        if os.path.exists(TEMP_TSV):
            os.remove(TEMP_TSV)
        shutil.rmtree(".duckdb_tmp", ignore_errors=True)
        log("Workspace scratch files cleared completely. Exit procedures finalized.")
        sys.exit(1)


if __name__ == "__main__":
    main()
