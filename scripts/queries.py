DUCKDB_CLEAN_STR = r"lower(regexp_replace(regexp_replace(trim(COL), '[^\w\s]', '', 'g'), '\s+', ' ', 'g'))"

BUILD_NORMALIZED_PIPELINE_SQL = f"""
    CREATE TABLE artist_wikidata_map AS
    SELECT 
        lau.entity0 AS artist_id,
        MIN(regexp_extract(u.url, 'wikidata\\.org/wiki/(Q\\d+)', 1)) AS artist_wikidata_id
    FROM raw_l_artist_url lau
    JOIN raw_url u ON lau.entity1 = u.id
    WHERE u.url LIKE '%wikidata.org/wiki/Q%'
    GROUP BY 1;

    CREATE TABLE winning_text_tracks AS
    WITH text_priorities AS (
        SELECT 
            t.id AS track_id,
            COALESCE(TRY_CAST(t.length AS INTEGER), 0) AS duration_ms,
            (COALESCE(TRY_CAST(t.length AS INTEGER), 0) / 45000)::INT AS duration_bracket,
            {DUCKDB_CLEAN_STR.replace('COL', 't.name')} AS clean_track,
            {DUCKDB_CLEAN_STR.replace('COL', 'r.name')} AS clean_album,
            {DUCKDB_CLEAN_STR.replace('COL', 'a.name')} AS clean_artist,
            CASE 
                WHEN COALESCE(rgt.name, 'Unknown') = 'Album' AND (lower(rg.name) LIKE '%deluxe%' OR lower(r.name) LIKE '%deluxe%') THEN 1
                WHEN COALESCE(rgt.name, 'Unknown') = 'Album' THEN 2
                WHEN COALESCE(rgt.name, 'Unknown') = 'EP' THEN 3
                WHEN COALESCE(rgt.name, 'Unknown') = 'Single' THEN 4
                ELSE 5
            END AS priority_score
        FROM raw_track t
        JOIN raw_medium m ON t.medium = m.id
        JOIN raw_release r ON m.release = r.id
        JOIN raw_release_group rg ON r.release_group = rg.id
        LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
        JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
        JOIN raw_artist a ON acn.artist = a.id
    ), ranked AS (
        SELECT track_id, clean_track, clean_album, clean_artist, duration_ms,
               ROW_NUMBER() OVER(
                   PARTITION BY clean_track, clean_album, clean_artist, duration_bracket 
                   ORDER BY priority_score ASC, track_id ASC
               ) as rn
        FROM text_priorities
        WHERE clean_track IS NOT NULL AND clean_album IS NOT NULL AND clean_artist IS NOT NULL
          AND clean_track != '' AND clean_album != '' AND clean_artist != ''
    )
    SELECT track_id, clean_track, clean_album, clean_artist, duration_ms FROM ranked WHERE rn = 1;

    -- Map streaming tracks links to clean target IDs
    CREATE TABLE winning_link_tracks AS
    SELECT DISTINCT
        u.url AS streaming_url,
        t.id AS track_id
    FROM raw_l_recording_url lru
    JOIN raw_url u ON lru.entity1 = u.id
    JOIN raw_recording rec ON lru.entity0 = rec.id
    JOIN raw_track t ON t.recording = rec.id
    WHERE u.url LIKE '%spotify.com%' 
       OR u.url LIKE '%music.apple.com%' 
       OR u.url LIKE '%tidal.com%';

    -- Combine unique keys to establish the master metadata dataset boundary
    CREATE TABLE distinct_winning_tracks AS
    SELECT DISTINCT track_id FROM (
        SELECT track_id FROM winning_text_tracks
        UNION DISTINCT
        SELECT track_id FROM winning_link_tracks
    );

    -- 1. Construct final_canonical_metadata
    CREATE TABLE final_canonical_metadata AS
    WITH raw_metadata AS (
        SELECT 
            t.id AS track_id,
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
            awm.artist_wikidata_id,
            ROW_NUMBER() OVER (PARTITION BY t.id ORDER BY acn.position ASC) AS rn
        FROM distinct_winning_tracks dwt
        JOIN raw_track t ON dwt.track_id = t.id
        JOIN raw_recording rec ON t.recording = rec.id
        JOIN raw_medium m ON t.medium = m.id
        JOIN raw_release r ON m.release = r.id
        JOIN raw_release_group rg ON r.release_group = rg.id
        LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
        JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
        JOIN raw_artist a ON acn.artist = a.id
        LEFT JOIN artist_wikidata_map awm ON a.id = awm.artist_id
    )
    SELECT 
        ROW_NUMBER() OVER() AS metadata_id,
        track_id, track_title, duration_ms, recording_mbid, album_mbid, album_title,
        release_group_mbid, release_group_title, release_group_type, artist_mbid, artist_name, artist_wikidata_id
    FROM raw_metadata
    WHERE rn = 1;

    -- 2. Construct final_link_lookup (Filter out track_ids not preserved in our canonical selection metadata dataset)
    CREATE TABLE final_link_lookup AS
    SELECT wlt.streaming_url, wlt.track_id
    FROM winning_link_tracks wlt
    WHERE wlt.track_id IN (SELECT track_id FROM final_canonical_metadata);

    -- 3. Construct final_text_lookup (Only retain records matching our exact canonical selections)
    CREATE TABLE final_text_lookup AS
    SELECT wtt.clean_track, wtt.clean_album, wtt.clean_artist, wtt.track_id
    FROM winning_text_tracks wtt
    WHERE wtt.track_id IN (SELECT track_id FROM final_canonical_metadata);
"""