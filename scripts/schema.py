import sqlite3
import logging
import time

logger = logging.getLogger("pipeline_engine")

def initialize_native_sqlite_schema(sqlite_path):
    """Creates tables locally to enforce structural optimizations like WITHOUT ROWID prior to DuckDB attachment."""
    logger.info(" -> Establishing native SQLite constraints and analytical table spaces...")
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.execute("PRAGMA journal_mode=OFF;")
        conn.execute("PRAGMA synchronous=OFF;")

        conn.executescript("""
                           CREATE TABLE release_group (
                                                          release_group_mbid  TEXT PRIMARY KEY,
                                                          release_group_title TEXT NOT NULL,
                                                          release_group_type  TEXT
                           );

                           CREATE TABLE recording (
                                                      recording_mbid      TEXT PRIMARY KEY,
                                                      release_group_mbid  TEXT NOT NULL,
                                                      length              INTEGER,
                                                      primary_artist_mbid TEXT,
                                                      primary_artist_name        TEXT,
                                                      primary_artist_wikidata_id TEXT
                           );

                           CREATE TABLE recording_artists (
                                                              recording_mbid     TEXT    NOT NULL,
                                                              artist_mbid        TEXT    NOT NULL,
                                                              position           INTEGER NOT NULL,
                                                              artist_name        TEXT    NOT NULL,
                                                              artist_wikidata_id TEXT
                           );

                           CREATE TABLE link_lookup (
                                                        url_identifier TEXT NOT NULL,
                                                        provider       TEXT,
                                                        recording_mbid TEXT NOT NULL,
                                                        PRIMARY KEY (provider, url_identifier, recording_mbid)
                           ) WITHOUT ROWID;

                           CREATE TABLE text_lookup (
                                                        track_title    TEXT NOT NULL,
                                                        release_title  TEXT NOT NULL,
                                                        artist_name    TEXT NOT NULL,
                                                        recording_mbid TEXT NOT NULL
                           );
                           """)
        conn.commit()
    finally:
        conn.close()

def apply_optimized_indexes(sqlite_path):
    """Establishes performance indexing targets and reinstates ACID safeties."""
    logger.info(f"Opening independent atomic write channel to SQLite database: {sqlite_path}")
    conn = sqlite3.connect(sqlite_path)
    try:
        # Re-enable standard safety headers after mass bulk writes have finalized
        conn.execute("PRAGMA journal_mode=DELETE;")
        conn.execute("PRAGMA synchronous=NORMAL;")

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