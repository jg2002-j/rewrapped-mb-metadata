import os
import sys
import shutil
import sqlite3
import tarfile
from datetime import datetime
import duckdb

# Import multi-file configuration assets
from schema import TABLE_SCHEMAS, TABLE_MAPPING
from queries import BUILD_LINK_LOOKUP_SQL, BUILD_TEXT_LOOKUP_SQL

DB_NAME = "metadata_core.db"
DUCKDB_TMP = "duckdb_working.db"
TEMP_TSV = "temp_extract.tsv"
TAR_FILES = ["mbdump.tar.bz2", "mbdump-derived.tar.bz2"]

def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")

def init_duckdb():
    log("Initializing DuckDB execution engine...")
    if os.path.exists(DUCKDB_TMP):
        os.remove(DUCKDB_TMP)
    con = duckdb.connect(DUCKDB_TMP)
    con.execute("SET max_memory='5GB';")
    con.execute("SET preserve_insertion_order=false;")
    return con

def init_target_sqlite():
    log(f"Initializing target SQLite asset container: {DB_NAME}")
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
            log(f"⚠️ Archive {tar_name} missing. Skipping package...")
            continue

        log(f"📦 Unpacking and processing data archive: {tar_name}")
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                if member.name in TABLE_MAPPING:
                    target_table = TABLE_MAPPING[member.name]
                    columns = TABLE_SCHEMAS[member.name]

                    log(f"▶️ Spooling & Extracting Named Schema: {member.name} -> {target_table}")

                    with tar.extractfile(member) as source, open(TEMP_TSV, "wb") as target:
                        shutil.copyfileobj(source, target)

                    # Direct native compilation utilizing our strict naming arrays
                    con.execute(f"""
                        CREATE TABLE {target_table} AS 
                        SELECT * FROM read_csv('{TEMP_TSV}', 
                                               header=False, 
                                               delim='\t', 
                                               quote='', 
                                               escape='', 
                                               nullstr='\\N', 
                                               names={columns},
                                               all_varchar=True);
                    """)

                    os.remove(TEMP_TSV)
                    log(f"   ✅ Successfully mapped and loaded {target_table}.")

def execute_analytics_and_export(con):
    log("🔌 Attaching deployment SQLite container to DuckDB environment...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB_NAME}' AS sqlite_db (TYPE SQLITE);")

    log("🔗 Processing Link Canonical Cache (Grouping Primary Keys)...")
    con.execute(BUILD_LINK_LOOKUP_SQL)

    log("🔗 Processing Text Canonical Cache (Grouping Composite Keys)...")
    con.execute(BUILD_TEXT_LOOKUP_SQL)

    con.execute("DETACH sqlite_db;")
    log("🎉 Relational consolidation processing complete.")

def optimize_final_sqlite():
    log("🗂️ Generating structured performance search indexes on SQLite production container...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_link_search ON link_canonical_lookup(streaming_link);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_text_search ON text_canonical_lookup(clean_track, clean_album, clean_artist);")
    cursor.execute("VACUUM;")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    log("🚀 Starting decoupled, named-schema DuckDB generation engine...")
    init_target_sqlite()
    db_con = init_duckdb()

    try:
        stream_tar_to_duckdb(db_con)
        execute_analytics_and_export(db_con)
        optimize_final_sqlite()
        log("🎉 Asset compilation completed successfully!")
    except Exception as e:
        log(f"❌ Pipeline Failure: {str(e)}")
        sys.exit(1)
    finally:
        db_con.close()
        if os.path.exists(DUCKDB_TMP):
            os.remove(DUCKDB_TMP)