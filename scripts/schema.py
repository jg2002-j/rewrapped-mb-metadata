import sqlite3

def initialize_bare_sqlite_schema(con):
    """Creates the analytical table space structures inside the target SQLite database."""
    con.execute("""
                CREATE TABLE target_sqlite.release_group
                (
                    release_group_mbid  TEXT PRIMARY KEY,
                    release_group_title TEXT NOT NULL,
                    release_group_type  TEXT
                );

                CREATE TABLE target_sqlite.recording
                (
                    recording_mbid      TEXT PRIMARY KEY,
                    release_group_mbid  TEXT NOT NULL,
                    length              INTEGER,
                    primary_artist_mbid TEXT,
                    primary_artist_name        TEXT,
                    primary_artist_wikidata_id TEXT
                );

                CREATE TABLE target_sqlite.recording_artists
                (
                    recording_mbid     TEXT    NOT NULL,
                    artist_mbid        TEXT    NOT NULL,
                    position           INTEGER NOT NULL,
                    artist_name        TEXT    NOT NULL,
                    artist_wikidata_id TEXT
                );

                CREATE TABLE target_sqlite.link_lookup
                (
                    url_identifier TEXT NOT NULL,
                    provider       TEXT,
                    recording_mbid TEXT NOT NULL,
                    PRIMARY KEY (provider, url_identifier, recording_mbid)
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


def apply_optimized_indexes(sqlite_path):
    """Establishes traditional performance indexing targets."""
    conn = sqlite3.connect(sqlite_path)
    try:
        cursor = conn.cursor()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_text_lookup ON text_lookup (track_title, artist_name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recording_artists_lookup ON recording_artists (recording_mbid, artist_mbid);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recording_artists_details ON recording_artists (position, artist_name, artist_wikidata_id);")
        conn.commit()
    finally:
        conn.close()