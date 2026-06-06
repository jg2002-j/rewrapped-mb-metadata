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

# Execution via full out-of-core streaming group-by, replacing chunk logic
BUILD_TEXT_LOOKUP_SQL = """
                        CREATE TABLE duck_text_lookup AS
                        SELECT ct.clean_track,
                               cr.clean_album,
                               ca.clean_artist,
                               first(ct.track_title)                AS track_title,
                               first(ct.duration_ms)                AS duration_ms,
                               first(rec.gid)                       AS recording_mbid,
                               first(cr.album_mbid)                 AS album_mbid,
                               first(cr.album_title)                AS album_title,
                               first(rg.gid)                        AS release_group_mbid,
                               first(rg.name)                       AS release_group_title,
                               first(COALESCE(rgt.name, 'Unknown')) AS release_group_type,
                               first(ca.artist_mbid)                AS artist_mbid,
                               first(ca.artist_name)                AS artist_name,
                               first(wm.wikidata_id)                AS artist_wikidata_id
                        FROM clean_tracks ct
                                 JOIN raw_recording rec ON ct.recording = rec.id
                                 JOIN raw_medium m ON ct.medium = m.id
                                 JOIN clean_releases cr ON m.release = cr.id
                                 JOIN raw_release_group rg ON cr.release_group = rg.id
                                 LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
                                 JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
                                 JOIN clean_artists ca ON acn.artist = ca.id
                                 LEFT JOIN wikidata_mapping wm ON ca.id = wm.artist_id
                        GROUP BY ct.clean_track, cr.clean_album, ca.clean_artist; \
                        """
