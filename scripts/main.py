import os
import sys
import time
import logging
import duckdb
from utils import stream_and_load_musicbrainz, cleanup_temp_files
from schema import initialize_native_sqlite_schema, apply_optimized_indexes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("pipeline_engine")

def main():
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("INITIATING MUSICBRAINZ ANALYTICS PIPELINE ENGINE")
    logger.info("=" * 60)

    cleanup_temp_files()
    target_sqlite_path = "mb_metadata.db"

    # Initialize the high-performance in-memory DuckDB instance
    logger.info("Initializing DuckDB storage engine runtime connection...")
    con = duckdb.connect("engine_runtime.duckdb")

    try:
        logger.info("Configuring engine hardware allocation boundaries...")
        con.execute("SET memory_limit='5.5GB';")
        con.execute("SET max_temp_directory_size='10GB';")
        con.execute("SET temp_directory='duckdb_spill_buffer';")

        con.execute("INSTALL sqlite;")
        con.execute("LOAD sqlite;")

        # Step 1: Initialize real SQLite database via native sqlite3 library calls
        if os.path.exists(target_sqlite_path):
            os.remove(target_sqlite_path)
        initialize_native_sqlite_schema(target_sqlite_path)

        # Attach the genuine SQLite database to our main streaming engine context
        con.execute(f"ATTACH '{target_sqlite_path}' AS target_sqlite (TYPE SQLITE);")

        # Step 2: Stream, extract, and load raw musicbrainz data tables
        stream_and_load_musicbrainz(con)

        # Step 3: Run transformation workflows
        transform_script_path = "transformations.sql"
        if not os.path.exists(transform_script_path):
            raise FileNotFoundError(f"Missing required execution asset: {transform_script_path}")

        logger.info(f"Reading target analytical workflow queries from {transform_script_path}...")
        with open(transform_script_path, "r", encoding="utf-8") as f:
            sql_script = f.read()

        logger.info("Executing pure hash pipelines and phased table drop reductions...")
        tx_start = time.time()
        con.execute(sql_script)
        logger.info(f"Target insertions and normalization mappings finalized in {time.time() - tx_start:.2f}s")

        # Step 4: Detach to free up file handles and build indices natively
        con.execute("DETACH target_sqlite;")
        apply_optimized_indexes(target_sqlite_path)

        logger.info("=" * 60)
        logger.info(f"SUCCESS: Pipeline processing finalized in {time.time() - start_time:.2f}s")
        logger.info("=" * 60)

    except Exception as e:
        logger.critical("=" * 60)
        logger.critical(f"PIPELINE CRASHED DIAGNOSTIC DUMP: {str(e)}")
        logger.critical("=" * 60)
        raise e
    finally:
        con.close()
        cleanup_temp_files()

if __name__ == "__main__":
    main()