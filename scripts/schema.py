# MusicBrainz Core Column Schema Blueprint Definitions
# Audited and verified against admin/sql/CreateTables.sql

TABLE_SCHEMAS = {
    "mbdump/artist": [
        "id", "gid", "name", "sort_name", "begin_date_year", "begin_date_month",
        "begin_date_day", "end_date_year", "end_date_month", "end_date_day",
        "type", "area", "gender", "comment", "edits_pending", "last_updated",
        "ended", "begin_area", "end_area"
    ],
    "mbdump/artist_credit_name": [
        "artist_credit", "position", "artist", "name", "join_phrase"
    ],
    "mbdump/release": [
        "id", "gid", "name", "artist_credit", "release_group", "status",
        "packaging", "language", "script", "barcode", "comment",
        "edits_pending", "quality", "last_updated"
    ],
    "mbdump/release_group": [
        "id", "gid", "name", "artist_credit", "type", "comment",
        "edits_pending", "last_updated"
    ],
    "mbdump/release_group_primary_type": [
        "id", "name", "parent", "child_order", "description", "gid"
    ],
    "mbdump/medium": [
        "id", "release", "position", "format", "name", "track_count",
        "edits_pending", "last_updated"
    ],
    "mbdump/track": [
        "id", "gid", "recording", "medium", "position", "number", "name",
        "length", "edits_pending", "last_updated", "is_data_track"
    ],
    "mbdump/recording": [
        "id", "gid", "name", "artist_credit", "length", "comment",
        "edits_pending", "last_updated", "video"
    ],
    "mbdump/url": [
        "id", "gid", "url", "description", "edits_pending", "last_updated"
    ],
    "mbdump/l_release_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "mbdump/l_recording_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "mbdump/l_artist_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ],
    "l_artist_url": [
        "id", "link", "entity0", "entity1", "edits_pending", "last_updated"
    ]
}

# Ingestion Table Target Aliases
TABLE_MAPPING = {
    "mbdump/artist": "raw_artist",
    "mbdump/artist_credit_name": "raw_artist_credit_name",
    "mbdump/release": "raw_release",
    "mbdump/release_group": "raw_release_group",
    "mbdump/release_group_primary_type": "raw_rg_type",
    "mbdump/medium": "raw_medium",
    "mbdump/track": "raw_track",
    "mbdump/recording": "raw_recording",
    "mbdump/url": "raw_url",
    "mbdump/l_release_url": "raw_l_release_url",
    "mbdump/l_recording_url": "raw_l_recording_url",
    "mbdump/l_artist_url": "raw_l_artist_url",
    "l_artist_url": "raw_l_artist_url"
}