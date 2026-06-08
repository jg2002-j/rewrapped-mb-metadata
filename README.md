# README

# What is this workflow for?

Using MusicBrainz database dump, finds and defines a single "canonical" album concept / release group for every single
track/recording. The algorithm here prefers most complete release format then the earliest within that format. It then
links those to a streaming service url, and a text-based (track name, album name, and artist name) for easy lookup.

The downstream service using this is a music analytics service. It takes a user's downloaded streaming history, parses
through it and attempts to calculate statistics from it. However, exported streaming history from music services often
lack complete detail needed to determine when tracks should be counted together.

For example:

```json
[
  {
    "ts": "2025-10-22T23:00:36Z",
    "platform": "ios",
    "ms_played": 228639,
    "conn_country": "IE",
    "ip_addr": "ip address",
    "master_metadata_track_name": "we can't be friends (wait for your love)",
    "master_metadata_album_artist_name": "Ariana Grande",
    "master_metadata_album_album_name": "eternal sunshine",
    "spotify_track_uri": "spotify:track:51ZQ1vr10ffzbwIjDCwqm4",
    "episode_name": null,
    "episode_show_name": null,
    "spotify_episode_uri": null,
    "audiobook_title": null,
    "audiobook_uri": null,
    "audiobook_chapter_uri": null,
    "audiobook_chapter_title": null,
    "reason_start": "trackdone",
    "reason_end": "trackdone",
    "shuffle": false,
    "skipped": false,
    "offline": false,
    "offline_timestamp": 1761173807,
    "incognito_mode": false
  },
  {
    "ts": "2025-04-20T23:34:47Z",
    "platform": "ios",
    "ms_played": 228639,
    "conn_country": "GB",
    "ip_addr": "ip address",
    "master_metadata_track_name": "we can't be friends (wait for your love)",
    "master_metadata_album_artist_name": "Ariana Grande",
    "master_metadata_album_album_name": "eternal sunshine deluxe: brighter days ahead",
    "spotify_track_uri": "spotify:track:3zSnPhuucEb9JbFSxKVcIn",
    "episode_name": null,
    "episode_show_name": null,
    "spotify_episode_uri": null,
    "audiobook_title": null,
    "audiobook_uri": null,
    "audiobook_chapter_uri": null,
    "audiobook_chapter_title": null,
    "reason_start": "trackdone",
    "reason_end": "trackdone",
    "shuffle": true,
    "skipped": false,
    "offline": false,
    "offline_timestamp": 1745191858,
    "incognito_mode": false
  }
]
```

Without any way to discern that these are the same track, this would count separately. If each track had 15 plays, and I
wanted to see the total plays for `eternal sunshine` it would say 15, rather than 30, despite
`eternal sunshine deluxe: brighter days ahead` being the same album, just a deluxe version.

With this logic using MusicBrainz metadata, the downstream service will be able to look up every track first by
streaming link, and as a fallback by matching track album and artist, and get a single album concept a.k.a. release
group for it.

## Logic for determining "canonical" release group

```
For each MusicBrainz Recording
    ↓
Get all Releases associated with that recording
    ↓
Get all streaming URLs associated with that recording
    ↓
Get all the Release Groups containing the Recording
    ↓
Filter all Release Groups by criteria
    ↓
Return highest-ranking Release Group
```

### Criteria for determining "canonical" release group

#### Step 1: Hard exclusions

Reject: `release.status != Official`

#### Step 2: Base score by primary type

Something like:

| Release Group Type | Score |
|--------------------|-------|
| Album              | 1000  |
| EP                 | 900   |
| Single             | 800   |
| Soundtrack         | 700   |
| Other              | 600   |
| Broadcast          | 500   |
| Audio Drama        | 400   |
| Audiobook          | 300   |
| Spokenword         | 200   |
| Interview          | 100   |
| Demo               | 50    |
| Live               | -1000 |
| Remix              | -1100 |
| DJ-mix             | -1200 |
| Mixtape/Street     | -1300 |
| Compilation        | -1400 |

#### Step 3: Bonus points

+50 if the first credited artist on the release group matches the first credited artist on the recording

#### Step 4: Earliest release date

When sorting by the earliest release date in DuckDB, a NULL value can completely break the ranking depending on how
NULLS FIRST/LAST is configured.

- Normalize your dates during extraction.
- For partial dates like `1978`, pad them to `1978-01-01`.
- For NULL dates, use a coalesce strategy to treat them as far-future dates (e.g.,
  `COALESCE(release_date, '9999-12-31')`) so they never accidentally win against a known release date.

Earlier release date wins

#### Step 5: Deterministic MBID tie-break

lowest MBID lexicographically

# Output

A database download containing all track metadata
from [MusicBrainz PostgreSQL Data Dumps](https://metabrainz.org/datasets/postgres-dumps#musicbrainz), optimised for
lookups via streaming links and track/album/artist names.

# What are the requirements?

In order of priority:

1. Operate in a free-tier GitHub Workflow once per week/2 weeks - critically must remain within RAM and storage
   constraints of this.
2. Produce a database optimised for lookups by a) **streaming link** and b) as a fallback, **trackname**, **albumname**,
   and **artistname**
3. Make the compressed database end result as small as possible
4. Run the whole workflow in as little time as possible

# What does the database contain?

```sqlite
CREATE TABLE target_sqlite.release_group
(
    release_group_mbid  TEXT PRIMARY KEY,
    release_group_title TEXT NOT NULL,
    release_group_type  TEXT
);

CREATE TABLE target_sqlite.recording
(
    recording_mbid      TEXT PRIMARY KEY,
    release_group_mbid  TEXT NOT NULL,
    length              INTEGER,
    primary_artist_mbid TEXT,
    primary_artist_name TEXT
);

CREATE TABLE target_sqlite.recording_artists
(
    recording_mbid     TEXT    NOT NULL,
    artist_mbid        TEXT    NOT NULL,
    position           INTEGER NOT NULL,
    artist_name        TEXT    NOT NULL,
    artist_wikidata_id TEXT
);

CREATE TABLE target_sqlite.link_lookup
(
    url_identifier TEXT NOT NULL,
    provider       TEXT,
    recording_mbid TEXT NOT NULL,
    PRIMARY KEY (url_identifier, provider)
);

CREATE TABLE target_sqlite.text_lookup
(
    id             INTEGER PRIMARY KEY,
    track_title    TEXT NOT NULL,
    release_title  TEXT NOT NULL,
    artist_name    TEXT NOT NULL,
    recording_mbid TEXT NOT NULL
);
```

- There will be 0 to n links in `link_lookup` for every 1 row in `recording`.
- There will be 1 to n tracks in `text_lookup` for every 1 row in `recording`. A single record contains 1 to
  n releases, so each release will be a new row on that table so that downstream service can look up by a track name +
  release name + artist name
- There will be 1 row in `release group` for 1 to n rows in `recording` - this will be determined using
  the [above logic](#logic-for-determining-canonical-release-group)

# Additional Needed Indexes

* Primary key indexes are created out of the box, so aren't needed to be manually created after

```sql
CREATE INDEX IF NOT EXISTS idx_text_lookup ON text_lookup (track_title, artist_name);
CREATE INDEX IF NOT EXISTS idx_recording_artists_lookup ON recording_artists (recording_mbid, artist_mbid);
CREATE INDEX IF NOT EXISTS idx_recording_artists_details ON recording_artists (position, artist_name, artist_wikidata_id);
```

# What does the downstream service need?

1. **Recording length** - used to learn the duration in ms of a track
2. **Release group title** - this is the "canonical" album concept/release group
3. **Release group MBID** - used to fetch album artwork
4. **Primary artist wikidata ID** - used to fetch the (primary) artist image
5. **Additional artist names** - Used to flesh out the credits of a song, as Spotify Extended Streaming History by
   default only includes the primary artist

It will find these values by:

1. URL lookup - strip domain out of both this service URLs and the user's listen URL, then use an exact match on the url
   and the provider
2. Failing that, a text-based lookup - normalise the text on `track_title`, `release_title` and `artist_name` and then
   match to user's listen values

# Extra links

- https://musicbrainz.org/doc/MusicBrainz_Database/Schema
- https://github.com/metabrainz/musicbrainz-server/blob/master/admin/sql/CreateTables.sql

# Further notes

- Include GitHub Action like `easimon/maximize-build-space` at the very start of the workflow to remove pre-installed
  .NET, Android, and Haskell runtimes. This will free up roughly 30GB–35GB of extra space.
- Use a script to extract only the specific table files that are needed (recording, release, track, etc.) one by one,
  stream them directly into DuckDB tables, and immediately delete the source text file before moving to the next one.
- Explicitly configure DuckDB’s memory limit and spilling options at the start of the script.
- The DuckDB processing script needs to traverse the relationship graph: `[url] ── via l_recording_url ──> [recording]`:
    - `url`: Contains the actual target string (e.g., https://open.spotify.com/track/...)
    - `l_recording_url`: The entity-to-entity link table mapping recording IDs to url IDs
    - `link / link_type`: To filter out only the streaming-service relationship types (e.g. "stream for free" or "
      purchase for download")
- No vacuum on final database since that'll require double the amount of storage, and compression will achieve a similar
  effect anyway
- Use DuckDB or equivalent to stream the MusicBrainz databases without needing to use Postgres which would dramatically
  increase storage use
- Strip domains out of urls in this service, as using a `like '%something%'` match breaks the indexing completely. On a
  user's downloaded history, each service will have their urls in a set format, e.g. Spotify links are always
  `spotify:track:[id]` and they should be stripped down too.
- Normalisation on the `text_lookup` table would be done before inserting, as SQLite’s built-in NOCASE collation only
  handles 7-bit ASCII characters - meaning texts with special characters won't be treated case-insensitive. So, the text
  must be normalised beforehand so text matching becomes a highly efficient, byte-for-byte exact comparison (=), keeping
  lookup speeds incredibly fast.
    - Ensure the same normalisation algorithm is applied downstream to the user's streaming history fields, so text
      lookup works
- Text lookups on downstream service must be done track, then artist, then album, the order matches the created index.
  `release_title` and `recording_mbid` aren't indexed as this saves space: Even without release_title in the index,
  filtering down millions of records by the combination of Track Title + Artist Name usually narrows the search results
  down to just 1–3 rows. SQLite can then scan those remaining rows on disk to check the release_title instantly,
  preserving fast performance while saving massive amounts of disk space.
- The downstream service would still keep the original album associated with that specific play, for more accurate
  information. It would just include an extra field: release group, allowing for more useful statistics.