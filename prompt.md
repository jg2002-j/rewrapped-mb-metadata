im trying to create a github workflow (free limits) that grabs the musicbrainz data dump and extracts certain columns from it, puts it in sqlite and compresses it again for download by another service. it must work on github workflow, but be as optimised as possible within those constraints. i get the following error:

```
PS C:\Users\Jai\code\rewrapped-mb-metadata> python scripts/generate_metadata_db.py --prod
[23:21:19] 🚀 Starting decoupled, named-schema DuckDB generation engine...
[23:21:19] Initializing target SQLite asset container: metadata_core.db
[23:21:19] Initializing DuckDB engine for GitHub Actions environment...
[23:21:19] 📦 Unpacking and processing data archive: mbdump.tar.bz2
[23:21:20] ▶️ Spooling & Extracting: mbdump/artist -> raw_artist
[23:21:35]    ✅ Memory-optimized load complete for raw_artist.
[23:21:46] ▶️ Spooling & Extracting: mbdump/artist_credit_name -> raw_artist_credit_name
[23:21:54]    ✅ Memory-optimized load complete for raw_artist_credit_name.
[23:22:22] ▶️ Spooling & Extracting: mbdump/l_artist_url -> raw_l_artist_url
[23:22:33]    ✅ Memory-optimized load complete for raw_l_artist_url.
[23:22:42] ▶️ Spooling & Extracting: mbdump/l_recording_url -> raw_l_recording_url
[23:22:47]    ✅ Memory-optimized load complete for raw_l_recording_url.
[23:22:57] ▶️ Spooling & Extracting: mbdump/l_release_url -> raw_l_release_url
[23:23:13]    ✅ Memory-optimized load complete for raw_l_release_url.
[23:23:16] ▶️ Spooling & Extracting: mbdump/medium -> raw_medium
[23:23:36]    ✅ Memory-optimized load complete for raw_medium.
[23:23:37] ▶️ Spooling & Extracting: mbdump/recording -> raw_recording
[23:26:07]    ✅ Memory-optimized load complete for raw_recording.
[23:26:17] ▶️ Spooling & Extracting: mbdump/release -> raw_release
[23:26:49]    ✅ Memory-optimized load complete for raw_release.
[23:26:53] ▶️ Spooling & Extracting: mbdump/release_group -> raw_release_group
[23:27:11]    ✅ Memory-optimized load complete for raw_release_group.
[23:27:11] ▶️ Spooling & Extracting: mbdump/release_group_primary_type -> raw_rg_type
[23:27:11]    ✅ Memory-optimized load complete for raw_rg_type.
[23:27:17] ▶️ Spooling & Extracting: mbdump/track -> raw_track
[23:31:45]    ✅ Memory-optimized load complete for raw_track.
[23:31:47] ▶️ Spooling & Extracting: mbdump/url -> raw_url
[23:33:06]    ✅ Memory-optimized load complete for raw_url.
[23:33:15] 📋 Validating ingested DuckDB record volume...
[23:33:15]    ✅ raw_artist: Passed (2,887,383 records)
[23:33:15]    ✅ raw_track: Passed (56,094,602 records)
[23:33:15]    ✅ raw_recording: Passed (39,003,620 records)
[23:33:15] ⚡ Precomputing filter tables to shrink massive joins...
[23:33:21] 🔗 Processing Link Canonical Cache natively in DuckDB...
[23:33:48] 🧹 Materializing Regex-cleaned string vectors...
[23:35:03] 🔗 Processing Text Canonical Cache via sequential vector join...
[23:35:27] ❌ Pipeline Failure: Out of Memory Error: failed to allocate data of size 8.0 KiB (3.7 GiB/3.7 GiB used)

Possible solutions:
* Reducing the number of threads (SET threads=X)
* Disabling insertion-order preservation (SET preserve_insertion_order=false)
* Increasing the memory limit (SET memory_limit='...GB')

See also https://duckdb.org/docs/stable/guides/performance/how_to_tune_workloads
```

.github/workflows/musicbrainz-sync.yml
```yaml
name: musicbrainz_metadata

on:
  schedule:
    - cron: '33 3 * * 3'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build-metadata:
    runs-on: ubuntu-latest

    steps:
      - name: Maximize Runner Disk Space
        uses: easimon/maximize-build-space@v10
        with:
          root-reserve-mb: 3072
          swap-size-mb: 1024
          remove-dotnet: 'true'
          remove-android: 'true'
          remove-haskell: 'true'
          remove-codeql: 'true'
          remove-docker-images: 'true'

      # Freeing up secondary tool dependencies for immediate workspace swapping
      - name: Purge Tool Framework Overheads
        run: |
          sudo rm -rf /usr/share/dotnet
          sudo rm -rf /usr/local/lib/android
          sudo rm -rf /opt/ghc

      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Dependencies
        run: |
          pip install duckdb

      - name: Download MusicBrainz Cloud Dumps
        run: |
          echo "Resolving the latest weekly export directory name..."
          LATEST_FOLDER=$(curl -sL "https://data.musicbrainz.org/pub/musicbrainz/data/fullexport/LATEST")
          echo "Targeting active timestamp folder: $LATEST_FOLDER"
          
          echo "Streaming data packages into workspace..."
          curl -SLf "https://ftp.musicbrainz.org/pub/musicbrainz/data/fullexport/${LATEST_FOLDER}/mbdump.tar.bz2" \
            -o mbdump.tar.bz2
          
          echo "Downloads completed successfully."

      - name: Run Extraction and Flattening Script
        run: |
          python -u scripts/generate_metadata_db.py

      - name: Compress Database Asset
        run: |
          echo "Compressing SQLite binary..."
          tar -czf metadata_core.tar.gz metadata_core.db

      - name: Get Current Date Tag
        id: date
        run: echo "tag=v$(date +'%Y.%m.%d')" >> $GITHUB_OUTPUT

      - name: Create GitHub Release and Upload Asset
        uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ steps.date.outputs.tag }}
          name: MusicBrainz Metadata Release ${{ steps.date.outputs.tag }}
          body: |
            Automated snapshot of the flattened MusicBrainz lookup database.
            Includes Release Group MBIDs (Cover Art Archive) and Artist Wikidata IDs.
          draft: false
          prerelease: false
          files: |
            ${{ github.workspace }}/metadata_core.tar.gz
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

scripts/generate_metadata_db.py
```python
import duckdb
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime

from queries import (
    PRECOMPUTE_FILTERS_SQL,
    BUILD_LINK_LOOKUP_SQL,
    MATERIALIZE_CLEAN_STRINGS_SQL,
    BUILD_TEXT_LOOKUP_SQL
)
from schema import TABLE_SCHEMAS, TABLE_MAPPING

DB_NAME = "metadata_core.db"
DUCKDB_TMP = "duckdb_working.db"
TEMP_TSV = "temp_extract.tsv"

if os.environ.get("GITHUB_ACTIONS") == "true" or "--prod" in sys.argv:
    TAR_FILES = ["mbdump.tar.bz2"]
elif os.path.exists("test_mbdump.tar.bz2"):
    TAR_FILES = ["test_mbdump.tar.bz2"]
else:
    TAR_FILES = ["mbdump.tar.bz2"]


def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


def init_duckdb():
    log("Initializing DuckDB engine for GitHub Actions environment...")

    # Ensure a clean slate
    for suffix in ["", ".tmp", ".wal"]:
        path = f"{DUCKDB_TMP}{suffix}"
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)

    if os.path.exists(".duckdb_tmp"):
        shutil.rmtree(".duckdb_tmp", ignore_errors=True)

    # Inject config directly during connection to prevent ghost .tmp files
    con = duckdb.connect(DUCKDB_TMP, config={'temp_directory': '.duckdb_tmp'})

    # 4GB leaves ~3GB for OS and Python overhead on free tier
    con.execute("SET memory_limit='4GB';")
    con.execute("SET threads=2;")
    con.execute("SET preserve_insertion_order=false;")

    return con


def init_target_sqlite():
    log(f"Initializing target SQLite asset container: {DB_NAME}")
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Maximize write speed during sequential insertions
    cursor.execute("PRAGMA journal_mode = OFF;")
    cursor.execute("PRAGMA synchronous = 0;")
    cursor.execute("PRAGMA cache_size = 100000;")

    cursor.execute("""
                   CREATE TABLE link_canonical_lookup
                   (
                       streaming_link      TEXT PRIMARY KEY,
                       track_title         TEXT    NOT NULL,
                       duration_ms         INTEGER NOT NULL,
                       recording_mbid      TEXT    NOT NULL,
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
                       recording_mbid      TEXT    NOT NULL,
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
    conn.commit()
    conn.close()


def stream_tar_to_duckdb(con):
    if not TAR_FILES:
        raise FileNotFoundError("Critical Error! No .tar.bz2 data archives found in the workspace.")

    for tar_name in TAR_FILES:
        log(f"📦 Unpacking and processing data archive: {tar_name}")
        with tarfile.open(tar_name, "r:bz2") as tar:
            for member in tar:
                clean_name = member.name.lstrip("./").strip()

                if clean_name in TABLE_MAPPING:
                    target_table = TABLE_MAPPING[clean_name]
                    columns = TABLE_SCHEMAS[clean_name]

                    log(f"▶️ Spooling & Extracting: {member.name} -> {target_table}")

                    with tar.extractfile(member) as source, open(TEMP_TSV, "wb") as target:
                        shutil.copyfileobj(source, target)

                    types_dict = {}
                    for col in columns:
                        if col == "id" or col.endswith("_credit") or col in ["release", "medium", "artist", "entity0",
                                                                             "entity1", "recording"]:
                            types_dict[col] = "INTEGER"
                        else:
                            types_dict[col] = "VARCHAR"

                    types_sql = "{" + ", ".join([f"'{k}': '{v}'" for k, v in types_dict.items()]) + "}"

                    con.execute(f"""
                        CREATE TABLE {target_table} AS 
                        SELECT * FROM read_csv('{TEMP_TSV}', 
                                               header=False, 
                                               delim='\t', 
                                               quote='', 
                                               escape='', 
                                               nullstr='\\N', 
                                               names={columns},
                                               types={types_sql},
                                               null_padding=True,
                                               strict_mode=False);
                    """)

                    os.remove(TEMP_TSV)
                    log(f"   ✅ Memory-optimized load complete for {target_table}.")


def verify_extracted_counts(con):
    log("📋 Validating ingested DuckDB record volume...")
    is_test_env = any("test" in f for f in TAR_FILES)

    baselines = {
        "raw_artist": 10 if is_test_env else 2800000,
        "raw_track": 10 if is_test_env else 55000000,
        "raw_recording": 10 if is_test_env else 35000000,
    }

    for table_name, expected_minimum in baselines.items():
        exists = con.execute(f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}';").fetchone()
        if not exists and not is_test_env:
            raise AssertionError(f"Critical Error! Table '{table_name}' was not processed.")

        if exists:
            actual_count = con.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()[0]
            if actual_count < expected_minimum:
                raise AssertionError(f"Leakage! {table_name} has {actual_count:,} rows. Expected {expected_minimum:,}.")
            log(f"   ✅ {table_name}: Passed ({actual_count:,} records)")


def execute_analytics_and_export(con):
    log("⚡ Precomputing filter tables to shrink massive joins...")
    for query in PRECOMPUTE_FILTERS_SQL:
        con.execute(query)

    log("🔗 Processing Link Canonical Cache natively in DuckDB...")
    con.execute(BUILD_LINK_LOOKUP_SQL)

    log("🧹 Materializing Regex-cleaned string vectors...")
    for query in MATERIALIZE_CLEAN_STRINGS_SQL:
        con.execute(query)

    log("🔗 Processing Text Canonical Cache via sequential vector join...")
    con.execute(BUILD_TEXT_LOOKUP_SQL)

    # CRITICAL SPACE CLEANUP: Evict raw source data from memory/disk before generating SQLite asset
    log("🧹 Evicting raw staging tables to reclaim workspace disk space...")
    raw_tables = [
        "raw_track", "raw_recording", "raw_medium", "raw_release", "raw_release_group",
        "raw_artist", "raw_artist_credit_name", "raw_url", "raw_l_recording_url",
        "raw_l_artist_url", "raw_l_release_url", "raw_rg_type", "clean_tracks",
        "clean_releases", "clean_artists", "target_urls", "streaming_links", "wikidata_mapping"
    ]
    for table in raw_tables:
        con.execute(f"DROP TABLE IF EXISTS {table};")

    log("🔌 Attaching targeted SQLite container to DuckDB environment...")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB_NAME}' AS sqlite_db (TYPE SQLITE);")

    log("🚚 Streaming finalized structures directly to SQLite container...")
    con.execute("INSERT INTO sqlite_db.link_canonical_lookup SELECT * FROM duck_link_lookup;")
    con.execute("INSERT INTO sqlite_db.text_canonical_lookup SELECT * FROM duck_text_lookup;")

    con.execute("DETACH sqlite_db;")
    log("🎉 Relational consolidation processing complete.")


def optimize_final_sqlite():
    log("🗂️ Generating structured performance search indexes on SQLite...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_link_search ON link_canonical_lookup(streaming_link);")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_text_search ON text_canonical_lookup(clean_track, clean_album, clean_artist);")

    # We omit VACUUM to prevent duplicate disk consumption on the free-tier runner
    cursor.execute("PRAGMA journal_mode = DELETE;")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    log("🚀 Starting decoupled, named-schema DuckDB generation engine...")
    init_target_sqlite()
    db_con = init_duckdb()

    try:
        stream_tar_to_duckdb(db_con)
        verify_extracted_counts(db_con)
        execute_analytics_and_export(db_con)
        optimize_final_sqlite()
        log("🎉 Asset compilation completed successfully!")
    except Exception as e:
        log(f"❌ Pipeline Failure: {str(e)}")
        sys.exit(1)
    finally:
        db_con.close()
        for suffix in ["", ".tmp", ".wal"]:
            path = f"{DUCKDB_TMP}{suffix}"
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
        if os.path.exists(".duckdb_tmp"):
            shutil.rmtree(".duckdb_tmp", ignore_errors=True)
```

scripts/queries.py
```python
# Compilation Analytic Queries Using Pure DuckDB Syntax

DUCKDB_CLEAN_STR = r"lower(regexp_replace(regexp_replace(trim(COL), '[^\w\s]', '', 'g'), '\s+', ' ', 'g'))"

# We isolate heavy wildcards and subqueries BEFORE the main joins
PRECOMPUTE_FILTERS_SQL = [
    """
    CREATE TABLE target_urls AS 
    SELECT id, url FROM raw_url 
    WHERE url LIKE '%spotify/%' OR url LIKE '%apple%' OR url LIKE '%tidal%';
    """,
    """
    CREATE TABLE streaming_links AS
    SELECT lru.entity0 AS recording_id, tu.url AS streaming_link
    FROM raw_l_recording_url lru
    JOIN target_urls tu ON lru.entity1 = tu.id;
    """,
    """
    CREATE TABLE wikidata_mapping AS
    SELECT lau.entity0 AS artist_id, regexp_extract(u.url, 'wikidata\\.org/wiki/(Q\\d+)', 1) AS wikidata_id
    FROM raw_l_artist_url lau
             JOIN raw_url u ON lau.entity1 = u.id
    WHERE u.url LIKE '%wikidata%';
    """
]

# Drastically simpler now that URLs are pre-filtered and Wikidata is extracted
BUILD_LINK_LOOKUP_SQL = """
                        CREATE TABLE duck_link_lookup AS
                        WITH ranked_links AS (SELECT sl.streaming_link,
                                                     t.name                                     AS track_title,
                                                     COALESCE(TRY_CAST(t.length AS INTEGER), 0) AS duration_ms,
                                                     rec.gid                                    AS recording_mbid,
                                                     r.gid                                      AS album_mbid,
                                                     r.name                                     AS album_title,
                                                     rg.gid                                     AS release_group_mbid,
                                                     rg.name                                    AS release_group_title,
                                                     COALESCE(rgt.name, 'Unknown')              AS release_group_type,
                                                     a.gid                                      AS artist_mbid,
                                                     a.name                                     AS artist_name,
                                                     wm.wikidata_id                             AS artist_wikidata_id,
                                                     ROW_NUMBER() OVER (
                                                         PARTITION BY sl.streaming_link
                                                         ORDER BY CASE COALESCE(rgt.name, 'Unknown')
                                                                      WHEN 'Album' THEN 1
                                                                      WHEN 'EP' THEN 2
                                                                      WHEN 'Single' THEN 3
                                                                      ELSE 4
                                                             END, t.id ASC
                                                         )                                      as rn
                                              FROM streaming_links sl
                                                       JOIN raw_recording rec ON sl.recording_id = rec.id
                                                       JOIN raw_track t ON t.recording = rec.id
                                                       JOIN raw_medium m ON t.medium = m.id
                                                       JOIN raw_release r ON m.release = r.id
                                                       JOIN raw_release_group rg ON r.release_group = rg.id
                                                       LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
                                                       JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
                                                       JOIN raw_artist a ON acn.artist = a.id
                                                       LEFT JOIN wikidata_mapping wm ON a.id = wm.artist_id)
                        SELECT * EXCLUDE (rn)
                        FROM ranked_links
                        WHERE rn = 1; \
                        """

# Isolates CPU-heavy regex tasks to a flat vector pass before jumping into 5-table deep joins
MATERIALIZE_CLEAN_STRINGS_SQL = [
    f"""
    CREATE TABLE clean_artists AS
    SELECT id, gid as artist_mbid, name as artist_name, {DUCKDB_CLEAN_STR.replace('COL', 'name')} AS clean_artist
    FROM raw_artist;
    """,
    f"""
    CREATE TABLE clean_releases AS
    SELECT id, release_group, gid as album_mbid, name as album_title, {DUCKDB_CLEAN_STR.replace('COL', 'name')} AS clean_album
    FROM raw_release;
    """,
    f"""
    CREATE TABLE clean_tracks AS
    SELECT id, recording, medium, name as track_title, COALESCE(TRY_CAST(length AS INTEGER), 0) AS duration_ms, {DUCKDB_CLEAN_STR.replace('COL', 'name')} AS clean_track
    FROM raw_track;
    """
]

# Execution via full out-of-core streaming window function, replacing heavy Hash Aggregates
BUILD_TEXT_LOOKUP_SQL = """
                        CREATE TABLE duck_text_lookup AS
                        SELECT ct.clean_track,
                               cr.clean_album,
                               ca.clean_artist,
                               ct.track_title,
                               ct.duration_ms,
                               rec.gid                       AS recording_mbid,
                               cr.album_mbid                 AS album_mbid,
                               cr.album_title                AS album_title,
                               rg.gid                        AS release_group_mbid,
                               rg.name                       AS release_group_title,
                               COALESCE(rgt.name, 'Unknown') AS release_group_type,
                               ca.artist_mbid                AS artist_mbid,
                               ca.artist_name                AS artist_name,
                               wm.wikidata_id                AS artist_wikidata_id
                        FROM clean_tracks ct
                                 JOIN raw_recording rec ON ct.recording = rec.id
                                 JOIN raw_medium m ON ct.medium = m.id
                                 JOIN clean_releases cr ON m.release = cr.id
                                 JOIN raw_release_group rg ON cr.release_group = rg.id
                                 LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
                                 JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
                                 JOIN clean_artists ca ON acn.artist = ca.id
                                 LEFT JOIN wikidata_mapping wm ON ca.id = wm.artist_id QUALIFY ROW_NUMBER() OVER (
                            PARTITION BY ct.clean_track, cr.clean_album, ca.clean_artist
                            ORDER BY ct.track_title ASC
                        ) = 1; \
                        """
```

scripts/schema.py
```python
# MusicBrainz Core Column Schema Blueprint Definitions
# Audited and verified against admin/sql/CreateTables.sql

TABLE_SCHEMAS = {
    "mbdump/artist": [
        "id", "gid", "name", "sort_name", "begin_date_year", "begin_date_month",
        "begin_date_day", "end_date_year", "end_date_month", "end_date_day",
        "type", "area", "gender", "comment", "edits_pending", "last_updated",
        "ended", "begin_area", "end_area"
    ],
    "mbdump/artist_credit_name": [
        "artist_credit", "position", "artist", "name", "join_phrase"
    ],
    "mbdump/release": [
        "id", "gid", "name", "artist_credit", "release_group", "status",
        "packaging", "language", "script", "barcode", "comment",
        "edits_pending", "quality", "last_updated"
    ],
    "mbdump/release_group": [
        "id", "gid", "name", "artist_credit", "type", "comment",
        "edits_pending", "last_updated"
    ],
    "mbdump/release_group_primary_type": [
        "id", "name", "parent", "child_order", "description", "gid"
    ],
    "mbdump/medium": [
        "id", "release", "position", "format", "name", "track_count",
        "edits_pending", "last_updated"
    ],
    "mbdump/track": [
        "id", "gid", "recording", "medium", "position", "number", "name",
        "length", "edits_pending", "last_updated", "is_data_track"
    ],
    "mbdump/recording": [
        "id", "gid", "name", "artist_credit", "length", "comment",
        "edits_pending", "last_updated", "video"
    ],
    "mbdump/url": [
        "id", "gid", "url", "description", "edits_pending", "last_updated"
    ],
    "mbdump/l_release_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "mbdump/l_recording_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "mbdump/l_artist_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "l_artist_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ]
}

# Ingestion Table Target Aliases
TABLE_MAPPING = {
    "mbdump/artist": "raw_artist",
    "mbdump/artist_credit_name": "raw_artist_credit_name",
    "mbdump/release": "raw_release",
    "mbdump/release_group": "raw_release_group",
    "mbdump/release_group_primary_type": "raw_rg_type",
    "mbdump/medium": "raw_medium",
    "mbdump/track": "raw_track",
    "mbdump/recording": "raw_recording",
    "mbdump/url": "raw_url",
    "mbdump/l_release_url": "raw_l_release_url",
    "mbdump/l_recording_url": "raw_l_recording_url",
    "mbdump/l_artist_url": "raw_l_artist_url",
    "l_artist_url": "raw_l_artist_url"
}
```