import duckdb
import os
import shutil
import subprocess
import sys


def get_tar_paths():
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


# Adjusted for the multi-table release schema refactoring
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
    "url": ["id", "gid", "url", "description", "edits_pending", "last_updated"],
    "l_recording_url": ["id", "link", "entity0", "entity1", "edits_pending", "last_updated"],
    "l_artist_url": ["id", "link", "entity0", "entity1", "edits_pending", "last_updated"]
}


def extract_and_stream_to_duckdb(con, archive_path, internal_name):
    target_table = TABLE_MAPPING[internal_name]
    internal_tar_path = f"mbdump/{internal_name}"
    print(f"Isolating {internal_tar_path} from {archive_path}...")

    cmd = ["tar", "-xjf", archive_path, internal_tar_path]
    subprocess.run(cmd, check=False)

    columns = TABLE_SCHEMAS[internal_name]
    con.execute(f"DROP TABLE IF EXISTS {target_table};")

    if os.path.exists(internal_tar_path):
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
    else:
        print(f"Skipping extraction target: {internal_name} (Generating type-safe mock schema)")
        column_selects = ", ".join([f"CAST(NULL AS VARCHAR) AS {col}" for col in columns])
        con.execute(f"CREATE TABLE {target_table} AS SELECT * FROM (SELECT {column_selects}) WHERE 1=0;")


def cleanup_temp_files():
    print("Initiating temporary file cleanup routines...")
    try:
        duckdb.close()
    except Exception:
        pass

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


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sql_path = os.path.join(script_dir, "transformations.sql")
    target_sqlite_name = "metadata.db"

    if os.path.exists(target_sqlite_name):
        os.remove(target_sqlite_name)
    cleanup_temp_files()

    con = duckdb.connect('engine_runtime.duckdb')

    try:
        con.execute("PRAGMA memory_limit='4GB'")
        con.execute("PRAGMA temp_directory='duckdb_spill_buffer'")

        archives = get_tar_paths()
        for archive in archives:
            if not os.path.exists(archive):
                print(f"Critical execution barrier: File target missing -> {archive}")
                sys.exit(1)
            for table in TABLE_MAPPING.keys():
                extract_and_stream_to_duckdb(con, archive, table)

        con.execute("INSTALL sqlite;")
        con.execute("LOAD sqlite;")
        con.execute(f"ATTACH '{target_sqlite_name}' AS target_sqlite (TYPE SQLITE);")

        print("Constructing remote destination tables...")
        initialize_bare_sqlite_schema(con)

        print("Initiating analytical transformations...")
        if os.path.exists(sql_path):
            with open(sql_path, 'r') as query_file:
                transformation_queries = query_file.read()
            con.execute(transformation_queries)
            print("Transformations completed successfully.")
        else:
            print(f"Warning: {sql_path} not found. Skipping data load steps.")

        print("Generating optimized fast search indexes...")
        apply_optimized_indexes(con)

        print("Pipeline compilation routines terminated with explicit success.")

    finally:
        con.close()
        cleanup_temp_files()


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
                    length             INTEGER
                );

                CREATE TABLE target_sqlite.recording_artists
                (
                    recording_mbid     TEXT    NOT NULL,
                    artist_mbid        TEXT    NOT NULL,
                    position           INTEGER NOT NULL,
                    artist_name        TEXT    NOT NULL,
                    artist_wikidata_id TEXT,
                    PRIMARY KEY (recording_mbid, artist_mbid)
                );

                CREATE TABLE target_sqlite.link_lookup
                (
                    url_identifier TEXT NOT NULL,
                    provider       TEXT,
                    recording_mbid TEXT NOT NULL,
                    PRIMARY KEY (url_identifier, provider)
                );

                CREATE TABLE target_sqlite.text_lookup
                (
                    id             INTEGER PRIMARY KEY,
                    track_title    TEXT NOT NULL,
                    release_title  TEXT NOT NULL,
                    artist_name    TEXT NOT NULL,
                    recording_mbid TEXT NOT NULL
                );
                """)


def apply_optimized_indexes(con):
    con.execute("""
                CREATE INDEX idx_text_lookup ON target_sqlite.text_lookup (track_title, artist_name);
                CREATE INDEX idx_recording_artists ON target_sqlite.recording_artists (recording_mbid, position, artist_name, artist_wikidata_id);
                """)


if __name__ == "__main__":
    main()