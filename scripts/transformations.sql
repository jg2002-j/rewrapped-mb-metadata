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
                                                               COALESCE(NULLIF(TRY_CAST(date_year AS INTEGER), 0), 9999),
                                                               COALESCE(NULLIF(TRY_CAST(date_month AS INTEGER), 0), 1),
                                                               COALESCE(NULLIF(TRY_CAST(date_day AS INTEGER), 0), 1)
                                                       ) AS DATE
                                               ), '9999-12-31':: DATE
                                       )
                               ) AS normalized_release_date
                        FROM release_dates_combined
                        GROUP BY release),
     mapped_release_sets AS (SELECT rec.gid                                                   AS recording_mbid,
                                    TRY_CAST(rec.length AS INTEGER)                           AS length,
                                    rg.gid                                                    AS release_group_mbid,
                                    rg.name                                                   AS release_group_title,
                                    rg_type.name                                              AS release_group_type,

                                    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(rec.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS normalized_rec_name,
                                    rec.artist_credit                                         AS artist_credit,

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
                 PARTITION BY normalized_rec_name, artist_credit, ROUND(COALESCE(length, 0) / 5000.0)
                 ORDER BY evaluation_score DESC, normalized_release_date ASC, release_group_mbid ASC
                 ) as rank_priority
      FROM mapped_release_sets)
WHERE rank_priority = 1;

-- Precompute Wikidata mapping once to avoid per-row nested evaluations
CREATE TEMP TABLE artist_wikidata_map AS
SELECT lau.entity0 AS artist_id, REGEXP_EXTRACT(u.url, '(Q[0-9]+)', 1) AS wikidata_id
FROM raw_l_artist_url lau
         JOIN raw_url u ON lau.entity1 = u.id
WHERE u.url LIKE '%wikidata.org%';

INSERT INTO target_sqlite.release_group (release_group_mbid, release_group_title, release_group_type)
SELECT DISTINCT release_group_mbid, release_group_title, release_group_type
FROM temp_canonical_rank
WHERE release_group_mbid IS NOT NULL;

INSERT INTO target_sqlite.recording (recording_mbid, release_group_mbid, length, primary_artist_mbid, primary_artist_name, primary_artist_wikidata_id)
SELECT DISTINCT
    tcr.recording_mbid,
    tcr.release_group_mbid,
    tcr.length,
    a.gid   AS primary_artist_mbid,
    a.name  AS primary_artist_name,
    awm.wikidata_id AS primary_artist_wikidata_id
FROM temp_canonical_rank tcr
         LEFT JOIN raw_recording rec ON tcr.recording_mbid = rec.gid
         LEFT JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit AND TRY_CAST(acn.position AS INTEGER) = 0
         LEFT JOIN raw_artist a ON acn.artist = a.id
         LEFT JOIN artist_wikidata_map awm ON a.id = awm.artist_id
WHERE tcr.recording_mbid IS NOT NULL
  AND tcr.release_group_mbid IS NOT NULL; -- Null safety for target schema constraints

INSERT INTO target_sqlite.recording_artists (recording_mbid, artist_mbid, position, artist_name, artist_wikidata_id)
SELECT rec.gid                           AS recording_mbid,
       a.gid                             AS artist_mbid,
       TRY_CAST(acn.position AS INTEGER) AS position,
       a.name                            AS artist_name,
       awm.wikidata_id                   AS artist_wikidata_id
FROM raw_recording rec
         JOIN raw_artist_credit_name acn ON rec.artist_credit = acn.artist_credit
         JOIN raw_artist a ON acn.artist = a.id
         LEFT JOIN artist_wikidata_map awm ON a.id = awm.artist_id
WHERE rec.gid IN (SELECT recording_mbid FROM temp_canonical_rank);

INSERT INTO target_sqlite.link_lookup (url_identifier, provider, recording_mbid)
SELECT DISTINCT
    REGEXP_EXTRACT(REGEXP_REPLACE(u.url, '[\?#].*', ''), '([^/]+)$', 1) AS url_identifier,
    CASE
        WHEN u.url LIKE '%spotify.com%' THEN 'spotify'
        WHEN u.url LIKE '%apple.com%' THEN 'applemusic'
        WHEN u.url LIKE '%tidal.com%' THEN 'tidal'
        ELSE 'unknown'
        END AS provider,
    rec.gid AS recording_mbid
FROM raw_url u
         JOIN raw_l_recording_url lru ON u.id = lru.entity1
         JOIN raw_recording rec ON lru.entity0 = rec.id
WHERE (u.url LIKE '%spotify.com%' OR u.url LIKE '%apple.com%' OR u.url LIKE '%tidal.com%')
  AND rec.gid IN (SELECT recording_mbid FROM temp_canonical_rank);

INSERT INTO target_sqlite.text_lookup (track_title, release_title, artist_name, recording_mbid)
SELECT DISTINCT
    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(t.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS track_title,
    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(r.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS release_title,
    REGEXP_REPLACE(REGEXP_REPLACE(LOWER(a.name), '[^\p{L}\p{N}\s]', '', 'g'), '\s+', ' ', 'g') AS artist_name,
    rec.gid AS recording_mbid
FROM raw_track t
         JOIN raw_recording rec ON t.recording = rec.id
         JOIN raw_medium m ON t.medium = m.id
         JOIN raw_release r ON m.release = r.id
         JOIN raw_artist_credit_name acn ON t.artist_credit = acn.artist_credit
         JOIN raw_artist a ON acn.artist = a.id
WHERE CAST(acn.position AS INTEGER) = 0;