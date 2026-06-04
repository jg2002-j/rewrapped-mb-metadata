import os
import re
import sqlite3
import tarfile
from datetime import datetime

DB_NAME = "metadata_core.db"
TAR_FILES = ["mbdump.tar.bz2", "mbdump-derived.tar.bz2"]

TRACKED_FILES = {
    "mbdump/artist": "raw_artist",
    "mbdump/release": "raw_release",
    "mbdump/release_group": "raw_release_group",
    "mbdump/release_group_primary_type": "raw_rg_type",
    "mbdump/medium": "raw_medium",  # The crucial bridge between Release and Track
    "mbdump/track": "raw_track",
    "mbdump/url": "raw_url",
    "mbdump/l_release_url": "raw_l_release_url",
    "mbdump/l_artist_url": "raw_l_artist_url"
}

COLUMN_MAP = {
    "raw_artist": [0, 1, 2],                   # id, gid, name
    "raw_release": [0, 1, 2, 4],               # id, gid, name, release_group
    "raw_release_group": [0, 1, 2, 3, 4],      # id, gid, name, artist_credit, type
    "raw_rg_type": [0, 1],                     # id, name
    "raw_medium": [0, 1],                      # id, release
    "raw_track": [0, 1, 6, 3, 8],              # id, gid, name (6), medium (3), length (8)
    "raw_url": [0, 1, 2],                      # id, gid, url
    "raw_l_release_url": [0, 2, 3],            # id, entity0, entity1
    "raw_l_artist_url": [0, 2, 3]              # id, entity0, entity1
}

TARGET_DOMAINS = [
    "wikidata.org", 
    "spotify.com", 
    "spotify:",      
    "apple.com",     
    "tidal.com",     
    "tidalhifi.com"  
]

def log(message):
    """Helper to print timestamped logs so we can track execution time."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")

def init_database():
    log(f"Initializing database: {DB_NAME}")
    
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("PRAGMA synchronous = OFF;")
    cursor.execute("PRAGMA journal_mode = MEMORY;")
    cursor.execute("PRAGMA cache_size = -2000000;")
    cursor.execute("PRAGMA temp_store = MEMORY;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS link_canonical_lookup (
            streaming_link      TEXT PRIMARY KEY,
            track_title         TEXT    NOT NULL,
            duration_ms         INTEGER NOT NULL,
            album_mbid          TEXT    NOT NULL,
            album_title         TEXT    NOT NULL,
            release_group_mbid  TEXT    NOT NULL,
            release_group_title TEXT    NOT NULL,
            release_group_type  TEXT    NOT NULL,
            artist_mbid         TEXT    NOT NULL,
            artist_name         TEXT    NOT NULL,
            artist_wikidata_id  TEXT
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS text_canonical_lookup (
            clean_track         TEXT    NOT NULL,
            clean_album         TEXT    NOT NULL,
            clean_artist        TEXT    NOT NULL,
            track_title         TEXT    NOT NULL,
            duration_ms         INTEGER NOT NULL,
            album_mbid          TEXT    NOT NULL,
            album_title         TEXT    NOT NULL,
            release_group_mbid  TEXT    NOT NULL,
            release_group_title TEXT    NOT NULL,
            release_group_type  TEXT    NOT NULL,
            artist_mbid         TEXT    NOT NULL,
            artist_name         TEXT    NOT NULL,
            artist_wikidata_id  TEXT,
            PRIMARY KEY (clean_track, clean_album, clean_artist)
        );
    """)

    cursor.execute("CREATE TABLE IF NOT EXISTS raw_artist (id INTEGER, gid TEXT, name TEXT);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_release (id INTEGER, gid TEXT, name TEXT, release_group INTEGER);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_release_group (id INTEGER, gid TEXT, name TEXT, artist_credit INTEGER, type INTEGER);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_rg_type (id INTEGER, name TEXT);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_medium (id INTEGER, release INTEGER);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_track (id INTEGER, gid TEXT, name TEXT, medium INTEGER, length INTEGER);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_url (id INTEGER, gid TEXT, url TEXT);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_l_release_url (id INTEGER, entity0 INTEGER, entity1 INTEGER);")
    cursor.execute("CREATE TABLE IF NOT EXISTS raw_l_artist_url (id INTEGER, entity0 INTEGER, entity1 INTEGER);")

    conn.commit()
    return conn

def clean_string(text):
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    return " ".join(text.split())

def extract_wikidata_token(url_string):
    if not url_string:
        return None
    match = re.search(r'wikidata\.org/wiki/(Q\d+)', url_string)
    return match.group(1) if match else None

def stream_file_into_sqlite(cursor, file_stream, table_name):
    batch = []
    batch_size = 100000
    rows_processed = 0

    target_indices = COLUMN_MAP[table_name]
    col_count = len(target_indices)
    max_index = max(target_indices)
    
    placeholders = ",".join(["?"] * col_count)
    insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders});"

    for line in file_stream:
        row_str = line.decode('utf-8', errors='ignore').rstrip('\n')
        columns = row_str.split('\t')

        if len(columns) <= max_index:
            columns += ['\\N'] * ((max_index + 1) - len(columns))

        sliced_columns = [columns[i] for i in target_indices]

        if table_name == "raw_url":
            if not any(domain in sliced_columns[2] for domain in TARGET_DOMAINS):
                continue

        cleaned_row = [None if col == '\\N' or col == '' else col for col in sliced_columns]
        batch.append(cleaned_row)
        rows_processed += 1

        if len(batch) >= batch_size:
            cursor.executemany(insert_sql, batch)
            cursor.connection.commit()
            batch = []

        if rows_processed % 500000 == 0:
            log(f"   -> {table_name}: Processed {rows_processed:,} valid rows...")

    if batch:
        cursor.executemany(insert_sql, batch)
        cursor.connection.commit()

    log(f"✅ Completed {table_name}. Total saved rows: {rows_processed:,}")

def stream_tar_and_populate_raw(conn):
    cursor = conn.cursor()

    for tar_name in TAR_FILES:
        if not os.path.exists(tar_name):
            log(f"⚠️ Warning: Archive file {tar_name} not found. Skipping...")
            continue

        log(f"📦 Opening streaming archive: {tar_name}")
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                if member.name in TRACKED_FILES:
                    target_table = TRACKED_FILES[member.name]
                    log(f"▶️ Streaming data file: {member.name} -> Target SQL: {target_table}")

                    file_stream = tar.extractfile(member)
                    if file_stream is None:
                        continue

                    stream_file_into_sqlite(cursor, file_stream, target_table)
                    conn.commit()

def execute_flattening_joins(conn):
    cursor = conn.cursor()
    conn.create_function("WIKITOKEN", 1, extract_wikidata_token)

    log("🗂️ Building temporary execution indexes for the relational map...")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_t_med ON raw_track(medium);")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_m_rel ON raw_medium(release);")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_r_id ON raw_release(id, release_group);")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_rg_id ON raw_release_group(id, artist_credit);")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_lru_ent ON raw_l_release_url(entity0, entity1);")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_u_id ON raw_url(id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_lau_ent ON raw_l_artist_url(entity0, entity1);")
    cursor.execute("CREATE INDEX IF NOT EXISTS tmp_a_id ON raw_artist(id);")
    conn.commit()

    log("🔗 Flattening and generating platform streaming link cache...")
    cursor.execute("""
        INSERT OR IGNORE INTO link_canonical_lookup
        SELECT DISTINCT u.url, t.name, COALESCE(t.length, 0), r.gid, r.name,
                        rg.gid, rg.name, COALESCE(rgt.name, 'Unknown'),
                        a.gid, a.name, WIKITOKEN(artist_u.url)
        FROM raw_l_release_url lru
            JOIN raw_url u ON lru.entity1 = u.id
            JOIN raw_release r ON lru.entity0 = r.id
            JOIN raw_medium m ON r.id = m.release
            JOIN raw_track t ON t.medium = m.id
            JOIN raw_release_group rg ON r.release_group = rg.id
            LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
            JOIN raw_artist a ON rg.artist_credit = a.id
            LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0
            LEFT JOIN raw_url artist_u ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata.org%';
    """)
    conn.commit()
    log("✅ Link cache complete!")

    log("🔤 Creating un-cleaned staging table for text lookups...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tmp_text_staging (
            t_name TEXT, r_name TEXT, a_name TEXT, length INT,
            r_gid TEXT, rg_gid TEXT, rg_name TEXT, rgt_name TEXT,
            a_gid TEXT, artist_url TEXT
        );
    """)

    log("🔗 Executing text lookup joins...")
    cursor.execute("""
        INSERT INTO tmp_text_staging
        SELECT DISTINCT t.name, r.name, a.name, COALESCE(t.length, 0),
                        r.gid, rg.gid, rg.name, COALESCE(rgt.name, 'Unknown'),
                        a.gid, artist_u.url
        FROM raw_track t
            JOIN raw_medium m ON t.medium = m.id
            JOIN raw_release r ON m.release = r.id
            JOIN raw_release_group rg ON r.release_group = rg.id
            LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
            JOIN raw_artist a ON rg.artist_credit = a.id
            LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0
            LEFT JOIN raw_url artist_u ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata.org%';
    """)
    conn.commit()

    log("🔤 Processing string cleaning and migrating to final table in managed batches...")
    cursor.execute("SELECT * FROM tmp_text_staging;")

    batch = []
    batch_size = 250000
    total_inserted = 0
    insert_sql = "INSERT OR IGNORE INTO text_canonical_lookup VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);"

    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break

        for row in rows:
            t_name, r_name, a_name, length, r_gid, rg_gid, rg_name, rgt_name, a_gid, artist_url = row

            clean_t = clean_string(t_name)
            clean_r = clean_string(r_name)
            clean_a = clean_string(a_name)
            wiki_tok = extract_wikidata_token(artist_url)

            batch.append((
                clean_t, clean_r, clean_a, t_name, length, r_gid,
                r_name, rg_gid, rg_name, rgt_name, a_gid, a_name, wiki_tok
            ))

        if len(batch) >= batch_size:
            conn.cursor().executemany(insert_sql, batch)
            conn.commit()
            total_inserted += len(batch)
            log(f"   -> Text Lookup Map: Processed {total_inserted:,} rows...")
            batch = []

    if batch:
        conn.cursor().executemany(insert_sql, batch)
        conn.commit()
        total_inserted += len(batch)

    conn.cursor().execute("DROP TABLE IF EXISTS tmp_text_staging;")
    conn.commit()
    log(f"✅ Text cache complete! Total uniquely mapped records: {total_inserted:,
