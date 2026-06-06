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
