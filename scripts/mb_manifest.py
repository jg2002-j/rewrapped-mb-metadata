"""
MusicBrainz Core Dump Ingestion Contract Manifest
Source of Truth aligned with:
 - https://musicbrainz.org/doc/MusicBrainz_Database/Schema
 - https://github.com/metabrainz/musicbrainz-server/blob/master/admin/sql/CreateTables.sql

This dictionary acts as the immutable structural specification for the raw data dumps.
Modifications to column layouts by MetaBrainz should be updated here.

Each table also declares a "keep" list: the subset of columns the transformation
pipeline actually consumes. The loader declares every column for positional
alignment but only persists the "keep" subset, which dramatically reduces the
working-set memory, spill, and final size.
"""

MUSICBRAINZ_MANIFEST = {
    "artist": {
        "raw_table_name": "raw_artist",
        "dump_file_name": "artist",
        "description": "Individual performers, groups, collaborations, or entities involved in performance credits.",
        "keep": ["id", "gid", "name"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal database serial surrogate key"},
            "gid": {"type": "VARCHAR", "pos": 1, "nullable": False, "desc": "Global Unique Identifier (MBID)"},
            "name": {"type": "VARCHAR", "pos": 2, "nullable": False, "desc": "Canonical display name of the artist"},
            "sort_name": {"type": "VARCHAR", "pos": 3, "nullable": False, "desc": "Alphabetized entry formatting (e.g. 'Presley, Elvis')"},
            "begin_date_year": {"type": "INTEGER", "pos": 4, "nullable": True, "desc": "Year tracking origin or birth"},
            "begin_date_month": {"type": "INTEGER", "pos": 5, "nullable": True, "desc": "Month tracking origin or birth"},
            "begin_date_day": {"type": "INTEGER", "pos": 6, "nullable": True, "desc": "Day tracking origin or birth"},
            "end_date_year": {"type": "INTEGER", "pos": 7, "nullable": True, "desc": "Year tracking death or termination"},
            "end_date_month": {"type": "INTEGER", "pos": 8, "nullable": True, "desc": "Month tracking death or termination"},
            "end_date_day": {"type": "INTEGER", "pos": 9, "nullable": True, "desc": "Day tracking death or termination"},
            "type": {"type": "INTEGER", "pos": 10, "nullable": True, "desc": "Foreign key to type classifications"},
            "area": {"type": "INTEGER", "pos": 11, "nullable": True, "desc": "Foreign key to geographic regions"},
            "gender": {"type": "INTEGER", "pos": 12, "nullable": True, "desc": "Foreign key to gender classifications"},
            "comment": {"type": "VARCHAR", "pos": 13, "nullable": True, "desc": "Disambiguation string modifier"},
            "edits_pending": {"type": "INTEGER", "pos": 14, "nullable": False, "desc": "Open moderation requests pending apply"},
            "last_updated": {"type": "VARCHAR", "pos": 15, "nullable": False, "desc": "Database alteration tracking timestamp"},
            "ended": {"type": "BOOLEAN", "pos": 16, "nullable": False, "desc": "Boolean state flag tracking activity boundary"},
            "begin_area": {"type": "INTEGER", "pos": 17, "nullable": True, "desc": "Origin geography foreign reference key"},
            "end_area": {"type": "INTEGER", "pos": 18, "nullable": True, "desc": "Termination geography foreign reference key"}
        }
    },
    "artist_credit_name": {
        "raw_table_name": "raw_artist_credit_name",
        "dump_file_name": "artist_credit_name",
        "description": "Join table breaking out individual billing positions and credits inside a multi-artist composite.",
        "keep": ["artist_credit", "position", "artist", "name"],
        "columns": {
            "artist_credit": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Composite cluster ID identifier"},
            "position": {"type": "INTEGER", "pos": 1, "nullable": False, "desc": "Billing array indexing sort sequence (0 = primary)"},
            "artist": {"type": "INTEGER", "pos": 2, "nullable": False, "desc": "Foreign key to raw_artist.id"},
            "name": {"type": "VARCHAR", "pos": 3, "nullable": False, "desc": "Specific textual credit variation name override"},
            "join_phrase": {"type": "VARCHAR", "pos": 4, "nullable": True, "desc": "Grammatical connecting string to next index"}
        }
    },
    "recording": {
        "raw_table_name": "raw_recording",
        "dump_file_name": "recording",
        "description": "Distinct audio tracks, cuts, or masters characterized by an intrinsic length duration metric.",
        "keep": ["id", "gid", "name", "artist_credit", "length"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal database serial surrogate key"},
            "gid": {"type": "VARCHAR", "pos": 1, "nullable": False, "desc": "Global Unique Identifier (MBID)"},
            "name": {"type": "VARCHAR", "pos": 2, "nullable": False, "desc": "Title of the master piece of audio"},
            "artist_credit": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Foreign key to artist_credit group contexts"},
            "length": {"type": "INTEGER", "pos": 4, "nullable": True, "desc": "Master execution duration tracked in milliseconds"},
            "comment": {"type": "VARCHAR", "pos": 5, "nullable": True, "desc": "Disambiguation string modifier"},
            "edits_pending": {"type": "INTEGER", "pos": 6, "nullable": False, "desc": "Open moderation requests pending apply"},
            "last_updated": {"type": "VARCHAR", "pos": 7, "nullable": False, "desc": "Database alteration tracking timestamp"},
            "video": {"type": "INTEGER", "pos": 8, "nullable": False, "desc": "Boolean flag tracking if asset is video-primary"}
        }
    },
    "track": {
        "raw_table_name": "raw_track",
        "dump_file_name": "track",
        "description": "Physical realization of a recording on a specific media line positioning.",
        "keep": ["id", "recording", "medium", "name"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal surrogate identifier"},
            "gid": {"type": "VARCHAR", "pos": 1, "nullable": False, "desc": "Global Unique Track Identifier"},
            "recording": {"type": "INTEGER", "pos": 2, "nullable": False, "desc": "Foreign key link back to raw_recording.id"},
            "medium": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Foreign key link back to raw_medium.id"},
            "position": {"type": "INTEGER", "pos": 4, "nullable": False, "desc": "Track execution matrix integer position ordering"},
            "number": {"type": "VARCHAR", "pos": 5, "nullable": False, "desc": "Literal textual representation index on asset packaging"},
            "name": {"type": "VARCHAR", "pos": 6, "nullable": False, "desc": "Explicit track title name override layout"},
            "artist_credit": {"type": "INTEGER", "pos": 7, "nullable": False, "desc": "Per-track specific artist billing structure"},
            "length": {"type": "INTEGER", "pos": 8, "nullable": True, "desc": "Track specific duration override in milliseconds"},
            "edits_pending": {"type": "INTEGER", "pos": 9, "nullable": False, "desc": "Open moderation requests pending apply"},
            "last_updated": {"type": "VARCHAR", "pos": 10, "nullable": False, "desc": "Database alteration tracking timestamp"},
            "is_data_track": {"type": "INTEGER", "pos": 11, "nullable": False, "desc": "Boolean flag tracking text/data content streams"}
        }
    },
    "medium": {
        "raw_table_name": "raw_medium",
        "dump_file_name": "medium",
        "description": "Multi-disc segments or volume layers separating distinct groupings in a release package.",
        "keep": ["id", "release"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal surrogate identifier"},
            "release": {"type": "INTEGER", "pos": 1, "nullable": False, "desc": "Foreign key parent binding directly to raw_release.id"},
            "position": {"type": "INTEGER", "pos": 2, "nullable": False, "desc": "Volume arrangement numerical order"},
            "format": {"type": "INTEGER", "pos": 3, "nullable": True, "desc": "Classification format code identifier"},
            "name": {"type": "VARCHAR", "pos": 4, "nullable": True, "desc": "Specific title labeling for volume subset"},
            "last_updated": {"type": "VARCHAR", "pos": 5, "nullable": False, "desc": "Database alteration tracking timestamp"},
            "edits_pending": {"type": "INTEGER", "pos": 6, "nullable": False, "desc": "Open moderation requests pending apply"},
            "gid": {"type": "VARCHAR", "pos": 7, "nullable": False, "desc": "Global Unique Identifier (UUID)"}
        }
    },
    "release": {
        "raw_table_name": "raw_release",
        "dump_file_name": "release",
        "description": "Tangible trade physical issuance or programmatic digital launch containing mediums.",
        "keep": ["id", "name", "artist_credit", "release_group", "status"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal surrogate identifier"},
            "gid": {"type": "VARCHAR", "pos": 1, "nullable": False, "desc": "Global Unique Release Identifier (MBID)"},
            "name": {"type": "VARCHAR", "pos": 2, "nullable": False, "desc": "Literal public album asset title name"},
            "artist_credit": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Foreign credit mapping key reference"},
            "release_group": {"type": "INTEGER", "pos": 4, "nullable": False, "desc": "Foreign key reference linking to raw_release_group.id"},
            "status": {"type": "INTEGER", "pos": 5, "nullable": True, "desc": "Issuance authentication status (Status 1 = Official)"},
            "packaging": {"type": "INTEGER", "pos": 6, "nullable": True, "desc": "Retail packaging container material classification"},
            "language": {"type": "INTEGER", "pos": 7, "nullable": True, "desc": "Primary track listing text language filter id"},
            "script": {"type": "INTEGER", "pos": 8, "nullable": True, "desc": "Character set typographical notation system reference"},
            "barcode": {"type": "VARCHAR", "pos": 9, "nullable": True, "desc": "Global commercial UPC/EAN logistics identity code"},
            "comment": {"type": "VARCHAR", "pos": 10, "nullable": True, "desc": "Disambiguation string modifier"},
            "edits_pending": {"type": "INTEGER", "pos": 11, "nullable": False, "desc": "Open moderation requests pending apply"},
            "quality": {"type": "INTEGER", "pos": 12, "nullable": False, "desc": "Systemic data-entry curation verification score tier"},
            "last_updated": {"type": "VARCHAR", "pos": 13, "nullable": False, "desc": "Database alteration tracking timestamp"}
        }
    },
    "release_group": {
        "raw_table_name": "raw_release_group",
        "dump_file_name": "release_group",
        "description": "Abstract meta-record binding core albums, deluxe distributions, and local versions into an overarching single work group.",
        "keep": ["id", "gid", "name", "artist_credit", "type"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal database surrogate unique identifier"},
            "gid": {"type": "VARCHAR", "pos": 1, "nullable": False, "desc": "Global Unique Release Group Identifier (MBID)"},
            "name": {"type": "VARCHAR", "pos": 2, "nullable": False, "desc": "Abstract collection umbrella release title name"},
            "artist_credit": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Foreign key reference tracking author credits mapping"},
            "type": {"type": "INTEGER", "pos": 4, "nullable": True, "desc": "Classification pointer tracking base format definitions"},
            "comment": {"type": "VARCHAR", "pos": 5, "nullable": True, "desc": "Disambiguation string modifier"},
            "edits_pending": {"type": "INTEGER", "pos": 6, "nullable": False, "desc": "Open moderation requests pending apply"},
            "last_updated": {"type": "VARCHAR", "pos": 7, "nullable": False, "desc": "Database alteration tracking timestamp"}
        }
    },
    "release_country": {
        "raw_table_name": "raw_release_country",
        "dump_file_name": "release_country",
        "description": "Market release date tracker detailing release distribution windows inside explicitly declared geo zones.",
        "keep": ["release", "date_year", "date_month", "date_day"],
        "columns": {
            "release": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Primary identifier linking back to raw_release.id"},
            "country": {"type": "INTEGER", "pos": 1, "nullable": False, "desc": "Geographic territory country code match tracking reference"},
            "date_year": {"type": "INTEGER", "pos": 2, "nullable": True, "desc": "Calendar year calculation point"},
            "date_month": {"type": "INTEGER", "pos": 3, "nullable": True, "desc": "Calendar month calculation point"},
            "date_day": {"type": "INTEGER", "pos": 4, "nullable": True, "desc": "Calendar day calculation point"}
        }
    },
    "release_unknown_country": {
        "raw_table_name": "raw_release_unknown_country",
        "dump_file_name": "release_unknown_country",
        "description": "Market release date tracker fallback holding timestamps where explicit country allocation boundaries are untracked.",
        "keep": ["release", "date_year", "date_month", "date_day"],
        "columns": {
            "release": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Primary identifier linking back to raw_release.id"},
            "date_year": {"type": "INTEGER", "pos": 1, "nullable": True, "desc": "Calendar year calculation point"},
            "date_month": {"type": "INTEGER", "pos": 2, "nullable": True, "desc": "Calendar month calculation point"},
            "date_day": {"type": "INTEGER", "pos": 3, "nullable": True, "desc": "Calendar day calculation point"}
        }
    },
    "url": {
        "raw_table_name": "raw_url",
        "dump_file_name": "url",
        "description": "External target Uniform Resource Locator web paths tracking public reference points.",
        "keep": ["id", "url"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal database surrogate unique identifier"},
            "gid": {"type": "VARCHAR", "pos": 1, "nullable": False, "desc": "Global URL Node tracking Identifier"},
            "url": {"type": "VARCHAR", "pos": 2, "nullable": False, "desc": "Literal fully qualified external target resource address link"},
            "edits_pending": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Open moderation requests pending apply"},
            "last_updated": {"type": "VARCHAR", "pos": 4, "nullable": False, "desc": "Database alteration tracking timestamp"}
        }
    },
    "l_recording_url": {
        "raw_table_name": "raw_l_recording_url",
        "dump_file_name": "l_recording_url",
        "description": "Entity relationship relational junction map linking specific recordings out to target URLs.",
        "keep": ["entity0", "entity1"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal link instance tracker key"},
            "link": {"type": "INTEGER", "pos": 1, "nullable": False, "desc": "Structural linkage semantic structural behavior lookup id"},
            "entity0": {"type": "INTEGER", "pos": 2, "nullable": False, "desc": "Left foreign node relation mapping to raw_recording.id"},
            "entity1": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Right foreign node relation mapping to raw_url.id"},
            "edits_pending": {"type": "INTEGER", "pos": 4, "nullable": False, "desc": "Open moderation requests pending apply"},
            "last_updated": {"type": "VARCHAR", "pos": 5, "nullable": False, "desc": "Database alteration tracking timestamp"},
            "link_order": {"type": "INTEGER", "pos": 6, "nullable": False, "desc": "Order position descriptor identifier for relationships"}
        }
    },
    "l_artist_url": {
        "raw_table_name": "raw_l_artist_url",
        "dump_file_name": "l_artist_url",
        "description": "Entity relationship relational junction map linking individual artists out to external profiles (e.g. Wikidata).",
        "keep": ["entity0", "entity1"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Internal link instance tracker key"},
            "link": {"type": "INTEGER", "pos": 1, "nullable": False, "desc": "Structural linkage semantic structural behavior lookup id"},
            "entity0": {"type": "INTEGER", "pos": 2, "nullable": False, "desc": "Left foreign node relation mapping to raw_artist.id"},
            "entity1": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Right foreign node relation mapping to raw_url.id"},
            "edits_pending": {"type": "INTEGER", "pos": 4, "nullable": False, "desc": "Open moderation requests pending apply"},
            "last_updated": {"type": "VARCHAR", "pos": 5, "nullable": False, "desc": "Database alteration tracking timestamp"},
            "link_order": {"type": "INTEGER", "pos": 6, "nullable": False, "desc": "Order position descriptor identifier for relationships"}
        }
    },
    "release_group_primary_type": {
        "raw_table_name": "raw_rg_type",
        "dump_file_name": "release_group_primary_type",
        "description": "Static reference dictionary mapping release group classification integer ids out to physical names.",
        "keep": ["id", "name"],
        "columns": {
            "id": {"type": "INTEGER", "pos": 0, "nullable": False, "desc": "Primary key definition dictionary tracking index"},
            "name": {"type": "VARCHAR", "pos": 1, "nullable": False, "desc": "Format string display literal label (e.g., 'Album', 'Single')"},
            "parent": {"type": "INTEGER", "pos": 2, "nullable": True, "desc": "Hierarchy grouping parent code structural lookup key"},
            "child_order": {"type": "INTEGER", "pos": 3, "nullable": False, "desc": "Internal arrangement presentation weight"},
            "description": {"type": "VARCHAR", "pos": 4, "nullable": True, "desc": "Text description outlining type limits"}
        }
    }
}
