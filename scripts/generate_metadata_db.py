import os
import shutil
import sqlite3
import tarfile
from datetime import datetime
import duckdb

DB_NAME = "metadata_core.db"
DUCKDB_TMP = "duckdb_working.db"
TEMP_TSV = "temp_extract.tsv"
TAR_FILES = ["mbdump.tar.bz2", "mbdump-derived.tar.bz2"]

TRACKED_FILES = {
    "mbdump/artist": "raw_artist",
    "mbdump/artist_credit_name": "raw_artist_credit_name",
    "mbdump/release": "raw_release",
    "mbdump/release_group": "raw_release_group",
    "mbdump/release_group_primary_type": "raw_rg_type",
    "mbdump/medium": "raw_medium",
    "mbdump/track": "raw_track",
    "mbdump/url": "raw_url",
    "mbdump/l_release_url": "raw_l_release_url",
    "mbdump/l_artist_url": "raw_l_artist_url"
}

def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")

def init_duckdb():
    log("Initializing DuckDB engine...")
    if os.path.exists(DUCKDB_TMP):
        os.remove(DUCKDB_TMP)

    con = duckdb.connect(DUCKDB_TMP)
    # Limit memory execution to protect the 7GB GitHub runner environment
    con.execute("SET max_memory='5GB';")
    con.execute("SET preserve_insertion_order=false;")
    return con

def init_target_sqlite():
    log(f"Initializing target deployment SQLite container: {DB_NAME}")
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
                   CREATE TABLE link_canonical_lookup (
                                                          streaming_link      TEXT PRIMARY KEY,
                                                          track_title         TEXT NOT NULL,
                                                          duration_ms         INTEGER NOT NULL,
                                                          album_mbid          TEXT NOT NULL,
                                                          album_title         TEXT NOT NULL,
                                                          release_group_mbid  TEXT NOT NULL,
                                                          release_group_title TEXT NOT NULL,
                                                          release_group_type  TEXT NOT NULL,
                                                          artist_mbid         TEXT NOT NULL,
                                                          artist_name         TEXT NOT NULL,
                                                          artist_wikidata_id  TEXT
                   );
                   """)

    cursor.execute("""
                   CREATE TABLE text_canonical_lookup (
                                                          clean_track         TEXT NOT NULL,
                                                          clean_album         TEXT NOT NULL,
                                                          clean_artist        TEXT NOT NULL,
                                                          track_title         TEXT NOT NULL,
                                                          duration_ms         INTEGER NOT NULL,
                                                          album_mbid          TEXT NOT NULL,
                                                          album_title         TEXT NOT NULL,
                                                          release_group_mbid  TEXT NOT NULL,
                                                          release_group_title TEXT NOT NULL,
                                                          release_group_type  TEXT NOT NULL,
                                                          artist_mbid         TEXT NOT NULL,
                                                          artist_name         TEXT NOT NULL,
                                                          artist_wikidata_id  TEXT,
                                                          PRIMARY KEY (clean_track, clean_album, clean_artist)
                   );
                   """)
    conn.commit()
    conn.close()

def stream_tar_to_duckdb(con):
    for tar_name in TAR_FILES:
        if not os.path.exists(tar_name):
            log(f"⚠️ Warning: Archive {tar_name} missing. Skipping...")
            continue

        log(f"📦 Processing compressed data archive: {tar_name}")
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                if member.name in TRACKED_FILES:
                    target_table = TRACKED_FILES[member.name]
                    log(f"▶️ Spooling & Extracting: {member.name} -> {target_table}")

                    # Extract single file safely to disk
                    with tar.extractfile(member) as source, open(TEMP_TSV, "wb") as target:
                        shutil.copyfileobj(source, target)

                    # Dynamically inspect column counts to bypass standard schema definitions safely
                    with open(TEMP_TSV, 'r', encoding='utf-8', errors='ignore') as f:
                        first_line = f.readline()
                        col_count = len(first_line.split('\t'))

                    col_names = [f"c{i}" for i in range(col_count)]

                    # Blazing fast native DuckDB bulk CSV reader
                    con.execute(f"""
                        CREATE TABLE {target_table} AS 
                        SELECT * FROM read_csv('{TEMP_TSV}', 
                                               header=False, 
                                               delim='\t', 
                                               quote='', 
                                               escape='', 
                                               nullstr='\\N', 
                                               names={col_names},
                                               all_varchar=True);
                    """)

                    # Clean intermediate disk space instantly
                    os.remove(TEMP_TSV)
                    log(f"   ✅ Loaded {target_table} into columnar storage.")

def execute_analytics_and_export(con):
    log("🔌 Attaching deployment SQLite binary container directly to DuckDB...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB_NAME}' AS sqlite_db (TYPE SQLITE);")

    # SQL Macro logic for string normalization compiled straight to native C execution loops
    duckdb_clean_str = "lower(regexp_replace(regexp_replace(trim(COL), '[^\\w\\s]', '', 'g'), '\\s+', ' ', 'g'))"

    log("🔗 Compiling Link Canonical Cache & writing out directly to SQLite...")
    con.execute(f"""
        INSERT OR IGNORE INTO sqlite_db.link_canonical_lookup
        SELECT DISTINCT 
            u.c2 AS streaming_link,
            t.c2 AS track_title,
            COALESCE(TRY_CAST(t.c8 AS INTEGER), 0) AS duration_ms,
            r.c1 AS album_mbid,
            r.c2 AS album_title,
            rg.c1 AS release_group_mbid,
            rg.c2 AS release_group_title,
            COALESCE(rgt.c1, 'Unknown') AS release_group_type,
            a.c1 AS artist_mbid,
            a.c2 AS artist_name,
            regexp_extract(artist_u.c2, 'wikidata\\.org/wiki/(Q\\d+)', 1) AS artist_wikidata_id
        FROM raw_l_release_url lru
        JOIN raw_url u ON lru.c3 = u.c0
        JOIN raw_release r ON lru.c2 = r.c0
        JOIN raw_medium m ON r.c0 = m.c1
        JOIN raw_track t ON t.c3 = m.c0
        JOIN raw_release_group rg ON r.c3 = rg.c0
        LEFT JOIN raw_rg_type rgt ON rg.c4 = rgt.c0
        JOIN raw_artist_credit_name acn ON rg.c3 = acn.c0
        JOIN raw_artist a ON acn.c1 = a.c0
        LEFT JOIN raw_l_artist_url lau ON a.c0 = lau.c2
        LEFT JOIN raw_url artist_u ON lau.c3 = artist_u.c0 AND artist_u.c2 LIKE '%wikidata.org%'
        WHERE u.c2 LIKE '%spotify.com%' 
           OR u.c2 LIKE 'spotify:%' 
           OR u.c2 LIKE '%apple.com%' 
           OR u.c2 LIKE '%tidal.com%' 
           OR u.c2 LIKE '%wikidata.org%';
    """)

    log("🔗 Compiling Text Canonical Cache & writing out directly to SQLite...")
    con.execute(f"""
        INSERT OR IGNORE INTO sqlite_db.text_canonical_lookup
        SELECT DISTINCT 
            {duckdb_clean_str.replace('COL', 't.c2')} AS clean_track,
            {duckdb_clean_str.replace('COL', 'r.c2')} AS clean_album,
            {duckdb_clean_str.replace('COL', 'a.c2')} AS clean_artist,
            t.c2 AS track_title,
            COALESCE(TRY_CAST(t.c8 AS INTEGER), 0) AS duration_ms,
            r.c1 AS album_mbid,
            r.c2 AS album_title,
            rg.c1 AS release_group_mbid,
            rg.c2 AS release_group_title,
            COALESCE(rgt.c1, 'Unknown') AS release_group_type,
            a.c1 AS artist_mbid,
            a.c2 AS artist_name,
            regexp_extract(artist_u.c2, 'wikidata\\.org/wiki/(Q\\d+)', 1) AS artist_wikidata_id
        FROM raw_track t
        JOIN raw_medium m ON t.c3 = m.c0
        JOIN raw_release r ON m.c1 = r.c0
        JOIN raw_release_group rg ON r.c3 = rg.c0
        LEFT JOIN raw_rg_type rgt ON rg.c4 = rgt.c0
        JOIN raw_artist_credit_name acn ON rg.c3 = acn.c0
        JOIN raw_artist a ON acn.c1 = a.c0
        LEFT JOIN raw_l_artist_url lau ON a.c0 = lau.c2
        LEFT JOIN raw_url artist_u ON lau.c3 = artist_u.c0 AND artist_u.c2 LIKE '%wikidata.org%';
    """)

    con.execute("DETACH sqlite_db;")
    log("🎉 All data pipelines completed successfully.")

def optimize_final_sqlite():
    log("🗂️ Generating structured binary search deployment indexes on production asset...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_link_search ON link_canonical_lookup(streaming_link);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_text_search ON text_canonical_lookup(clean_track, clean_album, clean_artist);")
    cursor.execute("VACUUM;")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    import sys

    log("🚀 Starting DuckDB-powered database generation pipeline...")
    init_target_sqlite()
    db_con = init_duckdb()

    try:
        stream_tar_to_duckdb(db_con)
        execute_analytics_and_export(db_con)
        optimize_final_sqlite()
        log("🎉 Production ready metadata binary compiled successfully!")
    except Exception as e:
        log(f"❌ Structural Failure during compilation: {str(e)}")
        sys.exit(1)
    finally:
        db_con.close()
        if os.path.exists(DUCKDB_TMP):
            os.remove(DUCKDB_TMP)