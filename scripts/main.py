import duckdb
import os
import sys
import logging
import time

from schema import initialize_bare_sqlite_schema, apply_optimized_indexes
from utils import get_tar_paths, extract_and_stream_to_duckdb, cleanup_temp_files, TABLE_MAPPING

# Configure global logging matrix with high-resolution timestamps
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("pipeline_engine")

def main():
    start_time = time.time()
    logger.info("==================================================")
    logger.info("INITIATING MUSICBRAINZ ANALYTICS PIPELINE ENGINE")
    logger.info("==================================================")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sql_path = os.path.join(script_dir, "transformations.sql")
    target_sqlite_name = "metadata.db"

    # Reset working space environment
    if os.path.exists(target_sqlite_name):
        logger.info(f"Stale database artifact found. Removing: {target_sqlite_name}")
        os.remove(target_sqlite_name)

    cleanup_temp_files()

    logger.info("Initializing DuckDB storage engine runtime connection...")
    con = duckdb.connect('engine_runtime.duckdb')

    try:
        logger.info("Configuring engine hardware allocation boundaries...")
        con.execute("PRAGMA memory_limit='4GB'")
        con.execute("PRAGMA temp_directory='duckdb_spill_buffer'")

        # This forces DuckDB to output progress indications to stdout during long operations
        con.execute("PRAGMA enable_progress_bar=true;")

        # Unpack raw entities through imported utilities
        archives = get_tar_paths()
        for archive in archives:
            if not os.path.exists(archive):
                logger.critical(f"CRITICAL EXECUTION BARRIER: File target missing -> {archive}")
                sys.exit(1)

            logger.info(f"Targeting source archive package: {archive}")
            for idx, table in enumerate(TABLE_MAPPING.keys(), 1):
                logger.info(f"[{idx}/{len(TABLE_MAPPING)}] Processing extraction pipeline for entity type: '{table}'")
                extract_and_stream_to_duckdb(con, archive, table)

        # Attach SQLite Database plugin layer
        logger.info("Loading SQLite translation layer extensions...")
        con.execute("INSTALL sqlite;")
        con.execute("LOAD sqlite;")

        logger.info(f"Attaching destination transaction engine space: {target_sqlite_name}")
        con.execute(f"ATTACH '{target_sqlite_name}' AS target_sqlite (TYPE SQLITE);")

        logger.info("Constructing remote destination tables and bare schemas...")
        initialize_bare_sqlite_schema(con)
        logger.info("Destination SQLite layouts compiled successfully.")

        logger.info("Initiating analytical transformations and relational compilation...")
        if os.path.exists(sql_path):
            with open(sql_path, 'r') as query_file:
                transformation_queries = query_file.read()

            transform_start = time.time()
            logger.info("Executing transformations.sql inside DuckDB core. (Progress indicators will emit directly from engine threads below)")

            con.execute(transformation_queries)

            duration = time.time() - transform_start
            logger.info(f"Analytical transformations completed successfully in {duration:.2f} seconds.")
        else:
            logger.warning(f"Execution warning: {sql_path} not found. Skipping data load steps.")

        # Sever the live pipeline to allow index attachment locks safely
        logger.info("Severing pipeline database attachments to prepare for standalone indexing...")
        con.execute("DETACH target_sqlite;")
        con.close()

        # Call isolated indexing logic
        logger.info("Generating optimized fast search secondary B-Trees on destination...")
        index_start = time.time()
        apply_optimized_indexes(target_sqlite_name)
        logger.info(f"Indexing routines finalized successfully in {time.time() - index_start:.2f} seconds.")

        total_duration = time.time() - start_time
        logger.info("==================================================")
        logger.info(f"PIPELINE TERMINATED SUCCESSFULLY In {total_duration:.2f} seconds.")
        logger.info("==================================================")

    except Exception as e:
        logger.critical(f"PIPELINE CRASHED DIAGNOSTIC DUMP: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        try:
            con.close()
        except Exception:
            pass
        cleanup_temp_files()


if __name__ == "__main__":
    main()