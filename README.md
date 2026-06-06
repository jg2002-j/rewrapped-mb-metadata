# README

# What does this GitHub Workflow produce?

A database download, as small as possible, and optimised for finding metadata of a track by either:

# What are the requirements?

- Operate in a free-tier GitHub Workflow once per week/2 weeks
- Produce a small as possible compressed database
- A database optimised for lookups by a) streaming link and b) as a fallback, trackname + albumname + artistname
- Operate in as little time as possible

# What does the database contain?

- **all streaming links (from spotify, applemusic, tidal) linked to their metadata** - (this should return 1 link to 1
  metadata, some metadata may not have a link associated with them, they should be ignored as they will be caught in the
  following group)
- **all combinations of track + album + artist linked to their metadata** - (this could, before deduplication, be 1 key
  to 1...n metadata) since one track+album+artist combination could lead to many recordings, i want to prioritise the
  most
  complete type of release group - deluxe album, then regular album, then EP, then single (or equivalent in Musicbrainz
  terms) - resulting in a 1 track + album + artist to 1 metadata

the metadata for a track should contain at least:

- track mbid
- track title
- track duration

- album mbid (a MusicBrainz "release")
- album title

- release group mbid
- release group title
- release group type

- artist mbid
- artist name
- artist wikidata id

# What does the downstream service use this for?

Using the end result of this GitHub Workflow, my app is for getting a user's extended streaming history from either
Spotify (current usecase) or Apple Music / Tidal (future planned implementations). When the user opens the app and
uploads their extended streaming history (the "setup phase"), the app will download the compressed `metadata_core.db`
from GitHub API, uncompress it, and use it to determine which tracks are functionally the same. Once the raw tracks from
the user's streaming history are matched to their metadata, the `metadata.db` should be deleted since it's a large file
that no longer has any use. The compressed version should stay to avoid the user needed to re-download on subsequent
uses of the app, and is only re-downloaded when the version on GitHub is updated). The rest of the aggregation and
statistic logic is handled on the downstream app.

example, spotify exports could show "Dearly Missed" by "Searows" on its single release, EP release, and album release.
all of these are different with just the spotify data. If the user had x plays on each, and was trying to determine
the "most played albums" in the user's history, the actual count for the full release album would be divided between
them. When I think it's a more useful statistic to consider "Dearly Missed" on only its full album release "Death in the
Business of Whaling", and aggregate all plays on earlier incomplete release (singles, EPs) into that full album release.
This is more obvious when considering album/deluxe releases. The regular album often releases earlier, meaning it could
have many plays that wouldn't merge together with the deluxe's, even though the tracks included on both are exactly the
same track. I want to group them both into the deluxe.

With MusicBrainz metadata I get:

1. a consistent title, album and artist in terms of casing/spelling etc.
2. a duration of the track
3. a way to get album art (release group mbid, queried on metabrainz) and artist images (wikidata id queried on
   wikidata)
4. the ability to group functionally same tracks - release group, which MusicBrainz defines as the concept of a release,
   rather than release which is the various legal/material releases of what is essentially the same album