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