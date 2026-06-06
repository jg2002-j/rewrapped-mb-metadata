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