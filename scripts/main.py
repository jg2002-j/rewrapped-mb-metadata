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

# Anchor code assets (transformations.sql) to this file's directory so the
# pipeline works regardless of the current working directory (e.g. when the
# workflow runs `python scripts/main.py` from the repo root).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The output database is written to the CWD so the CI "Compress Output Database"
# step (which runs from the repo root) finds it by the same name.
TARGET_SQLITE_NAME = "mb_metadata.db"


def main():
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("INITIATING MUSICBRAINZ ANALYTICS PIPELINE ENGINE")
    logger.info("=" * 60)

    cleanup_temp_files()
    target_sqlite_path = TARGET_SQLITE_NAME

    # On-disk DuckDB engine file (it spills to disk under the configured limits).
    logger.info("Initializing DuckDB storage engine runtime connection...")
    con = duckdb.connect("engine_runtime.duckdb")

    try:
        logger.info("Configuring engine hardware allocation boundaries...")
        con.execute("SET memory_limit='5.5GB';")
        con.execute("SET max_temp_directory_size='10GB';")
        con.execute("SET temp_directory='duckdb_spill_buffer';")
        # Lower peak memory on large aggregations/sorts; we never rely on row order.
        con.execute("SET preserve_insertion_order=false;")
        con.execute(f"SET threads={max(1, os.cpu_count() or 2)};")

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
        transform_script_path = os.path.join(BASE_DIR, "transformations.sql")
        if not os.path.exists(transform_script_path):
            raise FileNotFoundError(f"Missing required execution asset: {transform_script_path}")

        logger.info(f"Reading target analytical workflow queries from {transform_script_path}...")
        with open(transform_script_path, "r", encoding="utf-8") as f:
            sql_script = f.read()

        logger.info("Executing pure hash pipelines and phased table drop reductions...")
        tx_start = time.time()
        con.execute(sql_script)
        logger.info(f"Target insertions and normalization mappings finalized in {time.time() - tx_start:.2f}s")

        # Step 4: Detach, release the engine's memory, then build indices natively
        con.execute("DETACH target_sqlite;")
        con.close()  # free DuckDB's ~5.5GB before the native SQLite index build
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
        try:
            con.close()  # idempotent; safe even if already closed above
        except Exception:
            pass
        cleanup_temp_files()


if __name__ == "__main__":
    main()
