"""
MusicBrainz Core Column Schema Blueprint Definitions
Audited and verified against https://github.com/metabrainz/musicbrainz-server/blob/master/admin/sql/CreateTables.sql
Schema and Mapping Definition Config for MusicBrainz Ingestion Engine.
Handles structural mapping, complete column arrays, and optimized projection subsets.
"""

TABLE_MAPPING = {
    "mbdump/artist": "raw_artist",
    "mbdump/artist_credit_name": "raw_artist_credit_name",
    "mbdump/l_artist_url": "raw_l_artist_url",
    "mbdump/l_recording_url": "raw_l_recording_url",
    "mbdump/l_release_url": "raw_l_release_url",
    "mbdump/medium": "raw_medium",
    "mbdump/recording": "raw_recording",
    "mbdump/release": "raw_release",
    "mbdump/release_group": "raw_release_group",
    "mbdump/release_group_primary_type": "raw_rg_type",
    "mbdump/track": "raw_track",
    "mbdump/url": "raw_url",
}

TABLE_SCHEMAS = {
    "mbdump/artist": [
        "id", "gid", "name", "sort_name", "begin_date_year", "begin_date_month",
        "begin_date_day", "end_date_year", "end_date_month", "end_date_day",
        "type", "area", "gender", "comment", "edits_pending", "last_updated", "ended"
    ],
    "mbdump/artist_credit_name": [
        "artist_credit", "position", "artist", "name", "join_phrase"
    ],
    "mbdump/l_artist_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "mbdump/l_recording_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "mbdump/l_release_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "mbdump/medium": [
        "id", "release", "position", "format", "name", "track_count",
        "edits_pending", "last_updated"
    ],
    "mbdump/recording": [
        "id", "gid", "name", "artist_credit", "length", "comment",
        "edits_pending", "last_updated", "video"
    ],
    "mbdump/release": [
        "id", "gid", "name", "artist_credit", "release_group", "status",
        "packaging", "country", "language", "script", "date_year", "date_month",
        "date_day", "barcode", "comment", "edits_pending", "quality", "last_updated"
    ],
    "mbdump/release_group": [
        "id", "gid", "name", "artist_credit", "type", "comment",
        "edits_pending", "last_updated"
    ],
    "mbdump/release_group_primary_type": [
        "id", "name", "parent", "child_order", "description"
    ],
    "mbdump/track": [
        "id", "gid", "recording", "medium", "position", "number", "name",
        "artist_credit", "length", "edits_pending", "last_updated", "is_data_track"
    ],
    "mbdump/url": [
        "id", "gid", "url", "description", "edits_pending", "last_updated"
    ]
}

# PROJECTION PUSHDOWN OPTIMIZATION:
# Only columns declared here are selected from the stream and written to memory.
NEEDED_COLUMNS = {
    "raw_artist": ["id", "gid", "name"],
    "raw_artist_credit_name": ["artist_credit", "position", "artist"],
    "raw_release": ["id", "gid", "name", "release_group"],
    "raw_release_group": ["id", "gid", "name", "artist_credit", "type"],
    "raw_rg_type": ["id", "name"],
    "raw_medium": ["id", "release"],
    "raw_track": ["id", "recording", "medium", "name", "length"],
    "raw_recording": ["id", "gid"],
    "raw_url": ["id", "url"],
    "raw_l_artist_url": ["entity0", "entity1"],
    "raw_l_recording_url": ["entity0", "entity1"]
}