import duckdb
import os
import requests
import subprocess

# Constants
DUMP_URL_BASE = "https://data.metabrainz.org/pub/musicbrainz/data/fullexport"
FILES_TO_DOWNLOAD = ["mbdump.tar.bz2", "mbdump-derived.tar.bz2"]

# Define only the tables we care about to save disk space
REQUIRED_TABLES = [
    "recording", "track", "medium", "release", "release_group",
    "release_status", "release_group_primary_type",
    "artist_credit_name", "artist",
    "url", "l_recording_url", "link", "link_type"
]


def get_latest_dump_url():
    """Fetches the latest dump timestamp from MusicBrainz."""
    response = requests.get(f"{DUMP_URL_BASE}/LATEST")
    response.raise_for_status()
    latest_dir = response.text.strip()
    return f"{DUMP_URL_BASE}/{latest_dir}"


def extract_and_load_table(con, archive_name, table_name):
    """
    Extracts a single table from the bz2 tarball, loads it into DuckDB,
    and immediately deletes the text file to conserve storage.
    """
    print(f"Extracting {table_name} from {archive_name}...")
    # Extract only the specific file. MB dumps contain folders named like 'mbdump/table_name'
    subprocess.run(["tar", "-xjf", archive_name, f"mbdump/{table_name}"], check=False)

    file_path = f"mbdump/{table_name}"
    if os.path.exists(file_path):
        print(f"Loading {table_name} into DuckDB...")
        # MusicBrainz dumps are TSV. We load them into an in-memory or temp-disk DuckDB table
        con.execute(f"""
            CREATE TABLE {table_name} AS 
            SELECT * FROM read_csv_auto('{file_path}', sep='\t', header=False, nullstr='\\N')
        """)
        os.remove(file_path)
    else:
        print(f"Warning: {table_name} not found in {archive_name}")


def main():
    latest_url = get_latest_dump_url()

    # Initialize DuckDB
    # Spilling to disk and limiting memory to 4GB prevents GitHub runner OOM crashes (Runner has ~7GB max)
    con = duckdb.connect('temp_mb.duckdb')
    con.execute("PRAGMA memory_limit='4GB'")
    con.execute("PRAGMA temp_directory='duckdb_temp'")

    # 1. Download, Extract, Load, and Clean up
    for archive in FILES_TO_DOWNLOAD:
        url = f"{latest_url}/{archive}"
        print(f"Downloading {archive}...")
        subprocess.run(["curl", "-O", url], check=True)

        for table in REQUIRED_TABLES:
            extract_and_load_table(con, archive, table)

        # Delete archive immediately after extracting needed tables
        os.remove(archive)

    # 2. Attach SQLite and setup schema
    print("Installing SQLite extension and attaching final database...")
    con.execute("INSTALL sqlite;")
    con.execute("LOAD sqlite;")
    con.execute("ATTACH 'music_analytics.db' AS final_db (TYPE SQLITE);")

    # Load schema into SQLite DB (defined in section below)
    setup_sqlite_schema(con)

    # 3. Execute the Core Transformation Logic
    print("Executing transformations and loading into SQLite...")
    execute_transformations(con)

    print("Finished successfully.")


def setup_sqlite_schema(con):
    con.execute("""
                CREATE TABLE final_db.release_group
                (
                    release_group_mbid  TEXT PRIMARY KEY,
                    release_group_title TEXT NOT NULL,
                    release_group_type  TEXT
                );

                CREATE TABLE final_db.recording
                (
                    recording_mbid     TEXT PRIMARY KEY,
                    release_group_mbid TEXT NOT NULL,
                    length             INTEGER,
                    FOREIGN KEY (release_group_mbid) REFERENCES release_group (release_group_mbid)
                );

                CREATE TABLE final_db.recording_artists
                (
                    recording_mbid     TEXT    NOT NULL,
                    artist_mbid        TEXT    NOT NULL,
                    position           INTEGER NOT NULL,
                    artist_name        TEXT    NOT NULL,
                    artist_wikidata_id TEXT,
                    FOREIGN KEY (recording_mbid) REFERENCES recording (recording_mbid),
                    PRIMARY KEY (recording_mbid, artist_mbid)
                );

                CREATE TABLE final_db.link_lookup
                (
                    url_identifier TEXT NOT NULL,
                    provider       TEXT,
                    recording_mbid TEXT NOT NULL,
                    FOREIGN KEY (recording_mbid) REFERENCES recording (recording_mbid),
                    PRIMARY KEY (url_identifier, provider)
                );

                CREATE TABLE final_db.text_lookup
                (
                    id             INTEGER PRIMARY KEY,
                    track_title    TEXT NOT NULL,
                    release_title  TEXT NOT NULL,
                    artist_name    TEXT NOT NULL,
                    recording_mbid TEXT NOT NULL,
                    FOREIGN KEY (recording_mbid) REFERENCES recording (recording_mbid)
                );

                -- Create Indexes
                CREATE INDEX final_db.idx_text_lookup ON text_lookup (track_title, artist_name);
                CREATE INDEX final_db.idx_recording_artists ON recording_artists (recording_mbid, position, artist_name, artist_wikidata_id);
                """)


def execute_transformations(con):
    with open('transformations.sql', 'r') as f:
        sql = f.read()
    con.execute(sql)


if __name__ == "__main__":
    main()
