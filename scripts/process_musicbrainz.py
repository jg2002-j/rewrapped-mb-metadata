import duckdb
import os
import subprocess
import sys
from datetime import datetime


def get_tar_paths():
    """
    Selects production or localized test archives depending on execution arguments
    or active automated environment hooks.
    """
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


def get_db_name():
    """Resolves output dynamic layout mapping."""
    env_ts = os.environ.get("TARGET_TIMESTAMP")
    if env_ts:
        return f"metadata-{env_ts}.db"
    # Local fallback timestamp matching design specifications
    return f"metadata-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"


def extract_and_stream_to_duckdb(con, archive_path, table_name):
    """
    Extracts individual targets piece-by-piece from tarball, streams raw CSV layouts,
    and isolates storage vectors to prevent running out of storage.
    """
    internal_tar_path = f"mbdump/{table_name}"
    print(f"Isolating {internal_tar_path} from {archive_path}...")

    # Extract singular target dynamically without expanding the entire package to disk
    cmd = ["tar", "-xjf", archive_path, internal_tar_path]
    subprocess.run(cmd, check=False)

    if os.path.exists(internal_tar_path):
        print(f"Streaming text schema directly into memory structures: {table_name}")
        # MusicBrainz control sequences utilize \N explicitly for structural NULL targets
        con.execute(f"""
            CREATE TABLE {table_name} AS 
            SELECT * FROM read_csv_auto('{internal_tar_path}', sep='\t', header=False, nullstr='\\N')
        """)
        # Immediate clean destruction of intermediate text data blocks
        os.remove(internal_tar_path)
        try:
            os.rmdir("mbdump")
        except OSError:
            pass  # Keep directories active if other threads rely on structural persistence
    else:
        print(f"Skipping extraction target: {table_name} (Missing in archive context)")


def main():
    archives = get_tar_paths()
    target_sqlite_name = get_db_name()
    print(f"Target execution output container resolved: {target_sqlite_name}")

    # Establish local transactional backend with optimized spill strategies
    con = duckdb.connect('engine_runtime.duckdb')
    con.execute("PRAGMA memory_limit='4GB'")
    con.execute("PRAGMA temp_directory='duckdb_spill_buffer'")

    # Targeted extraction arrays validating strictly against structural layout specifications
    required_tables = [
        "recording", "track", "medium", "release", "release_group",
        "release_status", "release_group_primary_type",
        "artist_credit_name", "artist",
        "url", "l_recording_url", "link", "link_type"
    ]

    for archive in archives:
        if not os.path.exists(archive):
            print(f"Critical execution barrier: File target missing -> {archive}")
            sys.exit(1)
        for table in required_tables:
            extract_and_stream_to_duckdb(con, archive, table)

    # Load and initialize the native SQLite serialization module
    con.execute("INSTALL sqlite;")
    con.execute("LOAD sqlite;")
    con.execute(f"ATTACH '{target_sqlite_name}' AS target_sqlite (TYPE SQLITE);")

    # Build bare-metal schemas first (No structural indexes applied yet)
    print("Constructing remote destination tables...")
    initialize_bare_sqlite_schema(con)

    # Compute analytical steps and populate target container maps
    print("Initiating analytical transformation transformations...")
    with open('transformations.sql', 'r') as query_file:
        transformation_queries = query_file.read()

    # Execute batch transactional mutations
    con.execute(transformation_queries)

    # Post-Insertion Global Indexing Step
    print("Data insertions successfully completed. Generating localized fast search indexes...")
    apply_optimized_indexes(con)

    print("Pipeline compilation routines terminated with explicit success.")


def initialize_bare_sqlite_schema(con):
    con.execute("""
                CREATE TABLE target_sqlite.release_group
                (
                    release_group_mbid  TEXT PRIMARY KEY,
                    release_group_title TEXT NOT NULL,
                    release_group_type  TEXT
                );

                CREATE TABLE target_sqlite.recording
                (
                    recording_mbid     TEXT PRIMARY KEY,
                    release_group_mbid TEXT NOT NULL,
                    length             INTEGER,
                    FOREIGN KEY (release_group_mbid) REFERENCES release_group (release_group_mbid)
                );

                CREATE TABLE target_sqlite.recording_artists
                (
                    recording_mbid     TEXT    NOT NULL,
                    artist_mbid        TEXT    NOT NULL,
                    position           INTEGER NOT NULL,
                    artist_name        TEXT    NOT NULL,
                    artist_wikidata_id TEXT,
                    FOREIGN KEY (recording_mbid) REFERENCES recording (recording_mbid),
                    PRIMARY KEY (recording_mbid, artist_mbid)
                );

                CREATE TABLE target_sqlite.link_lookup
                (
                    url_identifier TEXT NOT NULL,
                    provider       TEXT,
                    recording_mbid TEXT NOT NULL,
                    FOREIGN KEY (recording_mbid) REFERENCES recording (recording_mbid),
                    PRIMARY KEY (url_identifier, provider)
                );

                CREATE TABLE target_sqlite.text_lookup
                (
                    id             INTEGER PRIMARY KEY,
                    track_title    TEXT NOT NULL,
                    release_title  TEXT NOT NULL,
                    artist_name    TEXT NOT NULL,
                    recording_mbid TEXT NOT NULL,
                    FOREIGN KEY (recording_mbid) REFERENCES recording (recording_mbid)
                );
                """)


def apply_optimized_indexes(con):
    con.execute("""
                CREATE INDEX target_sqlite.idx_text_lookup ON text_lookup (track_title, artist_name);
                CREATE INDEX target_sqlite.idx_recording_artists ON recording_artists (recording_mbid, position, artist_name, artist_wikidata_id);
                """)


if __name__ == "__main__":
    main()
