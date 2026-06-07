-- ============================================================================
-- ALIGNMENT WITH OFFICIAL METABRAINZ SCHEMA REFERENCE MANIFEST
-- Column positions resolved natively from read_csv_auto positional definitions:
-- recording: column00=id, column01=gid, column02=name, column03=artist_credit, column04=length
-- release:   column00=id, column01=gid, column02=name, column03=artist_credit, column04=release_group, column05=status, column06=packaging, column07=country, column08=language, column09=date_year, column10=date_month, column11=date_day
-- track:     column00=id, column01=gid, column02=recording, column03=medium, column04=position, column05=number, column06=name, column07=artist_credit, column08=length
-- medium:    column00=id, column01=release, column02=position, column03=format, column04=name, column05=track_count
-- release_group: column00=id, column01=gid, column02=name, column03=artist_credit, column04=type, column05=comment, column06=edits_pending
-- ============================================================================

-- 1. Compute Canonical Ranking Hierarchy Matrix
CREATE TABLE temp_canonical_rank AS
WITH mapped_release_sets AS (SELECT rec.column01                                            AS recording_mbid,
                                    CAST(rec.column04 AS INTEGER)                           AS length,
                                    rg.column01                                             AS release_group_mbid,
                                    rg.column02                                             AS release_group_title,
                                    rg_type.column01                                        AS release_group_type,

                                    -- Scoring Evaluation Weights
                                    CASE rg_type.column01
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
                                    CASE WHEN rg.column03 = rec.column03 THEN 50 ELSE 0 END AS evaluation_score,

                                    -- Date Normalization & Far-Future Substitution Strategy for NULL records
                                    COALESCE(
                                            MAKE_DATE(
                                                    CAST(COALESCE(r.column09, 9999) AS INTEGER),
                                                    CAST(COALESCE(NULLIF(r.column10, 0), 1) AS INTEGER),
                                                    CAST(COALESCE(NULLIF(r.column11, 0), 1) AS INTEGER)
                                            ),
                                            '9999-12-31'::DATE
                                    )                                                       AS normalized_release_date

                             FROM recording rec
                                      JOIN track t ON t.column02 = rec.column00
                                      JOIN medium m ON t.column03 = m.column00
                                      JOIN release r ON m.column01 = r.column00
                                      JOIN release_group rg ON r.column04 = rg.column00
                                      LEFT JOIN release_status rs ON r.column05 = rs.column00
                                      LEFT JOIN release_group_primary_type rg_type ON rg.column04 = rg_type.column00

                             WHERE rs.column01 = 'Official' -- Explicit Hard Filtering Exclusion Rules
)
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
-- Populate Release Groups
INSERT INTO target_sqlite.release_group (release_group_mbid, release_group_title, release_group_type)
SELECT DISTINCT release_group_mbid, release_group_title, release_group_type
FROM temp_canonical_rank;

-- Populate Recording Maps
INSERT INTO target_sqlite.recording (recording_mbid, release_group_mbid, length)
SELECT DISTINCT recording_mbid, release_group_mbid, length
FROM temp_canonical_rank;

-- Populate Artist Matrix Assignments
INSERT INTO target_sqlite.recording_artists (recording_mbid, artist_mbid, position, artist_name, artist_wikidata_id)
SELECT DISTINCT rec.column01                  AS recording_mbid,
                a.column01                    AS artist_mbid,
                CAST(acn.column01 AS INTEGER) AS position,
                a.column02                    AS artist_name,
                -- Extract Wikidata elements via the URL matching relational graph schema structures
                (SELECT REGEXP_EXTRACT(u.column02, '(Q[0-9]+)$', 1)
                 FROM l_artist_url lau
                          JOIN url u ON lau.column03 = u.column00
                          JOIN link l ON lau.column01 = l.column00
                 WHERE lau.column02 = a.column00
                   AND l.column01 = 352 -- 352 represents explicit Wikidata link type entity mapping constraints
                 LIMIT 1)                     AS artist_wikidata_id
FROM recording rec
         JOIN artist_credit_name acn ON rec.column03 = acn.column00
         JOIN artist a ON acn.column02 = a.column00
WHERE rec.column01 IN (SELECT recording_mbid FROM temp_canonical_rank);

-- Populate Direct URL Fast Link Identifiers
INSERT INTO target_sqlite.link_lookup (url_identifier, provider, recording_mbid)
SELECT DISTINCT REGEXP_EXTRACT(u.column02, '([^/:]+)$', 1) AS url_identifier,
                CASE
                    WHEN u.column02 ILIKE '%spotify%' THEN 'spotify'
                    WHEN u.column02 ILIKE '%apple%' THEN 'applemusic'
                    WHEN u.column02 ILIKE '%tidal%' THEN 'tidal'
                    ELSE 'unknown'
                    END                                    AS provider,
                rec.column01                               AS recording_mbid
FROM url u
         JOIN l_recording_url lru ON u.column00 = lru.column03
         JOIN link l ON lru.column01 = l.column00
         JOIN recording rec ON lru.column02 = rec.column00
WHERE l.column01 IN (74, 75, 85) -- Match against stream and native download configurations
  AND rec.column01 IN (SELECT recording_mbid FROM temp_canonical_rank);

-- Populate Normalized Full Text Search Mappings
INSERT INTO target_sqlite.text_lookup (track_title, release_title, artist_name, recording_mbid)
SELECT DISTINCT LOWER(t.column06) AS track_title,
                LOWER(r.column02) AS release_title,
                LOWER(a.column02) AS artist_name,
                rec.column01      AS recording_mbid
FROM track t
         JOIN recording rec ON t.column02 = rec.column00
         JOIN medium m ON t.column03 = m.column00
         JOIN release r ON m.column01 = r.column00
         JOIN artist_credit_name acn ON t.column07 = acn.column00
         JOIN artist a ON acn.column02 = a.column00
WHERE acn.column01 = 0; -- Only reference the primary artist for fallback validation optimization