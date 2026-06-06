# Purpose of this Repo:

A database download, as small as possible, and optimised for finding metadata of a track by either:

1. streaming link
2. trackname + albumname + artistname (fallback)

The database download should be as small as possible, since it's downloaded to the user's app during runtime.

The process for creating this database download needs to be as fast as possible, but also work within the constraints of
GitHub actions/workflow.

I want the following things derived from the original Musicbrainz data dump:

- **all streaming links (from spotify, applemusic, tidal) linked to their metadata** - (this should return 1 link to 1
  metadata, some metadata may not have a link associated with them, they should be ignored)
- **all combinations of track + album + artist linked to their metadata** - (this could, before deduplication, be 1 key to
  1-many metadata) since one track+album+artist combination could lead to many recordings, i want to prioritise the most
  complete type of release group - deluxe album, then regular album, then EP, then single (or equivalent in Musicbrainz
  terms) - resulting in a 1 track + album + artist to 1 metadata

the metadata for a track should contain at least:

- recording mbid
- track title
- track duration

- album mbid
- album title

- release group mbid
- release group title
- release group type

- artist mbid
- artist name
- artist wikidata id