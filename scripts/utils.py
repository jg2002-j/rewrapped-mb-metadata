import os
import shutil
import subprocess
import sys

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
        print("Pipeline running in PRODUCTION context.")
        return prod_files
    if os.path.exists("test_mbdump.tar.bz2"):
        print("Pipeline running in LOCAL TEST context using test_mbdump.tar.bz2.")
        return test_files

    print("Defaulting to standard production naming layout locally.")
    return prod_files


def extract_and_stream_to_duckdb(con, archive_path, internal_name):
    """Streams data straight out of target tarball entries into active memory tables."""
    target_table = TABLE_MAPPING[internal_name]
    internal_tar_path = f"mbdump/{internal_name}"
    print(f"Isolating {internal_tar_path} from {archive_path}...")

    # Added check=True: Extraction failures act as a hard kill switch
    cmd = ["tar", "-xjf", archive_path, internal_tar_path]
    subprocess.run(cmd, check=True)

    columns = TABLE_SCHEMAS[internal_name]
    con.execute(f"DROP TABLE IF EXISTS {target_table};")

    if not os.path.exists(internal_tar_path):
        print(f"Critical execution barrier: Failed to locate {internal_tar_path} after extraction.")
        sys.exit(1)

    print(f"Streaming text schema directly into memory structures: {target_table}")
    con.execute(f"""
        CREATE TABLE {target_table} AS 
        SELECT * FROM read_csv('{internal_tar_path}', sep='\t', header=False, nullstr='\\N', names={columns}, all_varchar=True)
    """)
    os.remove(internal_tar_path)
    try:
        os.rmdir("mbdump")
    except OSError:
        pass


def cleanup_temp_files():
    """Sweeps pipeline engine disk cache allocations cleanly."""
    print("Initiating temporary file cleanup routines...")
    temp_files = ['engine_runtime.duckdb', 'engine_runtime.duckdb.wal']
    temp_dirs = ['duckdb_spill_buffer', 'mbdump']

    for f in temp_files:
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"Removed temp file: {f}")
            except Exception as e:
                print(f"Warning: Could not remove file {f}: {e}")

    for d in temp_dirs:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
                print(f"Removed temp directory: {d}")
            except Exception as e:
                print(f"Warning: Could not remove directory {d}: {e}")