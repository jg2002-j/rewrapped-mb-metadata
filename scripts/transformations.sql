-- Step 1: Pre-Projection Pattern for Large Auxiliary Tables
CREATE TEMP TABLE temp_official_releases AS
SELECT id, release_group, artist_credit
FROM raw_release
WHERE status = 1;

CREATE TEMP TABLE temp_release_names AS
SELECT id, name
FROM raw_release;

CREATE TEMP TABLE temp_earliest_dates AS
WITH release_dates_combined AS (
    SELECT release, date_year, date_month, date_day FROM raw_release_country
    UNION ALL
    SELECT release, date_year, date_month, date_day FROM raw_release_unknown_country
)
SELECT release,
       MIN(
               COALESCE(
                       TRY_CAST(
                               MAKE_DATE(
                                       COALESCE(NULLIF(date_year, 0), 9999),
                                       COALESCE(NULLIF(date_month, 0), 1),
                                       COALESCE(NULLIF(date_day, 0), 1)
                               ) AS DATE
                       ), '9999-12-31'::DATE
               )
       ) AS normalized_release_date
FROM release_dates_combined
GROUP BY release;

-- Trigger strict phased drop #1
DROP TABLE raw_release;
DROP TABLE raw_release_country;
DROP TABLE raw_release_unknown_country;

-- Step 2: Ordered Aggregation inside a pure hash pipeline
CREATE TEMP TABLE temp_canonical_rank AS
WITH scored_recordings AS (
    SELECT rec.gid AS recording_mbid,
           rec.length AS raw_length,
           rg.gid AS release_group_mbid,
           rg.name AS release_group_title,
           rg_type.name AS release_group_type,
           REGEXP_REPLACE(REGEXP_REPLACE(LOWER(rec.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS normalized_rec_name,
           rec.artist_credit,
           ROUND(COALESCE(rec.length, 0) / 5000.0) AS length_bucket,
           CASE CAST(rg_type.name AS TEXT)
               WHEN 'Album' THEN 1000 WHEN 'EP' THEN 900 WHEN 'Single' THEN 800
               WHEN 'Soundtrack' THEN 700 WHEN 'Other' THEN 600 WHEN 'Broadcast' THEN 500
               WHEN 'Audio Drama' THEN 400 WHEN 'Audiobook' THEN 300 WHEN 'Spokenword' THEN 200
               WHEN 'Interview' THEN 100 WHEN 'Demo' THEN 50 WHEN 'Live' THEN -1000
               WHEN 'Remix' THEN -1100 WHEN 'DJ-mix' THEN -1200 WHEN 'Mixtape/Street' THEN -1300
               WHEN 'Compilation' THEN -1400 ELSE 0
               END + CASE WHEN rg.artist_credit = rec.artist_credit THEN 50 ELSE 0 END AS evaluation_score,
           COALESCE(erd.normalized_release_date, '9999-12-31'::DATE) AS normalized_release_date
    FROM raw_recording rec
             JOIN raw_track t ON t.recording = rec.id
             JOIN raw_medium m ON t.medium = m.id
             JOIN temp_official_releases r ON m.release = r.id
             LEFT JOIN temp_earliest_dates erd ON r.id = erd.release
             LEFT JOIN raw_release_group rg ON r.release_group = rg.id
             LEFT JOIN raw_rg_type rg_type ON rg.type = rg_type.id
)
SELECT
    normalized_rec_name,
    artist_credit,
    length_bucket,
    FIRST(recording_mbid ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC) AS canonical_recording_mbid,
    FIRST(release_group_mbid ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC) AS release_group_mbid,
    FIRST(release_group_title ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC) AS release_group_title,
    FIRST(release_group_type ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC) AS release_group_type,
    FIRST(raw_length ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC) AS length
FROM scored_recordings
GROUP BY normalized_rec_name, artist_credit, length_bucket;

-- Trigger strict phased drop #2
DROP TABLE raw_release_group;
DROP TABLE raw_rg_type;
DROP TABLE temp_earliest_dates;

-- Step 3: Global Link Coverage Map
CREATE TEMP TABLE temp_all_canonical_recordings AS
SELECT DISTINCT rec.id AS recording_id, rec.gid AS recording_mbid, tcr.canonical_recording_mbid
FROM raw_recording rec
         JOIN temp_canonical_rank tcr
              ON REGEXP_REPLACE(REGEXP_REPLACE(LOWER(rec.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') = tcr.normalized_rec_name
                  AND rec.artist_credit = tcr.artist_credit
                  AND ROUND(COALESCE(rec.length, 0) / 5000.0) = tcr.length_bucket;

-- Step 4: Index precomputed data mappings
CREATE TEMP TABLE artist_wikidata_map AS
SELECT lau.entity0 AS artist_id, REGEXP_EXTRACT(u.url, '(Q[0-9]+)', 1) AS wikidata_id
FROM raw_l_artist_url lau
         JOIN raw_url u ON lau.entity1 = u.id
WHERE u.url LIKE '%wikidata.org%';

-- Trigger strict phased drop #3
DROP TABLE raw_l_artist_url;

-- Step 5: Data compilation inserts for canonical nodes
INSERT INTO target_sqlite.release_group (release_group_mbid, release_group_title, release_group_type)
SELECT DISTINCT release_group_mbid, release_group_title, release_group_type
FROM temp_canonical_rank
WHERE release_group_mbid IS NOT NULL;

INSERT INTO target_sqlite.recording (recording_mbid, release_group_mbid, length, primary_artist_mbid, primary_artist_name, primary_artist_wikidata_id)
SELECT
    tcr.canonical_recording_mbid,
    ANY_VALUE(tcr.release_group_mbid),
    ANY_VALUE(tcr.length),
    ANY_VALUE(a.gid),
    ANY_VALUE(a.name),
    ANY_VALUE(awm.wikidata_id)
FROM temp_canonical_rank tcr
         LEFT JOIN raw_recording rec ON tcr.canonical_recording_mbid = rec.gid
         LEFT JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit AND acn.position = 0
         LEFT JOIN raw_artist a ON acn.artist = a.id
         LEFT JOIN artist_wikidata_map awm ON a.id = awm.artist_id
WHERE tcr.canonical_recording_mbid IS NOT NULL AND tcr.release_group_mbid IS NOT NULL
GROUP BY tcr.canonical_recording_mbid;

INSERT INTO target_sqlite.recording_artists (recording_mbid, artist_mbid, position, artist_name, artist_wikidata_id)
SELECT rec.gid, a.gid, acn.position, a.name, awm.wikidata_id
FROM raw_recording rec
         JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit
         JOIN raw_artist a ON acn.artist = a.id
         LEFT JOIN artist_wikidata_map awm ON a.id = awm.artist_id
WHERE rec.gid IN (SELECT canonical_recording_mbid FROM temp_canonical_rank);

-- Step 6: Targeted textual lookups specifically scoped to official releases (Executed BEFORE destroying Artist data)
INSERT INTO target_sqlite.text_lookup (track_title, release_title, artist_name, recording_mbid)
SELECT DISTINCT
    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(t.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS track_title,
    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(rn.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS release_title,
    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(a.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS artist_name,
    map.canonical_recording_mbid AS recording_mbid
FROM raw_track t
         JOIN raw_medium m ON t.medium = m.id
         JOIN temp_release_names rn ON m.release = rn.id
         JOIN temp_official_releases r ON r.id = rn.id
         JOIN raw_recording rec ON t.recording = rec.id
         JOIN temp_all_canonical_recordings map ON rec.gid = map.recording_mbid
         JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit AND acn.position = 0
         JOIN raw_artist a ON acn.artist = a.id;

-- Trigger strict phased drop #4
DROP TABLE raw_artist_credit_name;
DROP TABLE raw_artist;
DROP TABLE raw_track;
DROP TABLE raw_medium;
DROP TABLE temp_release_names;
DROP TABLE temp_official_releases;
DROP TABLE raw_recording;

-- Step 7: Funnel all URL links into their canonical target IDs
INSERT INTO target_sqlite.link_lookup (url_identifier, provider, recording_mbid)
SELECT DISTINCT
    REGEXP_EXTRACT(REGEXP_REPLACE(u.url, '[\?#].*', ''), '([^/]+)$', 1) AS url_identifier,
    CASE
        WHEN u.url LIKE '%spotify%' THEN 'spotify'
        WHEN u.url LIKE '%apple%' THEN 'applemusic'
        WHEN u.url LIKE '%tidal%' THEN 'tidal'
        ELSE 'unknown'
        END AS provider,
    map.canonical_recording_mbid AS recording_mbid
FROM raw_url u
         JOIN raw_l_recording_url lru ON u.id = lru.entity1
         JOIN temp_all_canonical_recordings map ON lru.entity0 = map.recording_id
WHERE (u.url LIKE '%spotify%' OR u.url LIKE '%apple%' OR u.url LIKE '%tidal%');

-- Trigger final drop cleanup
DROP TABLE raw_url;
DROP TABLE raw_l_recording_url;
DROP TABLE temp_all_canonical_recordings;
DROP TABLE temp_canonical_rank;