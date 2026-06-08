import os
import shutil
import subprocess
import sys
import logging
import time

logger = logging.getLogger("pipeline_engine")

TABLE_MAPPING = {
    "recording": "raw_recording",
    "track": "raw_track",
    "medium": "raw_medium",
    "release": "raw_release",
    "release_country": "raw_release_country",
    "release_unknown_country": "raw_release_unknown_country",
    "release_group": "raw_release_group",
    "release_group_primary_type": "raw_rg_type",
    "artist_credit_name": "raw_artist_credit_name",
    "artist": "raw_artist",
    "url": "raw_url",
    "l_recording_url": "raw_l_recording_url",
    "l_artist_url": "raw_l_artist_url"
}

TABLE_SCHEMAS = {
    "recording": ["id", "gid", "name", "artist_credit", "length", "comment", "edits_pending", "last_updated", "video"],
    "track": ["id", "gid", "recording", "medium", "position", "number", "name", "artist_credit", "length",
              "edits_pending", "last_updated", "is_data_track"],
    "medium": ["id", "release", "position", "format", "name", "track_count", "edits_pending", "last_updated"],
    "release": ["id", "gid", "name", "artist_credit", "release_group", "status", "packaging", "language", "script",
                "barcode", "comment", "edits_pending", "quality", "last_updated"],
    "release_country": ["release", "country", "date_year", "date_month", "date_day"],
    "release_unknown_country": ["release", "date_year", "date_month", "date_day"],
    "release_group": ["id", "gid", "name", "artist_credit", "type", "comment", "edits_pending", "last_updated"],
    "release_group_primary_type": ["id", "name", "parent", "child_order", "description"],
    "artist_credit_name": ["artist_credit", "position", "artist", "name", "join_phrase"],
    "artist": ["id", "gid", "name", "sort_name", "begin_date_year", "begin_date_month", "begin_date_day",
               "end_date_year", "end_date_month", "end_date_day", "type", "area", "gender", "comment", "edits_pending",
               "last_updated", "ended"],
    "url": ["id", "gid", "url", "edits_pending", "last_updated"],
    "l_recording_url": ["id", "link", "entity0", "entity1", "edits_pending", "last_updated"],
    "l_artist_url": ["id", "link", "entity0", "entity1", "edits_pending", "last_updated"]
}


def get_tar_paths():
    """Determines target tarball context locations based on environment flags."""
    is_prod = os.environ.get("GITHUB_ACTIONS") == "true" or "--prod" in sys.argv
    prod_files = ["mbdump.tar.bz2"]
    test_files = ["test_mbdump.tar.bz2"]

    if is_prod:
        logger.info("Pipeline context resolved: [PRODUCTION ENVIRONMENT]")
        return prod_files
    if os.path.exists("test_mbdump.tar.bz2"):
        logger.info("Pipeline context resolved: [LOCAL TESTING RUN] via test_mbdump.tar.bz2")
        return test_files

    logger.info("Defaulting execution naming format layout to standard production naming.")
    return prod_files


def extract_and_stream_to_duckdb(con, archive_path, internal_name):
    """Streams data straight out of target tarball entries into active memory tables."""
    target_table = TABLE_MAPPING[internal_name]
    internal_tar_path = f"mbdump/{internal_name}"

    logger.info(f" -> Shell calling: Isolating file entry '{internal_tar_path}' via sub-shell streaming payload...")

    tar_start = time.time()
    cmd = ["tar", "-xjf", archive_path, internal_tar_path]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info(f" -> Tar execution extraction confirmed ok. Action time: {time.time() - tar_start:.2f}s")
    except subprocess.CalledProcessError as err:
        logger.error(f"CRITICAL: Tar shell tracking code error output: {err.stderr.decode().strip()}")
        raise err

    columns = TABLE_SCHEMAS[internal_name]
    con.execute(f"DROP TABLE IF EXISTS {target_table};")

    if not os.path.exists(internal_tar_path):
        logger.critical(f"FATAL EXCEPTION: Target payload vanished or unreadable at path: {internal_tar_path}")
        sys.exit(1)

    logger.info(f" -> Parsing flat TSV vectors into relational columns for table: '{target_table}'...")
    parse_start = time.time()

    con.execute(f"""
        CREATE TABLE {target_table} AS 
        SELECT * FROM read_csv('{internal_tar_path}', sep='\t', header=False, nullstr='\\N', names={columns}, all_varchar=True)
    """)

    logger.info(f" -> Ingestion stream mapping complete. Row count: {con.execute(f'SELECT COUNT(*) FROM {target_table}').fetchone()[0]}. Ingestion time: {time.time() - parse_start:.2f}s")

    os.remove(internal_tar_path)
    try:
        os.rmdir("mbdump")
    except OSError:
        pass


def cleanup_temp_files():
    """Sweeps pipeline engine disk cache allocations cleanly."""
    logger.info("Running disk buffer cache purges...")
    temp_files = ['engine_runtime.duckdb', 'engine_runtime.duckdb.wal']
    temp_dirs = ['duckdb_spill_buffer', 'mbdump']

    for f in temp_files:
        if os.path.exists(f):
            try:
                os.remove(f)
                logger.info(f" -> Purged temporary database file: {f}")
            except Exception as e:
                logger.warning(f" -> Warning: Execution context lock encountered when purging file {f}: {e}")

    for d in temp_dirs:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
                logger.info(f" -> Purged temporary directory space: {d}")
            except Exception as e:
                logger.warning(f" -> Warning: Execution context lock encountered when purging directory {d}: {e}")