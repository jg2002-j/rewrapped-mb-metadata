-- ============================================================================
-- Normalization contract (SINGLE SOURCE OF TRUTH for text matching).
-- The downstream service MUST apply this exact transformation:
--   lowercase -> strip every char that is not a Unicode letter/number/space
--             -> collapse runs of whitespace to a single space
-- If the two sides diverge, the text fallback will silently miss.
-- ============================================================================
CREATE OR REPLACE MACRO norm(s) AS
    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(s), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g');

-- NOTE ON MEMORY: the large intermediates below are ordinary CREATE TABLE (not
-- CREATE TEMP TABLE) on purpose. Temp tables live in DuckDB's in-memory catalog
-- and tend to stay resident; ordinary tables live in the on-disk engine file
-- where the buffer manager can page them out under the memory_limit.

-- Step 0: Normalize every recording ONCE (name + length bucket), then drop the
-- heavy raw_recording table.
CREATE TABLE temp_recording_norm AS
SELECT id,
       gid,
       artist_credit,
       length,
       norm(name)                              AS normalized_name,
       ROUND(COALESCE(length, 0) / 5000.0)     AS length_bucket
FROM raw_recording;

-- Step 1: Pre-Projection Pattern for Large Auxiliary Tables
CREATE TABLE temp_official_releases AS
SELECT id, release_group, artist_credit
FROM raw_release
WHERE status = 1;

CREATE TABLE temp_release_names AS
SELECT id, name
FROM raw_release;

CREATE TABLE temp_earliest_dates AS
WITH release_dates_combined AS (
    SELECT release, date_year, date_month, date_day FROM raw_release_country
    UNION ALL
    SELECT release, date_year, date_month, date_day FROM raw_release_unknown_country
)
SELECT release,
       MIN(
               COALESCE(
                   -- TRY() swallows invalid calendar dates (e.g. Feb 30). MAKE_DATE
                   -- throws on those before any cast, so TRY_CAST cannot catch it.
                       TRY(MAKE_DATE(
                               COALESCE(NULLIF(date_year, 0), 9999),
                               COALESCE(NULLIF(date_month, 0), 1),
                               COALESCE(NULLIF(date_day, 0), 1)
                           )),
                       '9999-12-31'::DATE
               )
       ) AS normalized_release_date
FROM release_dates_combined
GROUP BY release;

-- Trigger strict phased drop #1 (raw_recording is now represented by temp_recording_norm)
DROP TABLE raw_recording;
DROP TABLE raw_release;
DROP TABLE raw_release_country;
DROP TABLE raw_release_unknown_country;
CHECKPOINT;

-- Step 2: Canonical ranking. One external sort (ROW_NUMBER ... QUALIFY = 1)
-- instead of five ordered FIRST() aggregates. Also carries the chosen release
-- group's artist_credit so we can expand its credits later (raw_release_group is
-- dropped right after this step).
CREATE TABLE temp_canonical_rank AS
WITH scored_recordings AS (
    SELECT rec.gid AS recording_mbid,
           rec.length AS raw_length,
           rg.gid AS release_group_mbid,
           rg.name AS release_group_title,
           rg_type.name AS release_group_type,
           rg.artist_credit AS release_group_artist_credit,
           rec.normalized_name AS normalized_rec_name,
           rec.artist_credit,
           rec.length_bucket,
           CASE CAST(rg_type.name AS TEXT)
               WHEN 'Album' THEN 1000 WHEN 'EP' THEN 900 WHEN 'Single' THEN 800
               WHEN 'Soundtrack' THEN 700 WHEN 'Other' THEN 600 WHEN 'Broadcast' THEN 500
               WHEN 'Audio Drama' THEN 400 WHEN 'Audiobook' THEN 300 WHEN 'Spokenword' THEN 200
               WHEN 'Interview' THEN 100 WHEN 'Demo' THEN 50 WHEN 'Live' THEN -1000
               WHEN 'Remix' THEN -1100 WHEN 'DJ-mix' THEN -1200 WHEN 'Mixtape/Street' THEN -1300
               WHEN 'Compilation' THEN -1400 ELSE 0
               END + CASE WHEN rg.artist_credit = rec.artist_credit THEN 50 ELSE 0 END AS evaluation_score,
           COALESCE(erd.normalized_release_date, '9999-12-31'::DATE) AS normalized_release_date
    FROM temp_recording_norm rec
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
    recording_mbid              AS canonical_recording_mbid,
    release_group_mbid,
    release_group_title,
    release_group_type,
    release_group_artist_credit,
    raw_length                  AS length
FROM scored_recordings
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY normalized_rec_name, artist_credit, length_bucket
    ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC
) = 1;

-- Trigger strict phased drop #2
DROP TABLE raw_release_group;
DROP TABLE raw_rg_type;
DROP TABLE temp_earliest_dates;
CHECKPOINT;

-- Step 3: Global Link Coverage Map -- a clean equi-join on the precomputed key
CREATE TABLE temp_all_canonical_recordings AS
SELECT DISTINCT rec.id AS recording_id, rec.gid AS recording_mbid, tcr.canonical_recording_mbid
FROM temp_recording_norm rec
         JOIN temp_canonical_rank tcr
              ON rec.normalized_name = tcr.normalized_rec_name
                  AND rec.artist_credit = tcr.artist_credit
                  AND rec.length_bucket = tcr.length_bucket;

-- Step 4: Wikidata id per artist (small; only wikidata-linked artists)
CREATE TABLE artist_wikidata_map AS
SELECT lau.entity0 AS artist_id, REGEXP_EXTRACT(u.url, '(Q[0-9]+)', 1) AS wikidata_id
FROM raw_l_artist_url lau
         JOIN raw_url u ON lau.entity1 = u.id
WHERE u.url LIKE '%wikidata.org%';

-- Trigger strict phased drop #3
DROP TABLE raw_l_artist_url;

-- Step 5: Canonical release-group dimension
INSERT INTO target_sqlite.release_group (release_group_mbid, release_group_title, release_group_type)
SELECT release_group_mbid, ANY_VALUE(release_group_title), ANY_VALUE(release_group_type)
FROM temp_canonical_rank
WHERE release_group_mbid IS NOT NULL
GROUP BY release_group_mbid;

-- Step 5a: Expand credits into (entity, artist gid, position) sources. These feed
-- both the normalised `artists` dimension and the two link tables.
CREATE TABLE temp_recording_credits AS
SELECT rec.gid AS recording_mbid, a.gid AS artist_mbid, acn.position AS position
FROM temp_recording_norm rec
         JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit
         JOIN raw_artist a ON acn.artist = a.id
WHERE rec.gid IN (SELECT canonical_recording_mbid FROM temp_canonical_rank);

CREATE TABLE temp_rg_credits AS
WITH rgc AS (
    SELECT release_group_mbid, ANY_VALUE(release_group_artist_credit) AS artist_credit
    FROM temp_canonical_rank
    WHERE release_group_mbid IS NOT NULL
    GROUP BY release_group_mbid
)
SELECT rgc.release_group_mbid, a.gid AS artist_mbid, acn.position AS position
FROM rgc
         JOIN raw_artist_credit_name acn ON rgc.artist_credit = acn.artist_credit
         JOIN raw_artist a ON acn.artist = a.id;

-- Step 5b: Normalised artist dimension -- one row per distinct credited artist,
-- name + wikidata stored once (no per-credit duplication).
INSERT INTO target_sqlite.artists (artist_mbid, artist_name, artist_wikidata_id)
SELECT a.gid, ANY_VALUE(a.name), ANY_VALUE(awm.wikidata_id)
FROM raw_artist a
         LEFT JOIN artist_wikidata_map awm ON a.id = awm.artist_id
WHERE a.gid IN (
    SELECT artist_mbid FROM temp_recording_credits
    UNION
    SELECT artist_mbid FROM temp_rg_credits
)
GROUP BY a.gid;

-- Step 5c: Canonical recording dimension (primary_artist_mbid = position 0 credit)
INSERT INTO target_sqlite.recording (recording_mbid, release_group_mbid, length, primary_artist_mbid)
SELECT
    tcr.canonical_recording_mbid,
    ANY_VALUE(tcr.release_group_mbid),
    ANY_VALUE(tcr.length),
    ANY_VALUE(a.gid)
FROM temp_canonical_rank tcr
         LEFT JOIN temp_recording_norm rec ON tcr.canonical_recording_mbid = rec.gid
         LEFT JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit AND acn.position = 0
         LEFT JOIN raw_artist a ON acn.artist = a.id
WHERE tcr.canonical_recording_mbid IS NOT NULL AND tcr.release_group_mbid IS NOT NULL
GROUP BY tcr.canonical_recording_mbid;

-- Step 5d: Credit link tables (artist_mbid only; join to artists for names)
INSERT INTO target_sqlite.recording_artists (recording_mbid, artist_mbid, position)
SELECT recording_mbid, artist_mbid, position FROM temp_recording_credits;

INSERT INTO target_sqlite.release_group_artists (release_group_mbid, artist_mbid, position)
SELECT release_group_mbid, artist_mbid, position FROM temp_rg_credits;

-- Step 6: Targeted textual lookups specifically scoped to official releases
-- (text_lookup.artist_name is the NORMALISED search key, distinct from the
-- display name in the artists table). Executed BEFORE destroying Artist data.
INSERT INTO target_sqlite.text_lookup (track_title, release_title, artist_name, recording_mbid)
SELECT DISTINCT
    norm(t.name)  AS track_title,
    norm(rn.name) AS release_title,
    norm(a.name)  AS artist_name,
    map.canonical_recording_mbid AS recording_mbid
FROM raw_track t
         JOIN raw_medium m ON t.medium = m.id
         JOIN temp_release_names rn ON m.release = rn.id
         JOIN temp_official_releases r ON r.id = rn.id
         JOIN temp_recording_norm rec ON t.recording = rec.id
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
DROP TABLE temp_recording_norm;
DROP TABLE temp_recording_credits;
DROP TABLE temp_rg_credits;
DROP TABLE artist_wikidata_map;
CHECKPOINT;

-- Step 7: Funnel all URL links into their canonical target IDs.
-- Apple Music album links carry the TRACK id in the `?i=` query parameter, so it
-- must be read BEFORE the query string is stripped. Everything else (Spotify,
-- Apple song links, Tidal) uses the last path segment.
INSERT INTO target_sqlite.link_lookup (url_identifier, provider, recording_mbid)
SELECT DISTINCT
    CASE
        WHEN u.url LIKE '%apple%' AND REGEXP_MATCHES(u.url, '[?&]i=[0-9]+')
            THEN REGEXP_EXTRACT(u.url, '[?&]i=([0-9]+)', 1)
        ELSE REGEXP_EXTRACT(REGEXP_REPLACE(u.url, '[\?#].*', ''), '([^/]+)$', 1)
        END AS url_identifier,
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
