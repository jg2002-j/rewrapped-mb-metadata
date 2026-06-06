# Purpose of this Repo:

A database download, as small as possible, and optimised for finding metadata of a track by either:

1. streaming link

2. trackname + albumname + artistname (fallback)

The database download should be as small as possible, since it's downloaded to the user's app during runtime.

The process for creating this database download needs to be as fast as possible, but also work within the constraints of

GitHub actions/workflow.

I want the following things derived from the original Musicbrainz data dump:

- **all streaming links (from spotify, applemusic, tidal) linked to their metadata** - (this should return 1 link to 1

  metadata, some metadata may not have a link associated with them, they should be ignored)

- **all combinations of track + album + artist linked to their metadata** - (this could, before deduplication, be 1 key
  to

  1-many metadata) since one track+album+artist combination could lead to many recordings, i want to prioritise the most

  complete type of release group - deluxe album, then regular album, then EP, then single (or equivalent in Musicbrainz

  terms) - resulting in a 1 track + album + artist to 1 metadata

the metadata for a track should contain at least:

- recording mbid

- track title

- track duration


- album mbid

- album title


- release group mbid

- release group title

- release group type


- artist mbid

- artist name

- artist wikidata id

.github/workflows/musicbrainz-sync.yml

```

name: musicbrainz_metadata


on:

  schedule:

    - cron: "33 3 * * 3"

  workflow_dispatch:


permissions:

  contents: write


jobs:

  build:

    runs-on: ubuntu-latest

    timeout-minutes: 360


    steps:

      - uses: actions/checkout@v4


      - name: Setup Python

        uses: actions/setup-python@v5

        with:

          python-version: "3.11"


      - name: Install deps

        run: |

          pip install --no-cache-dir duckdb


      - name: Download MusicBrainz dump

        run: |

          set -euo pipefail

          latest=$(curl -fsSL https://data.musicbrainz.org/pub/musicbrainz/data/fullexport/LATEST)

          url="https://ftp.musicbrainz.org/pub/musicbrainz/data/fullexport/${latest}/mbdump.tar.bz2"

          curl -L "$url" -o mbdump.tar.bz2


      - name: Build DuckDB staging

        run: |

          python scripts/build_duckdb_stage.py


      - name: Export SQLite

        run: |

          python scripts/export_sqlite.py


      - name: Compress

        run: |

          tar -czf metadata_core.tar.gz metadata_core.db


      - uses: actions/upload-artifact@v4

        with:

          name: metadata

          path: metadata_core.tar.gz

```

scripts/build_duckdb_stage.py

```

import duckdb

import os

import sys

import tarfile


DB = "stage.duckdb"



# ----------------------------

# TAR SELECTION

# ----------------------------

def get_tar_path():

    is_prod = os.environ.get("GITHUB_ACTIONS") == "true" or "--prod" in sys.argv


    if is_prod:

        return "mbdump.tar.bz2"


    if os.path.exists("test_mbdump.tar.bz2"):

        return "test_mbdump.tar.bz2"


    return "mbdump.tar.bz2"



TAR_PATH = get_tar_path()



# ----------------------------

# SCHEMAS (MusicBrainz TSV = fixed order, no headers)

# ----------------------------

URL_COLS = ["id", "gid", "url", "description", "edits_pending", "last_updated"]


TRACK_COLS = [

    "id", "gid", "recording", "medium", "position",

    "number", "name", "length", "edits_pending",

    "last_updated", "is_data_track"

]


RECORDING_COLS = [

    "id", "gid", "name", "artist_credit", "length",

    "comment", "edits_pending", "last_updated", "video"

]


RELEASE_COLS = [

    "id", "gid", "name", "artist_credit", "release_group",

    "status", "packaging", "language", "script",

    "barcode", "comment", "edits_pending",

    "quality", "last_updated"

]


RELEASE_GROUP_COLS = [

    "id", "gid", "name", "artist_credit", "type",

    "comment", "edits_pending", "last_updated"

]


MEDIUM_COLS = [

    "id", "release", "position", "format",

    "name", "track_count", "edits_pending", "last_updated"

]


LR_URL_COLS = [

    "id", "link", "entity0", "entity1",

    "edits_pending", "last_updated"

]



# ----------------------------

# HELPERS

# ----------------------------

def load_tsv(con, tar, member_name, table, cols):

    """

    Stream TSV directly from tar into DuckDB safely.

    """

    member = tar.getmember(member_name)

    f = tar.extractfile(member)


    if not f:

        return


    con.execute(f"""

        CREATE TABLE {table} AS

        SELECT *

        FROM read_csv(

            ?,

            delim='\t',

            header=false,

            columns={cols},

            nullstr='\\N',

            ignore_errors=true

        );

    """, [f])



# ----------------------------

# MAIN

# ----------------------------

def main():

    print("▶ Starting ETL stage build")

    print(f"📦 Using: {TAR_PATH}")


    if os.path.exists(DB):

        os.remove(DB)


    con = duckdb.connect(DB)


    con.execute("SET threads=4;")

    con.execute("SET memory_limit='4GB';")


    # ----------------------------

    # LOAD TAR STREAM

    # ----------------------------

    print("📦 Extracting files...")


    with tarfile.open(TAR_PATH, "r:bz2") as tar:

        print("🧠 Loading tables into DuckDB...")


        load_tsv(con, tar, "mbdump/url", "raw_url", URL_COLS)

        load_tsv(con, tar, "mbdump/track", "raw_track", TRACK_COLS)

        load_tsv(con, tar, "mbdump/recording", "raw_recording", RECORDING_COLS)

        load_tsv(con, tar, "mbdump/release", "raw_release", RELEASE_COLS)

        load_tsv(con, tar, "mbdump/release_group", "raw_release_group", RELEASE_GROUP_COLS)

        load_tsv(con, tar, "mbdump/medium", "raw_medium", MEDIUM_COLS)

        load_tsv(con, tar, "mbdump/l_recording_url", "raw_l_recording_url", LR_URL_COLS)


    # ----------------------------

    # STREAMING LINKS

    # ----------------------------

    print("🔗 Building streaming links...")


    con.execute("""

                CREATE TABLE streaming_links AS

                SELECT

                    l.entity0 AS recording_id,

                    u.url AS streaming_url

                FROM raw_l_recording_url l

                         JOIN raw_url u ON l.entity1 = u.id

                WHERE lower(u.url) LIKE '%spotify%'

                   OR lower(u.url) LIKE '%apple%'

                   OR lower(u.url) LIKE '%tidal%'

                   OR lower(u.url) LIKE '%deezer%'

                   OR lower(u.url) LIKE '%youtube%'

                """)


    # ----------------------------

    # CANONICAL RECORDINGS (BEST RELEASE GROUP)

    # ----------------------------

    print("🎧 Building canonical recordings...")


    con.execute("""

                CREATE TABLE ranked_recordings AS

                WITH base AS (

                    SELECT

                        rec.id AS recording_id,

                        rec.gid AS recording_mbid,

                        t.name AS track_title,

                        COALESCE(t.length, 0) AS duration_ms,


                        r.gid AS album_mbid,

                        r.name AS album_title,


                        rg.gid AS release_group_mbid,

                        rg.name AS release_group_title,


                        COALESCE(rgt.name, 'Other') AS release_group_type,


                        rec.artist_credit AS artist_credit

                    FROM raw_recording rec

                             JOIN raw_track t ON t.recording = rec.id

                             JOIN raw_medium m ON t.medium = m.id

                             JOIN raw_release r ON m.release = r.id

                             JOIN raw_release_group rg ON r.release_group = rg.id

                             LEFT JOIN raw_release_group_primary_type rgt ON rg.type = rgt.id

                )

                SELECT * FROM base

                """)


    # ----------------------------

    # FALLBACK TABLE (FAST SEARCH)

    # ----------------------------

    print("🔎 Building fallback index...")


    con.execute("""

        CREATE TABLE recording_fallback AS

        SELECT DISTINCT

            track_title,

            album_title,

            '' AS artist_name,

            recording_id

        FROM ranked_recordings

    """)


    print("⚡ Creating indexes...")


    con.execute("""

                CREATE INDEX idx_link ON streaming_links(streaming_url);

                """)


    con.close()

    print("✅ DONE")



if __name__ == "__main__":

    main()

```

scripts/export_sqlite.py

```

import duckdb

import sqlite3

import os


DUCK = "stage.duckdb"

SQLITE = "metadata_core.db"

BATCH = 10000



def main():

    if os.path.exists(SQLITE):

        os.remove(SQLITE)


    con = sqlite3.connect(SQLITE)

    cur = con.cursor()


    cur.executescript("""

                      CREATE TABLE recording

                      (

                          recording_id        INTEGER,

                          recording_mbid      TEXT,

                          track_title         TEXT,

                          duration_ms         INTEGER,

                          album_mbid          TEXT,

                          album_title         TEXT,

                          release_group_mbid  TEXT,

                          release_group_title TEXT,

                          release_group_type  TEXT

                      );


                      CREATE TABLE link_lookup

                      (

                          streaming_url TEXT,

                          recording_id  INTEGER

                      );


                      CREATE TABLE recording_fallback

                      (

                          track_title  TEXT,

                          album_title  TEXT,

                          artist_name  TEXT,

                          recording_id INTEGER

                      );

                      """)


    d = duckdb.connect(DUCK)


    # ----------------------------

    # RECORDINGS

    # ----------------------------

    cur.executemany(

        "INSERT INTO recording VALUES (?,?,?,?,?,?,?,?,?)",

        d.execute("""

                  SELECT

                      recording_id,

                      recording_mbid,

                      track_title,

                      duration_ms,

                      album_mbid,

                      album_title,

                      release_group_mbid,

                      release_group_title,

                      release_group_type

                  FROM ranked_recordings

                  """).fetchall()

    )


    # ----------------------------

    # LINKS (STREAMED)

    # ----------------------------

    res = d.execute("SELECT streaming_url, recording_id FROM streaming_links")


    while True:

        rows = res.fetchmany(BATCH)

        if not rows:

            break

        cur.executemany(

            "INSERT INTO link_lookup VALUES (?,?)",

            rows

        )


    # ----------------------------

    # FALLBACK

    # ----------------------------

    cur.executemany(

        "INSERT INTO recording_fallback VALUES (?,?,?,?)",

        d.execute("""

                  SELECT track_title, album_title, artist_name, recording_id

                  FROM recording_fallback

                  """).fetchall()

    )


    con.commit()

    con.close()



if __name__ == "__main__":

    main()

```

test/generate_test_mbdump_files.py

```

import os

import tarfile



def create_mini_test_archive(source_tar, output_tar, target_files):

    print(f"Creating mini test archive: {output_tar}")

    if not os.path.exists(source_tar):

        print(f"Skipping {source_tar} (not found locally)")

        return


    with tarfile.open(source_tar, "r:bz2") as src, tarfile.open(output_tar, "w:bz2") as dst:

        for member in src:

            # Normalize the name to handle leading './' prefixes and hidden trailing whitespace

            clean_name = member.name.lstrip("./").strip()


            if clean_name in target_files:

                print(f"  -> Truncating {clean_name} to 10 rows...")


                f = src.extractfile(member)

                if f is None:

                    continue


                # Safely slice the first 10 binary lines

                lines = [f.readline() for _ in range(10)]

                lines = [l for l in lines if l]  # Filter out empty lines if the file is short


                # Write back into a fresh temporary file

                temp_filename = "temp_test_slice.tsv"

                with open(temp_filename, "wb") as temp_out:

                    temp_out.writelines(lines)


                # Append the truncated file to our new test tarball using the expected clean path layout

                dst.add(temp_filename, arcname=clean_name)

                os.remove(temp_filename)



if __name__ == "__main__":

    mbdump_targets = [

        "mbdump/artist", "mbdump/artist_credit_name", "mbdump/release",

        "mbdump/release_group", "mbdump/release_group_primary_type",

        "mbdump/medium", "mbdump/track", "mbdump/recording", "mbdump/url",

        "mbdump/l_release_url", "mbdump/l_recording_url", "mbdump/l_artist_url",

        "l_artist_url"

    ]


    create_mini_test_archive("mbdump.tar.bz2", "test_mbdump.tar.bz2", mbdump_targets)

    print("Done! Mini test dataset is ready.")

```

test/run_test.py

```

import os

import sqlite3

import subprocess

import sys

from datetime import datetime



# ----------------------------

# LOGGING

# ----------------------------

def log(msg):

    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")



# ----------------------------

# RUN COMMANDS

# ----------------------------

def run(cmd):

    log(f"▶ Running: {cmd}")

    result = subprocess.run(cmd, shell=True)

    if result.returncode != 0:

        log(f"❌ FAILED: {cmd}")

        sys.exit(result.returncode)



def assert_true(cond, msg):

    if not cond:

        raise AssertionError(msg)



# ----------------------------

# DATASET RESOLUTION (SINGLE SOURCE OF TRUTH)

# ----------------------------

def get_tar_path():

    """

    Rules:

    - --prod → mbdump.tar.bz2

    - else → test_mbdump.tar.bz2 if exists

    - else → mbdump.tar.bz2

    """


    prod = "--prod" in sys.argv

    test_path = "../test_mbdump.tar.bz2"

    prod_path = "../mbdump.tar.bz2"


    if prod:

        log("📦 PROD MODE enabled")

        return prod_path


    if os.path.exists(test_path):

        log("🧪 TEST MODE (using test_mbdump)")

        return test_path


    log("⚠️ test_mbdump missing → fallback to full dump")

    return prod_path



# ----------------------------

# SQLITE VALIDATION

# ----------------------------

def validate_sqlite(db_path):

    log("🔍 Validating SQLite output...")


    conn = sqlite3.connect(db_path)

    cur = conn.cursor()


    # ----------------------------

    # Tables exist

    # ----------------------------

    expected_tables = [

        "recording",

        "link_lookup",

        "recording_fallback"

    ]


    for t in expected_tables:

        cur.execute("""

                    SELECT name

                    FROM sqlite_master

                    WHERE type = 'table'

                      AND name = ?

                    """, (t,))

        assert_true(cur.fetchone(), f"Missing table: {t}")


    log("✔ Tables exist")


    # ----------------------------

    # Basic counts

    # ----------------------------

    cur.execute("SELECT COUNT(*) FROM recording")

    recordings = cur.fetchone()[0]

    assert_true(recordings > 0, "No recordings found")


    cur.execute("SELECT COUNT(*) FROM link_lookup")

    links = cur.fetchone()[0]


    cur.execute("SELECT COUNT(*) FROM recording_fallback")

    fallback = cur.fetchone()[0]


    log(f"✔ recordings: {recordings}")

    log(f"✔ links: {links}")

    log(f"✔ fallback: {fallback}")


    # ----------------------------

    # Referential integrity

    # ----------------------------

    if links > 0:

        cur.execute("""

                    SELECT COUNT(*)

                    FROM link_lookup l

                             LEFT JOIN recording r

                                       ON l.recording_id = r.recording_id

                    WHERE r.recording_id IS NULL

                    """)

        orphans = cur.fetchone()[0]

        assert_true(orphans == 0, f"Orphan links found: {orphans}")


    log("✔ link integrity OK")


    # ----------------------------

    # Streaming URL sanity check

    # ----------------------------

    cur.execute("""

                SELECT streaming_url

                FROM link_lookup

                LIMIT 25

                """)

    sample = cur.fetchall()


    for (url,) in sample:

        assert_true(

            any(x in url.lower() for x in [

                "spotify",

                "apple",

                "tidal",

                "deezer",

                "youtube"

            ]),

            f"Invalid streaming URL: {url}"

        )


    log("✔ streaming URL filter OK")


    conn.close()

    log("✅ SQLite validation passed")



# ----------------------------

# MAIN PIPELINE

# ----------------------------

def main():

    log("🧪 Starting ETL test pipeline")


    tar_path = get_tar_path()


    # expose for ETL scripts

    os.environ["MB_DUMP_PATH"] = tar_path


    # ----------------------------

    # STEP 1: DuckDB stage build

    # ----------------------------

    run(f"python scripts/build_duckdb_stage.py {'--prod' if '--prod' in sys.argv else ''}".strip())


    assert_true(os.path.exists("stage.duckdb"), "Missing stage.duckdb")


    # ----------------------------

    # STEP 2: SQLite export

    # ----------------------------

    run("python scripts/export_sqlite.py")


    assert_true(os.path.exists("metadata_core.db"), "Missing metadata_core.db")


    # ----------------------------

    # STEP 3: validate output

    # ----------------------------

    validate_sqlite("metadata_core.db")


    log("🎉 FULL PIPELINE SUCCESS")



if __name__ == "__main__":

    main()

```

mbdump.tar.bz2

test_mbdump.tar.bz2

a) im getting this error:
```
PS C:\Users\Jai\code\rewrapped-mb-metadata> python .\test\run_test.py
[02:30:08] 🧪 Starting ETL test pipeline
[02:30:08] ⚠️ test_mbdump missing → fallback to full dump
[02:30:08] ▶ Running: python scripts/build_duckdb_stage.py
▶ Starting ETL stage build
📦 Using: test_mbdump.tar.bz2
📦 Extracting files...
🧠 Loading tables into DuckDB...
Traceback (most recent call last):
  File "C:\Users\Jai\code\rewrapped-mb-metadata\scripts\build_duckdb_stage.py", line 202, in <module>
    main()
    ~~~~^^
  File "C:\Users\Jai\code\rewrapped-mb-metadata\scripts\build_duckdb_stage.py", line 116, in main
    load_tsv(con, tar, "mbdump/url", "raw_url", URL_COLS)
    ~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\Jai\code\rewrapped-mb-metadata\scripts\build_duckdb_stage.py", line 79, in load_tsv
    con.execute(f"""
    ~~~~~~~~~~~^^^^^
        CREATE TABLE {table} AS
        ^^^^^^^^^^^^^^^^^^^^^^^
    ...<8 lines>...
        );
        ^^
    """, [f])
    ^^^^^^^^^
_duckdb.NotImplementedException: Not implemented Error: Unable to transform python value of type '<class 'tarfile.ExFileObject'>' to DuckDB LogicalType
[02:30:08] ❌ FAILED: python scripts/build_duckdb_stage.py
```

b) i want to know if this is the best strategy for my goal specified at the start of the message
