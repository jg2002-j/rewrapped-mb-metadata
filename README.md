# README

# What does this Workflow do:

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

Immediately reject release groups that are:

* secondary types contains:
* Compilation
* Live
* Remix
* DJ-mix
* Mixtape/Street

Also reject: `release.status != Official`

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

#### Step 3: Bonus points

+50 if the first credited artist on the release group matches the first credited artist on the recording

#### Step 4: Earliest release date

Earlier release date wins

#### Step 5: Deterministic MBID tie-break

lowest MBID lexicographically

#### Notes:

- The downstream service would still keep the original album associated with that specific play, for more accurate
  information. It would just include an extra field: release group, allowing for more useful statistics.
- If filtering removes all candidates: Ignore exclusions and choose the highest-scoring release group from the original
  candidate set.

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

Something like this, open to change for more optimised/performant lookups

```sqlite
create table release_group --simplified down into a single canonical release group, not an actual MusicBrainz release group
(
    release_group_mbid  text primary key, --need this for album art lookup, and also unique identifier for release group in downstream service
    release_group_title text not null,    --need this for text for release group
    release_group_type  text
);

create table recording
(
    recording_mbid     text primary key,
    release_group_mbid text not null,
    length             integer, --average track length for this recording, need this in downstream service
    foreign key (release_group_mbid) references release_group (release_group_mbid)
);

create table recording_artists
(
    position           integer not null, -- 1 = primary artist
    recording_mbid     text    not null,
    artist_mbid        text    not null,
    artist_wikidata_id text,             --need this for artist art lookup
    foreign key (recording_mbid) references recording (recording_mbid),
    primary key (recording_mbid, artist_mbid)
);

create table link_lookup
(
    url            text primary key,
    provider       text, --spotify, applemusic, tidal, etc.
    recording_mbid text not null,
    foreign key (recording_mbid) references recording (recording_mbid)
);

create table text_lookup --the fallback text lookup, equivalent to every track appearance on every release
(
    id             integer primary key,
    --keep these separate so i can search by all/some of them, add collate nocase so indexes aren't case sensitive
    track_title    text not null collate nocase,
    release_title  text not null collate nocase,
    artist_name    text not null collate nocase, --primary artist only, joins for more artist information
    --
    recording_mbid text not null,
    foreign key (recording_mbid) references recording (recording_mbid)
);
```

- There will be 0 to n links in `link_lookup` for every 1 row in `recording`.
- There will be 1 to n tracks in `text_lookup` for every 1 row in `recording`. A single record contains 1 to
  n releases, so each release will be a new row on that table so that downstream service can look up by a track name +
  release name + artist name
- There will be 1 row in `release group` for 1 to n rows in `recording` - this will be determined using
  the [above logic](#logic-for-determining-canonical-release-group)

# Needed Indexes

```sql
create index idx_recording_lookup
    on link_lookup (url);

create index idx_text_lookup
    on text_lookup (track_title, artist_name, release_title, recording_mbid);

create index idx_recording_release_group
    on recording (release_group_mbid);
```

# Extra links

https://musicbrainz.org/doc/MusicBrainz_Database/Schema
https://github.com/metabrainz/musicbrainz-server/blob/master/admin/sql/CreateTables.sql

# Further notes

- The DuckDB processing script needs to traverse the relationship graph: `URL → Release → Medium → Track → Recording`:
  the source of those URLs during your DuckDB processing must pull from the Release-level relationships.
- No vacuum on final database since that'll require double the amount of storage, and compression will achieve a similar
  effect anyway
- Use DuckDB or equivalent to stream the MusicBrainz databases without needing to use Postgres which would dramatically
  increase storage use
- Normalisation on the `text_lookup` table would be done downstream, so the same normalisation algorithm can be applied
  to this text as it can be from the user's streaming history fields
- Text lookups on downstream service must be done track, then artist, then album, the order matches the created index.