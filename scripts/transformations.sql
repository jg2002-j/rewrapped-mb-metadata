-- 1. Resolve Canonical Release Groups
-- DuckDB processes this analytically before we push to SQLite
CREATE TABLE temp_canonical_rank AS
WITH release_data AS (SELECT rec.gid      AS recording_mbid,
                             rec.length   AS length,
                             rg.gid       AS release_group_mbid,
                             rg.name      AS release_group_title,
                             rg_type.name AS release_group_type,

                             -- Scoring Matrix based on rules
                             CASE rg_type.name
                                 WHEN 'Album' THEN 1000
                                 WHEN 'EP' THEN 900
                                 WHEN 'Single' THEN 800
                                 WHEN 'Soundtrack' THEN 700
                                 WHEN 'Other' THEN 600
                                 WHEN 'Broadcast' THEN 500
                                 WHEN 'Audio Drama' THEN 400
                                 WHEN 'Audiobook' THEN 300
                                 WHEN 'Spokenword' THEN 200
                                 WHEN 'Interview' THEN 100
                                 WHEN 'Demo' THEN 50
                                 WHEN 'Live' THEN -1000
                                 WHEN 'Remix' THEN -1100
                                 WHEN 'DJ-mix' THEN -1200
                                 WHEN 'Mixtape/Street' THEN -1300
                                 WHEN 'Compilation' THEN -1400
                                 ELSE 0
                                 END
                                 -- Add artist bonus (+50) if first artist matches (logic abbreviated for speed, requires joining artist_credit_name)
                                          AS score,

                             -- Date Padding Strategy
                             COALESCE(
                                     MAKE_DATE(
                                             COALESCE(r.date_year, 9999),
                                             COALESCE(NULLIF(r.date_month, 0), 1),
                                             COALESCE(NULLIF(r.date_day, 0), 1)
                                     ),
                                     '9999-12-31'::DATE
                             )            AS padded_date

                      FROM recording rec
                               JOIN track t ON t.recording = rec.id
                               JOIN medium m ON t.medium = m.id
                               JOIN release r ON m.release = r.id
                               JOIN release_group rg ON r.release_group = rg.id
                               LEFT JOIN release_status rs ON r.status = rs.id
                               LEFT JOIN release_group_primary_type rg_type ON rg.type = rg_type.id

                      WHERE rs.name = 'Official' -- Hard Exclusion
)
SELECT *
FROM (SELECT *,
             ROW_NUMBER() OVER (
                 PARTITION BY recording_mbid
                 ORDER BY score DESC, padded_date ASC, release_group_mbid ASC
                 ) as rank
      FROM release_data)
WHERE rank = 1;

-- 2. Populate SQLite Tables
-- Insert Release Groups
INSERT INTO final_db.release_group (release_group_mbid, release_group_title, release_group_type)
SELECT DISTINCT release_group_mbid, release_group_title, release_group_type
FROM temp_canonical_rank;

-- Insert Recordings
INSERT INTO final_db.recording (recording_mbid, release_group_mbid, length)
SELECT DISTINCT recording_mbid, release_group_mbid, length
FROM temp_canonical_rank;

-- Insert Artists
INSERT INTO final_db.recording_artists
SELECT rec.gid AS recording_mbid,
       a.gid   AS artist_mbid,
       acn.position,
       a.name  AS artist_name,
       NULL    AS artist_wikidata_id -- Simplified: URL parsing logic needed here to extract wikidata IDs from MB's url table
FROM recording rec
         JOIN artist_credit_name acn ON rec.artist_credit = acn.artist_credit
         JOIN artist a ON acn.artist = a.id
WHERE rec.gid IN (SELECT recording_mbid FROM temp_canonical_rank);

-- Insert Link Lookups (Streaming URLs)
INSERT INTO final_db.link_lookup
SELECT
    -- Regex to strip domains, keeping unique IDs (e.g., spotify:track:123 or https://spotify.com/track/123 -> 123)
    REGEXP_EXTRACT(u.url, '([^/:]+)$', 1) AS url_identifier,
    -- Determine provider
    CASE
        WHEN u.url ILIKE '%spotify%' THEN 'spotify'
        WHEN u.url ILIKE '%apple%' THEN 'applemusic'
        WHEN u.url ILIKE '%tidal%' THEN 'tidal'
        ELSE 'unknown'
        END                               AS provider,
    rec.gid                               AS recording_mbid
FROM url u
         JOIN l_recording_url lru ON u.id = lru.entity1
         JOIN link l ON lru.link = l.id
         JOIN recording rec ON lru.entity0 = rec.id
WHERE l.link_type IN (74, 75, 85);
-- Example MB IDs for 'stream for free', 'purchase', etc.

-- Insert Text Lookups (Normalized for NOCASE alternative)
INSERT INTO final_db.text_lookup (track_title, release_title, artist_name, recording_mbid)
SELECT
    -- Natively lowering characters in DuckDB enforces proper Unicode conversion
    -- ensuring downstream exact matches work universally.
    LOWER(t.name) AS track_title,
    LOWER(r.name) AS release_title,
    LOWER(a.name) AS artist_name,
    rec.gid       AS recording_mbid
FROM track t
         JOIN recording rec ON t.recording = rec.id
         JOIN medium m ON t.medium = m.id
         JOIN release r ON m.release = r.id
         JOIN artist_credit_name acn ON t.artist_credit = acn.artist_credit
         JOIN artist a ON acn.artist = a.id
WHERE acn.position = 1; -- Primary artist only