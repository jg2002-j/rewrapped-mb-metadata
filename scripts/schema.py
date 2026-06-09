import sqlite3
import logging
import os
import time

logger = logging.getLogger("pipeline_engine")

# Larger pages reduce b-tree depth and generally compress better for a
# read-only analytics database.
PAGE_SIZE = 8192


def initialize_native_sqlite_schema(sqlite_path):
    """Creates tables locally to enforce structural optimizations like WITHOUT ROWID prior to DuckDB attachment."""
    logger.info(" -> Establishing native SQLite constraints and analytical table spaces...")
    conn = sqlite3.connect(sqlite_path)
    try:
        # page_size must be set on an empty database, before any table is created.
        conn.execute(f"PRAGMA page_size={PAGE_SIZE};")
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
                                                      primary_artist_mbid TEXT
                           );

                           -- Normalised artist dimension: one row per distinct artist
                           -- credited on any canonical recording or release group.
                           CREATE TABLE artists (
                                                    artist_mbid        TEXT PRIMARY KEY,
                                                    artist_name        TEXT NOT NULL,
                                                    artist_wikidata_id TEXT
                           );

                           -- Credit link tables (join to artists for name/wikidata).
                           CREATE TABLE recording_artists (
                                                              recording_mbid TEXT    NOT NULL,
                                                              artist_mbid    TEXT    NOT NULL,
                                                              position       INTEGER NOT NULL
                           );

                           CREATE TABLE release_group_artists (
                                                                  release_group_mbid TEXT    NOT NULL,
                                                                  artist_mbid        TEXT    NOT NULL,
                                                                  position           INTEGER NOT NULL
                           );

                           CREATE TABLE link_lookup (
                                                        url_identifier TEXT NOT NULL,
                                                        provider       TEXT,
                                                        recording_mbid TEXT NOT NULL,
                                                        PRIMARY KEY (provider, url_identifier, recording_mbid)
                           ) WITHOUT ROWID;

                           CREATE TABLE text_lookup (
                                                        id             INTEGER PRIMARY KEY,
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
    """Establishes performance indexing targets, gathers planner stats, then compacts the file."""
    logger.info(f"Opening independent atomic write channel to SQLite database: {sqlite_path}")
    conn = sqlite3.connect(sqlite_path)
    try:
        # The DuckDB engine is already closed, so the page cache below is the only
        # large allocation here. Keep journalling off purely for build speed.
        conn.execute("PRAGMA journal_mode=OFF;")
        conn.execute("PRAGMA synchronous=OFF;")
        conn.execute("PRAGMA cache_size=-2000000;")  # ~2 GB page cache for index builds

        cursor = conn.cursor()

        # Covering index for the text fallback: all three equality-matched columns
        # plus the returned recording_mbid, so a lookup is served from the index
        # alone (no table fetch).
        logger.info(" -> Building index Tree space: [idx_text_lookup] on text_lookup(track_title, artist_name, release_title, recording_mbid)...")
        t_start = time.time()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_text_lookup ON text_lookup (track_title, artist_name, release_title, recording_mbid);")
        logger.info(f" -> Done. Time: {time.time() - t_start:.2f}s")

        # Credit lookups: fetch a recording's / release group's artists in credit
        # order; artist_mbid trails so the lookup is covered (no table fetch before
        # the join to artists).
        logger.info(" -> Building index Tree space: [idx_recording_artists] on recording_artists(recording_mbid, position, artist_mbid)...")
        t_start = time.time()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recording_artists ON recording_artists (recording_mbid, position, artist_mbid);")
        logger.info(f" -> Done. Time: {time.time() - t_start:.2f}s")

        logger.info(" -> Building index Tree space: [idx_release_group_artists] on release_group_artists(release_group_mbid, position, artist_mbid)...")
        t_start = time.time()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_release_group_artists ON release_group_artists (release_group_mbid, position, artist_mbid);")
        logger.info(f" -> Done. Time: {time.time() - t_start:.2f}s")

        # Planner statistics so the downstream service reliably chooses these indexes.
        logger.info("Gathering query planner statistics (ANALYZE)...")
        cursor.execute("ANALYZE;")

        logger.info("Committing structural tree alterations to disk headers...")
        conn.commit()
    finally:
        conn.close()

    _compact_database(sqlite_path)


def _compact_database(sqlite_path):
    """Defragment into a fresh file via VACUUM INTO to minimise on-disk and post-zstd size.

    VACUUM INTO writes a clean, contiguous copy (indexes + ANALYZE stats included).
    The transient second copy is cheap relative to the build space freed earlier in
    the workflow, and the defragmented layout compresses noticeably better.
    """
    compact_path = sqlite_path + ".compact"
    if os.path.exists(compact_path):
        os.remove(compact_path)

    logger.info("Compacting database via VACUUM INTO to shrink final payload...")
    t_start = time.time()
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.execute(f"VACUUM INTO '{compact_path}';")
    finally:
        conn.close()

    os.replace(compact_path, sqlite_path)
    logger.info(f" -> Compaction complete in {time.time() - t_start:.2f}s")
