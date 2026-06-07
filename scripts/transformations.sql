-- ============================================================================
-- ALIGNMENT WITH OFFICIAL METABRAINZ SCHEMA REFERENCE MANIFEST
-- Column positions resolved natively from read_csv_auto positional definitions:
-- recording: column0=id, column1=gid, column2=name, column3=artist_credit, column4=length
-- release:   column0=id, column1=gid, column2=name, column3=artist_credit, column4=release_group, column5=status, column6=packaging, column7=country, column8=language, column9=date_year, column10=date_month, column11=date_day
-- track:     column0=id, column1=gid, column2=recording, column3=medium, column4=position, column5=number, column6=name, column7=artist_credit, column8=length
-- medium:    column0=id, column1=release, column2=position, column3=format, column4=name, column5=track_count
-- release_group: column0=id, column1=gid, column2=name, column3=artist_credit, column4=type, column5=comment, column6=edits_pending
-- ============================================================================

-- 1. Compute Canonical Ranking Hierarchy Matrix
CREATE TABLE temp_canonical_rank AS
WITH mapped_release_sets AS (SELECT rec.column1                  AS recording_mbid,
                                    CAST(rec.column4 AS INTEGER) AS length,
                                    rg.column1                   AS release_group_mbid,
                                    rg.column2                   AS release_group_title,
                                    rg_type.column1              AS release_group_type,

                                    -- Scoring Evaluation Weights
                                    CASE CAST(rg_type.column1 AS TEXT)
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
                                        WHEN CAST(rg.column3 AS INTEGER) = CAST(rec.column3 AS INTEGER) THEN 50
                                        ELSE 0 END               AS evaluation_score,

                                    -- Date Normalization & Far-Future Substitution Strategy for NULL records
                                    COALESCE(
                                            MAKE_DATE(
                                                    CAST(COALESCE(r.column9, 9999) AS INTEGER),
                                                    CAST(COALESCE(NULLIF(r.column10, 0), 1) AS INTEGER),
                                                    CAST(COALESCE(NULLIF(r.column11, 0), 1) AS INTEGER)
                                            ),
                                            '9999-12-31'::DATE
                                    )                            AS normalized_release_date

                             FROM recording rec
                                      JOIN track t ON t.column2 = rec.column0
                                      JOIN medium m ON t.column3 = m.column0
                                      JOIN release r ON m.column1 = r.column0
                                      JOIN release_group rg ON r.column4 = rg.column0
                                      LEFT JOIN release_status rs ON r.column5 = rs.column0
                                      LEFT JOIN release_group_primary_type rg_type ON rg.column4 = rg_type.column0

                             WHERE rs.column1 = 'Official' -- Explicit Hard Filtering Exclusion Rules
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
SELECT DISTINCT rec.column1                  AS recording_mbid,
                a.column1                    AS artist_mbid,
                CAST(acn.column1 AS INTEGER) AS position,
                a.column2                    AS artist_name,
                (SELECT REGEXP_EXTRACT(u.column2, '(Q[0-9]+)$', 1)
                 FROM l_artist_url lau
                          JOIN url u ON lau.column3 = u.column0
                          JOIN link l ON lau.column1 = l.column0
                 WHERE lau.column2 = a.column0
                   AND l.column1 = 352
                 LIMIT 1)                    AS artist_wikidata_id
FROM recording rec
         JOIN artist_credit_name acn ON rec.column3 = acn.column0
         JOIN artist a ON acn.column2 = a.column0
WHERE rec.column1 IN (SELECT recording_mbid FROM temp_canonical_rank);

-- Populate Direct URL Fast Link Identifiers
INSERT INTO target_sqlite.link_lookup (url_identifier, provider, recording_mbid)
SELECT DISTINCT REGEXP_EXTRACT(u.column2, '([^/:]+)$', 1) AS url_identifier,
                CASE
                    WHEN u.column2 ILIKE '%spotify%' THEN 'spotify'
                    WHEN u.column2 ILIKE '%apple%' THEN 'applemusic'
                    WHEN u.column2 ILIKE '%tidal%' THEN 'tidal'
                    ELSE 'unknown'
                    END                                   AS provider,
                rec.column1                               AS recording_mbid
FROM url u
         JOIN l_recording_url lru ON u.column0 = lru.column3
         JOIN link l ON lru.column1 = l.column0
         JOIN recording rec ON lru.column2 = rec.column0
WHERE l.column1 IN (74, 75, 85)
  AND rec.column1 IN (SELECT recording_mbid FROM temp_canonical_rank);

-- Populate Normalized Full Text Search Mappings
INSERT INTO target_sqlite.text_lookup (track_title, release_title, artist_name, recording_mbid)
SELECT DISTINCT LOWER(t.column6) AS track_title,
                LOWER(r.column2) AS release_title,
                LOWER(a.column2) AS artist_name,
                rec.column1      AS recording_mbid
FROM track t
         JOIN recording rec ON t.column2 = rec.column0
         JOIN medium m ON t.column3 = m.column0
         JOIN release r ON m.column1 = r.column0
         JOIN artist_credit_name acn ON t.column7 = acn.column0
         JOIN artist a ON acn.column2 = a.column0
WHERE acn.column1 = 0;