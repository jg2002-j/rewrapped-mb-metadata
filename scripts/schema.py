import sqlite3
import logging
import time

logger = logging.getLogger("pipeline_engine")

def initialize_bare_sqlite_schema(con):
    """Creates the analytical table space structures inside the target SQLite database."""
    logger.info(" -> Transmitting core CREATE TABLE DDL queries across attachment link...")
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
    logger.opening_index = time.time()
    logger.info(f"Opening independent atomic write channel to SQLite database: {sqlite_path}")
    conn = sqlite3.connect(sqlite_path)
    try:
        cursor = conn.cursor()

        logger.info(" -> Building index Tree space: [idx_text_lookup] on text_lookup(track_title, artist_name)...")
        t_start = time.time()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_text_lookup ON text_lookup (track_title, artist_name);")
        logger.info(f" -> Done. Time: {time.time() - t_start:.2f}s")

        logger.info(" -> Building index Tree space: [idx_recording_artists_lookup] on recording_artists(recording_mbid, artist_mbid)...")
        t_start = time.time()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recording_artists_lookup ON recording_artists (recording_mbid, artist_mbid);")
        logger.info(f" -> Done. Time: {time.time() - t_start:.2f}s")

        logger.info(" -> Building index Tree space: [idx_recording_artists_details] on recording_artists(position, artist_name, artist_wikidata_id)...")
        t_start = time.time()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recording_artists_details ON recording_artists (position, artist_name, artist_wikidata_id);")
        logger.info(f" -> Done. Time: {time.time() - t_start:.2f}s")

        logger.info("Committing structural tree alterations to disk headers...")
        conn.commit()
    finally:
        conn.close()