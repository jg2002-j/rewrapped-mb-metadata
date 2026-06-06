# Compilation Analytic Queries Using Pure DuckDB Syntax

DUCKDB_CLEAN_STR = r"lower(regexp_replace(regexp_replace(trim(COL), '[^\w\s]', '', 'g'), '\s+', ' ', 'g'))"

BUILD_LINK_LOOKUP_SQL = f"""
    CREATE TABLE duck_link_lookup AS
    WITH ranked_links AS (
        SELECT 
            u.url AS streaming_link,
            t.name AS track_title,
            COALESCE(TRY_CAST(t.length AS INTEGER), 0) AS duration_ms,
            rec.gid AS recording_mbid,
            r.gid AS album_mbid,
            r.name AS album_title,
            rg.gid AS release_group_mbid,
            rg.name AS release_group_title,
            COALESCE(rgt.name, 'Unknown') AS release_group_type,
            a.gid AS artist_mbid,
            a.name AS artist_name,
            regexp_extract(artist_u.url, 'wikidata\\.org/wiki/(Q\\d+)', 1) AS artist_wikidata_id,
            ROW_NUMBER() OVER (
                PARTITION BY u.url 
                ORDER BY CASE COALESCE(rgt.name, 'Unknown')
                    WHEN 'Album' THEN 1
                    WHEN 'EP' THEN 2
                    WHEN 'Single' THEN 3
                    ELSE 4
                END, t.id ASC
            ) as rn
        FROM raw_l_recording_url lru
        JOIN raw_url u ON lru.entity1 = u.id               
        JOIN raw_recording rec ON lru.entity0 = rec.id
        JOIN raw_track t ON t.recording = rec.id
        JOIN raw_medium m ON t.medium = m.id
        JOIN raw_release r ON m.release = r.id           
        JOIN raw_release_group rg ON r.release_group = rg.id
        LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
        JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
        JOIN raw_artist a ON acn.artist = a.id
        LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0 
        LEFT JOIN raw_url artist_u ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata%'
        WHERE u.url LIKE '%spotify/%'
           OR u.url LIKE '%apple%' 
           OR u.url LIKE '%tidal%'
           OR u.url LIKE '%wikidata%'
    )
    SELECT 
        streaming_link, track_title, duration_ms, recording_mbid, 
        album_mbid, album_title, release_group_mbid, release_group_title, 
        release_group_type, artist_mbid, artist_name, artist_wikidata_id
    FROM ranked_links
    WHERE rn = 1;
"""

CREATE_TEXT_HOLDING_TABLE_SQL = """
                                CREATE TABLE duck_text_lookup
                                (
                                    clean_track         TEXT,
                                    clean_album         TEXT,
                                    clean_artist        TEXT,
                                    track_title         TEXT,
                                    duration_ms         INTEGER,
                                    recording_mbid      TEXT,
                                    album_mbid          TEXT,
                                    album_title         TEXT,
                                    release_group_mbid  TEXT,
                                    release_group_title TEXT,
                                    release_group_type  TEXT,
                                    artist_mbid         TEXT,
                                    artist_name         TEXT,
                                    artist_wikidata_id  TEXT
                                );
                                """


def GET_CHUNKED_TEXT_LOOKUP_SQL(start_id, end_id):
    return f"""
        INSERT INTO duck_text_lookup
        SELECT 
            clean_track,
            clean_album,
            clean_artist,
            first(track_title) AS track_title,
            first(duration_ms) AS duration_ms,
            first(recording_mbid) AS recording_mbid,
            first(album_mbid) AS album_mbid,
            first(album_title) AS album_title,
            first(release_group_mbid) AS release_group_mbid,
            first(release_group_title) AS release_group_title,
            first(release_group_type) AS release_group_type,
            first(artist_mbid) AS artist_mbid,
            first(artist_name) AS artist_name,
            first(artist_wikidata_id) AS artist_wikidata_id
        FROM (
            SELECT DISTINCT 
                {DUCKDB_CLEAN_STR.replace('COL', 't.name')} AS clean_track,
                {DUCKDB_CLEAN_STR.replace('COL', 'r.name')} AS clean_album,
                {DUCKDB_CLEAN_STR.replace('COL', 'a.name')} AS clean_artist,
                t.name AS track_title,
                COALESCE(TRY_CAST(t.length AS INTEGER), 0) AS duration_ms,
                rec.gid AS recording_mbid,
                r.gid AS album_mbid,
                r.name AS album_title,
                rg.gid AS release_group_mbid,
                rg.name AS release_group_title,
                COALESCE(rgt.name, 'Unknown') AS release_group_type,
                a.gid AS artist_mbid,
                a.name AS artist_name,
                regexp_extract(artist_u.url, 'wikidata\\.org/wiki/(Q\\d+)', 1) AS artist_wikidata_id
            FROM raw_track t
            JOIN raw_recording rec ON t.recording = rec.id
            JOIN raw_medium m ON t.medium = m.id
            JOIN raw_release r ON m.release = r.id
            JOIN raw_release_group rg ON r.release_group = rg.id
            LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
            JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
            JOIN raw_artist a ON acn.artist = a.id
            LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0 
            LEFT JOIN raw_url artist_u ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata%'
            WHERE t.id >= {start_id} AND t.id < {end_id}
        )
        GROUP BY clean_track, clean_album, clean_artist;
    """


def GET_SQLITE_EXPORT_CHUNK_SQL(filter_condition):
    """
    Generates alphabet partitioned grouping instructions.
    This limits the amount of data processed concurrently during the final step.
    """
    return f"""
        INSERT INTO sqlite_db.text_canonical_lookup
        SELECT clean_track,
               clean_album,
               clean_artist,
               first(track_title),
               first(duration_ms),
               first(recording_mbid),
               first(album_mbid),
               first(album_title),
               first(release_group_mbid),
               first(release_group_title),
               first(release_group_type),
               first(artist_mbid),
               first(artist_name),
               first(artist_wikidata_id)
        FROM duck_text_lookup
        WHERE {filter_condition}
        GROUP BY clean_track, clean_album, clean_artist;
    """