# Compilation Analytic Queries Using Audited Named Column Semantics

DUCKDB_CLEAN_STR = "lower(regexp_replace(regexp_replace(trim(COL), '[^\\w\\s]', '', 'g'), '\\s+', ' ', 'g'))"

BUILD_LINK_LOOKUP_SQL = f"""
    INSERT INTO sqlite_db.link_canonical_lookup
    SELECT 
        streaming_link,
        first(track_title) AS track_title,
        first(duration_ms) AS duration_ms,
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
            u.url AS streaming_link,
            t.name AS track_title,
            COALESCE(TRY_CAST(t.length AS INTEGER), 0) AS duration_ms,
            r.gid AS album_mbid,
            r.name AS album_title,
            rg.gid AS release_group_mbid,
            rg.name AS release_group_title,
            COALESCE(rgt.name, 'Unknown') AS release_group_type,
            a.gid AS artist_mbid,
            a.name AS artist_name,
            regexp_extract(artist_u.url, 'wikidata\\.org/wiki/(Q\\d+)', 1) AS artist_wikidata_id
        FROM raw_l_release_url lru
        JOIN raw_url u ON lru.entity1 = u.id               -- entity1 is url
        JOIN raw_release r ON lru.entity0 = r.id           -- entity0 is release
        JOIN raw_medium m ON r.id = m.release
        JOIN raw_track t ON t.medium = m.id
        JOIN raw_release_group rg ON r.release_group = rg.id
        LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
        JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
        JOIN raw_artist a ON acn.artist = a.id
        LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0 -- entity0 is artist
        LEFT JOIN raw_url artist_u ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata.org%'
        WHERE u.url LIKE '%http://googleusercontent.com/spotify.com/%' 
           OR u.url LIKE 'spotify:%' 
           OR u.url LIKE '%apple.com%' 
           OR u.url LIKE '%tidal.com%' 
           OR u.url LIKE '%wikidata.org%'
    )
    GROUP BY streaming_link;
"""

BUILD_TEXT_LOOKUP_SQL = f"""
    INSERT INTO sqlite_db.text_canonical_lookup
    SELECT 
        clean_track,
        clean_album,
        clean_artist,
        first(track_title) AS track_title,
        first(duration_ms) AS duration_ms,
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
            r.gid AS album_mbid,
            r.name AS album_title,
            rg.gid AS release_group_mbid,
            rg.name AS release_group_title,
            COALESCE(rgt.name, 'Unknown') AS release_group_type,
            a.gid AS artist_mbid,
            a.name AS artist_name,
            regexp_extract(artist_u.url, 'wikidata\\.org/wiki/(Q\\d+)', 1) AS artist_wikidata_id
        FROM raw_track t
        JOIN raw_medium m ON t.medium = m.id
        JOIN raw_release r ON m.release = r.id
        JOIN raw_release_group rg ON r.release_group = rg.id
        LEFT JOIN raw_rg_type rgt ON rg.type = rgt.id
        JOIN raw_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
        JOIN raw_artist a ON acn.artist = a.id
        LEFT JOIN raw_l_artist_url lau ON a.id = lau.entity0 -- entity0 is artist
        LEFT JOIN raw_url artist_u ON lau.entity1 = artist_u.id AND artist_u.url LIKE '%wikidata.org%'
    )
    GROUP BY clean_track, clean_album, clean_artist;
"""