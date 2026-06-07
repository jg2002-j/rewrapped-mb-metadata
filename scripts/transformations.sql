-- ============================================================================
-- ALIGNMENT WITH BLUEPRINT STRUCTURAL MATRIX DEFINITIONS
-- ============================================================================

-- 1. Compute Canonical Ranking Hierarchy Matrix
CREATE TABLE temp_canonical_rank AS
WITH release_dates_combined AS (SELECT release, date_year, date_month, date_day
                                FROM raw_release_country
                                UNION ALL
                                SELECT release, date_year, date_month, date_day
                                FROM raw_release_unknown_country),
     earliest_dates AS (SELECT release,
                               MIN(
                                       COALESCE(
                                               TRY_CAST(
                                                       MAKE_DATE(
                                                               CAST(CASE
                                                                        WHEN date_year IS NULL OR date_year = '0'
                                                                            THEN '9999'
                                                                        ELSE date_year END AS INTEGER),
                                                               CAST(CASE
                                                                        WHEN date_month IS NULL OR date_month = '0'
                                                                            THEN '1'
                                                                        ELSE date_month END AS INTEGER),
                                                               CAST(CASE WHEN date_day IS NULL OR date_day = '0' THEN '1' ELSE date_day END AS INTEGER)
                                                       ) AS DATE
                                               ),
                                               '9999-12-31'::DATE
                                       )
                               ) AS normalized_release_date
                        FROM release_dates_combined
                        GROUP BY release),
     mapped_release_sets AS (SELECT rec.gid                                                   AS recording_mbid,
                                    TRY_CAST(rec.length AS INTEGER)                           AS length,
                                    rg.gid                                                    AS release_group_mbid,
                                    rg.name                                                   AS release_group_title,
                                    rg_type.name                                              AS release_group_type,

                                    -- Scoring Evaluation Weights
                                    CASE CAST(rg_type.name AS TEXT)
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
                                        END +
                                    CASE
                                        WHEN rg.artist_credit = rec.artist_credit
                                            THEN 50
                                        ELSE 0 END                                            AS evaluation_score,

                                    COALESCE(erd.normalized_release_date, '9999-12-31'::DATE) AS normalized_release_date

                             FROM raw_recording rec
                                      JOIN raw_track t ON t.recording = rec.id
                                      JOIN raw_medium m ON t.medium = m.id
                                      JOIN raw_release r ON m.release = r.id
                                      LEFT JOIN earliest_dates erd ON r.id = erd.release
                                      LEFT JOIN raw_release_group rg ON r.release_group = rg.id
                                      LEFT JOIN raw_rg_type rg_type ON rg.type = rg_type.id
                             WHERE CAST(r.status AS INTEGER) = 1)
SELECT recording_mbid,
       length,
       release_group_mbid,
       release_group_title,
       release_group_type
FROM (SELECT *,
             ROW_NUMBER() OVER (
                 PARTITION BY recording_mbid
                 ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC
                 ) as rank_priority
      FROM mapped_release_sets)
WHERE rank_priority = 1;

-- 2. Populate Bare Target Tables Natively
INSERT INTO target_sqlite.release_group (release_group_mbid, release_group_title, release_group_type)
SELECT DISTINCT release_group_mbid, release_group_title, release_group_type
FROM temp_canonical_rank
WHERE release_group_mbid IS NOT NULL;

INSERT INTO target_sqlite.recording (recording_mbid, release_group_mbid, length)
SELECT DISTINCT recording_mbid, release_group_mbid, length
FROM temp_canonical_rank
WHERE recording_mbid IS NOT NULL;

-- Populate Artist Matrix Assignments
INSERT INTO target_sqlite.recording_artists (recording_mbid, artist_mbid, position, artist_name, artist_wikidata_id)
SELECT DISTINCT rec.gid                           AS recording_mbid,
                a.gid                             AS artist_mbid,
                TRY_CAST(acn.position AS INTEGER) AS position,
                a.name                            AS artist_name,
                (SELECT REGEXP_EXTRACT(u.url, '(Q[0-9]+)$', 1)
                 FROM raw_l_artist_url lau
                          JOIN raw_url u ON lau.entity1 = u.id
                 WHERE lau.entity0 = a.id
                   AND lau.link = '352'
                 LIMIT 1)                         AS artist_wikidata_id
FROM raw_recording rec
         JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit
         JOIN raw_artist a ON acn.artist = a.id
WHERE rec.gid IN (SELECT recording_mbid FROM temp_canonical_rank);

-- Populate Direct URL Fast Link Identifiers
INSERT INTO target_sqlite.link_lookup (url_identifier, provider, recording_mbid)
SELECT DISTINCT REGEXP_EXTRACT(u.url, '([^/:]+)$', 1) AS url_identifier,
                CASE
                    WHEN u.url ILIKE '%spotify%' THEN 'spotify'
                    WHEN u.url ILIKE '%apple%' THEN 'applemusic'
                    WHEN u.url ILIKE '%tidal%' THEN 'tidal'
                    ELSE 'unknown'
                    END                               AS provider,
                rec.gid                               AS recording_mbid
FROM raw_url u
         JOIN raw_l_recording_url lru ON u.id = lru.entity1
         JOIN raw_recording rec ON lru.entity0 = rec.id
WHERE lru.link IN ('74', '75', '85')
  AND rec.gid IN (SELECT recording_mbid FROM temp_canonical_rank);

-- Populate Normalized Full Text Search Mappings
INSERT INTO target_sqlite.text_lookup (track_title, release_title, artist_name, recording_mbid)
SELECT DISTINCT LOWER(t.name) AS track_title,
                LOWER(r.name) AS release_title,
                LOWER(a.name) AS artist_name,
                rec.gid       AS recording_mbid
FROM raw_track t
         JOIN raw_recording rec ON t.recording = rec.id
         JOIN raw_medium m ON t.medium = m.id
         JOIN raw_release r ON m.release = r.id
         JOIN raw_artist_credit_name acn ON t.artist_credit = acn.artist_credit
         JOIN raw_artist a ON acn.artist = a.id
WHERE CAST(acn.position AS INTEGER) = 0;