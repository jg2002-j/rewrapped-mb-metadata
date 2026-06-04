import os
import re
import sqlite3
import tarfile
from datetime import datetime

DB_NAME = "metadata_core.db"
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

COLUMN_MAP = {
    "raw_artist": [0, 1, 2],  # id, gid, name
    "raw_artist_credit_name": [0, 2],  # artist_credit, artist
    "raw_release": [0, 1, 2, 4],  # id, gid, name, release_group
    "raw_release_group": [0, 1, 2, 3, 4],  # id, gid, name, artist_credit, type
    "raw_rg_type": [0, 1],  # id, name
    "raw_medium": [0, 1],  # id, release
    "raw_track": [0, 1, 6, 3, 8],  # id, gid, name, medium, length
    "raw_url": [0, 1, 2],  # id, gid, url
    "raw_l_release_url": [0, 2, 3],  # id, entity0, entity1
    "raw_l_artist_url": [0, 2, 3]  # id, entity0, entity1
}

TARGET_DOMAINS = ["wikidata.org", "spotify.com", "spotify:", "apple.com", "tidal.com", "tidalhifi.com"]


def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


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


def init_database():
    log(f"Initializing database: {DB_NAME}")
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # CRITICAL: Use FILE for temp_store to prevent OOM RAM crashes
    cursor.execute("PRAGMA synchronous = OFF;")
    cursor.execute("PRAGMA journal_mode = OFF;")
    cursor.execute("PRAGMA cache_size = -3000000;")  # Keep cache safe around ~3GB RAM
    cursor.execute("PRAGMA temp_store = FILE;")  # Spill sorting structures to SSD

    cursor.execute("""
                   CREATE TABLE link_canonical_lookup
                   (
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
                   CREATE TABLE text_canonical_lookup
                   (
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

    # Schemas adjusted to store pre-cleaned strings natively
    cursor.execute("CREATE TABLE raw_artist (id INTEGER, gid TEXT, name TEXT, clean_name TEXT);")
    cursor.execute("CREATE TABLE raw_artist_credit_name (artist_credit INTEGER, artist INTEGER);")
    cursor.execute(
        "CREATE TABLE raw_release (id INTEGER, gid TEXT, name TEXT, release_group INTEGER, clean_name TEXT);")
    cursor.execute(
        "CREATE TABLE raw_release_group (id INTEGER, gid TEXT, name TEXT, artist_credit INTEGER, type INTEGER);")
    cursor.execute("CREATE TABLE raw_rg_type (id INTEGER, name TEXT);")
    cursor.execute("CREATE TABLE raw_medium (id INTEGER, release INTEGER);")
    cursor.execute(
        "CREATE TABLE raw_track (id INTEGER, gid TEXT, name TEXT, medium INTEGER, length INTEGER, clean_name TEXT);")
    cursor.execute("CREATE TABLE raw_url (id INTEGER, gid TEXT, url TEXT, wiki_token TEXT);")
    cursor.execute("CREATE TABLE raw_l_release_url (id INTEGER, entity0 INTEGER, entity1 INTEGER);")
    cursor.execute("CREATE TABLE raw_l_artist_url (id INTEGER, entity0 INTEGER, entity1 INTEGER);")

    conn.commit()
    return conn


def stream_file_into_sqlite(cursor, file_stream, table_name):
    batch = []
    batch_size = 250000
    rows_processed = 0

    target_indices = COLUMN_MAP[table_name]
    max_index = max(target_indices)

    extra_fields = 1 if table_name in ["raw_artist", "raw_release", "raw_track", "raw_url"] else 0
    placeholders = ",".join(["?"] * (len(target_indices) + extra_fields))
    insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders});"

    # Optimization: Process entire file inside a single explicit transaction block
    cursor.execute("BEGIN TRANSACTION;")

    for line in file_stream:
        row_str = line.decode('utf-8', errors='ignore').rstrip('\n')
        columns = row_str.split('\t')

        if len(columns) <= max_index:
            columns += ['\\N'] * ((max_index + 1) - len(columns))

        sliced_columns = [None if col == '\\N' or col == '' else col for col in [columns[i] for i in target_indices]]

        if table_name == "raw_url":
            url_str = sliced_columns[2] or ""
            if not any(domain in url_str for domain in TARGET_DOMAINS):
                continue
            sliced_columns.append(extract_wikidata_token(url_str))
        elif table_name in ["raw_artist", "raw_release", "raw_track"]:
            sliced_columns.append(clean_string(sliced_columns[2]))

        batch.append(sliced_columns)
        rows_processed += 1

        if len(batch) >= batch_size:
            cursor.executemany(insert_sql, batch)
            batch = []

        if rows_processed % 1000000 == 0:
            log(f"   -> {table_name}: Parsed {rows_processed:,} source rows...")

    if batch:
        cursor.executemany(insert_sql, batch)

    cursor.connection.commit()
    log(f"✅ Completed {table_name}. Total records saved: {rows_processed:,}")


def stream_tar_and_populate_raw(conn):
    cursor = conn.cursor()
    for tar_name in TAR_FILES:
        if not os.path.exists(tar_name):
            log(f"⚠️ Warning: Archive {tar_name} missing. Skipping...")
            continue

        log(f"📦 Streaming compressed data archive: {tar_name}")
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                if member.name in TRACKED_FILES:
                    target_table = TRACKED_FILES[member.name]
                    log(f"▶️ Extracting stream: {member.name} -> {target_table}")
                    file_stream = tar.extractfile(member)
                    if file_stream:
                        stream_file_into_sqlite(cursor, file_stream, target_table)


def execute_flattening_joins(conn):
    cursor = conn.cursor()

    log("🗂️ Generating optimized covering indexes on intermediate relational schemas...")
    cursor.execute("CREATE INDEX idx_t_med ON raw_track(medium, name, length, clean_name);")
    cursor.execute("CREATE INDEX idx_m_rel ON raw_medium(release, id);")
    cursor.execute("CREATE INDEX idx_r_id ON raw_release(id, release_group, name, gid, clean_name);")
    cursor.execute("CREATE INDEX idx_rg_id ON raw_release_group(id, artist_credit, type, name, gid);")
    cursor.execute("CREATE INDEX idx_rgt_id ON raw_rg_type(id, name);")
    cursor.execute("CREATE INDEX idx_acn_lut ON raw_artist_credit_name(artist_credit, artist);")
    cursor.execute("CREATE INDEX idx_a_id ON raw_artist(id, gid, name, clean_name);")
    cursor.execute("CREATE INDEX idx_lru_map ON raw_l_release_url(entity0, entity1);")
    cursor.execute("CREATE INDEX idx_lau_map ON raw_l_artist_url(entity0, entity1);")
    cursor.execute("CREATE INDEX idx_u_id ON raw_url(id, url, wiki_token);")
    conn.commit()

    log("🔗 Compiling Link Canonical Cache (Pure native C execution)...")
    cursor.execute("""
                   INSERT OR IGNORE INTO link_canonical_lookup
                   SELECT DISTINCT u.url,
                                   t.name,
                                   COALESCE(t.length, 0),
                                   r.gid,
                                   r.name,
                                   rg.gid,
                                   rg.name,
                                   COALESCE(rgt.name, 'Unknown'),
                                   a.gid,
                                   a.name,
                                   artist_u.wiki_token
                   FROM raw_l_release_url lru
                            JOIN raw_url u ON lru.entity1 = u.id
                            JOIN raw_release r ON lru.entity0 = r.id
                            JOIN raw_medium m ON r.id = m.release
                            JOIN raw_track t ON t.medium = m.id
                            JOIN raw_release_group rg ON r.release_group = rg.id
                            LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
                            JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
                            JOIN raw_artist a ON acn.artist = a.id
                            LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0
                            LEFT JOIN raw_url artist_u
                                      ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata.org%';
                   """)
    conn.commit()

    log("🔗 Compiling Text Canonical Cache (Pure native C execution)...")
    cursor.execute("""
                   INSERT OR IGNORE INTO text_canonical_lookup
                   SELECT DISTINCT t.clean_name,
                                   r.clean_name,
                                   a.clean_name,
                                   t.name,
                                   COALESCE(t.length, 0),
                                   r.gid,
                                   r.name,
                                   rg.gid,
                                   rg.name,
                                   COALESCE(rgt.name, 'Unknown'),
                                   a.gid,
                                   a.name,
                                   artist_u.wiki_token
                   FROM raw_track t
                            JOIN raw_medium m ON t.medium = m.id
                            JOIN raw_release r ON m.release = r.id
                            JOIN raw_release_group rg ON r.release_group = rg.id
                            LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
                            JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
                            JOIN raw_artist a ON acn.artist = a.id
                            LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0
                            LEFT JOIN raw_url artist_u
                                      ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata.org%';
                   """)
    conn.commit()


def optimize_and_cleanup(conn):
    cursor = conn.cursor()
    log("🧹 Dropping large temporary relational tables...")
    for raw_table in TRACKED_FILES.values():
        cursor.execute(f"DROP TABLE IF EXISTS {raw_table};")
    conn.commit()

    log("🗂️ Generating structured deployment binary search indexes...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_link_search ON link_canonical_lookup(streaming_link);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_text_search ON text_canonical_lookup(clean_track, clean_album, clean_artist);")

    log("🗜️ Optimizing and vacuuming database storage allocation...")
    cursor.execute("VACUUM;")
    conn.commit()


if __name__ == "__main__":
    import sys

    log("🚀 Starting database generation pipeline...")
    connection = init_database()
    try:
        stream_tar_and_populate_raw(connection)
        execute_flattening_joins(connection)
        optimize_and_cleanup(connection)
        log("🎉 Production ready metadata binary compiled successfully!")
    except Exception as e:
        log(f"❌ Structural Failure during compilation: {str(e)}")
        sys.exit(1)
    finally:
        connection.close()
