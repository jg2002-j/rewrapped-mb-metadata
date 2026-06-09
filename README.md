# README

# What is this workflow for?

Using a MusicBrainz database dump, this workflow finds and defines a single "canonical" album concept / release group
for every single track/recording. The algorithm prefers the most complete release format, then the earliest within that
format. It then links those to a streaming service URL identifier and a normalised, text-based lookup schema (track
name, album name, and artist name) for efficient downstream queries.

The downstream system using this dataset is a music analytics service. It takes a user's downloaded streaming history,
parses through it, and attempts to calculate statistics from it. However, exported streaming history from music services
often lack the complete metadata structural detail needed to determine when tracks should be counted together.

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

With this logic using MusicBrainz metadata, the downstream service can look up every track first by streaming link, and
as a fallback by matching track, album, and artist, and get a single album concept a.k.a. release group for it.

## Logic for determining "canonical" release group

```text
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

> **Implementation note — grouping key.** Canonical selection does not operate on the raw Recording row directly. To
> collapse trivially different masters of the same performance, recordings are clustered by
> `(normalized_name, artist_credit, length_bucket)`, where `normalized_name` is the normalization contract below and
> `length_bucket = ROUND(length_ms / 5000)` (a ~5-second tolerance). The winning release group is chosen per cluster and
> then mapped back out to every recording in that cluster.

### Criteria for determining "canonical" release group

#### Step 1: Hard exclusions

Reject: `release.status != Official` (Status Code `1`)

#### Step 2: Base score by primary type

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

+50 when the release group's artist credit matches the recording's artist credit. This is implemented as an exact
equality on the MusicBrainz `artist_credit` id (`rg.artist_credit = rec.artist_credit`), which is a strict proxy for
"same lead artist": identical credit ids guarantee the same credited artist(s), though two distinct credits that happen
to share a lead artist will not earn the bonus.

#### Step 4: Earliest release date

When sorting by the earliest release date in DuckDB, a NULL value can completely break the ranking depending on how
NULLS FIRST/LAST is configured.

* Normalise dates during extraction.
* For partial dates like `1978`, pad them to `1978-01-01`.
* For NULL dates, use a coalesce strategy to treat them as far-future dates (e.g.,
  `COALESCE(release_date, '9999-12-31')`) so they never accidentally win against a known release date.
* Invalid calendar dates (e.g. an editor-entered `Feb 30`) are wrapped in `TRY(MAKE_DATE(...))`; the failed date
  becomes `NULL` and falls back to the far-future sentinel rather than aborting the build.

Earlier release date wins.

#### Step 5: Deterministic MBID tie-break

Lowest MBID lexicographically.

# Output

A database download containing all track metadata
from [MusicBrainz PostgreSQL Data Dumps](https://metabrainz.org/datasets/postgres-dumps#musicbrainz), optimised for
lookups via streaming links and track/album/artist names.

# What are the requirements?

In order of priority:

1. **GitHub Actions Constraint Handling:** Operate cleanly inside a free-tier workflow once per week - critically
   remaining within strict RAM and ephemeral storage boundaries.
2. **Optimised Multi-Paradigm Search:** Produce a target SQLite database optimised for lookup by:

* **Streaming link identifiers** (Primary)
* **Track Name**, **Album Name**, and **Artist Name** (Fallback)


3. **Storage Efficiency:** Keep the final compressed engine payload size as small as possible.
4. **Execution Speed:** Complete the compilation and migration workflow in as little time as possible.

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
    recording_mbid             TEXT PRIMARY KEY,
    release_group_mbid         TEXT NOT NULL,
    length                     INTEGER,
    primary_artist_mbid        TEXT,
    primary_artist_name        TEXT,
    primary_artist_wikidata_id TEXT
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
    PRIMARY KEY (provider, url_identifier, recording_mbid)
) WITHOUT ROWID;

CREATE TABLE target_sqlite.text_lookup
(
    id             INTEGER PRIMARY KEY,
    track_title    TEXT NOT NULL,
    release_title  TEXT NOT NULL,
    artist_name    TEXT NOT NULL,
    recording_mbid TEXT NOT NULL
);

```

* There will be 0 to n links in `link_lookup` for every 1 row in `recording`.
* There will be 1 to n tracks in `text_lookup` for every 1 row in `recording`. A single recording can belong to multiple
  releases, so each release variant represents a distinct row on this table so that downstream engines can lookup by
  text.
* There will be 1 row in `release_group` for 1 to n rows in `recording` - this mapping is determined using
  the [above canonical selection logic](https://www.google.com/search?q=%23logic-for-determining-canonical-release-group).

# Additional Indexes

Primary key index trees are automatically generated by the engine. Traditional secondary indexing strategies are called
immediately post-migration:

```sql
CREATE INDEX IF NOT EXISTS idx_text_lookup ON text_lookup (track_title, artist_name, release_title, recording_mbid);
CREATE INDEX IF NOT EXISTS idx_recording_artists_lookup ON recording_artists (recording_mbid, artist_mbid);
CREATE INDEX IF NOT EXISTS idx_recording_artists_details ON recording_artists (position, artist_name, artist_wikidata_id);

```

`idx_text_lookup` covers all three equality-matched fallback columns (`track_title`, `artist_name`, `release_title`) and
appends `recording_mbid`, so the fallback lookup is a covering index seek (no row fetch). `ANALYZE` is run after the
indexes are built so the downstream planner reliably chooses them.

# What does the downstream service need?

1. **Recording length** - used to determine the exact duration of a track in milliseconds.
2. **Release group title** - the unified, "canonical" album concept.
3. **Release group MBID** - utilised to dynamically fetch cover art.
4. **Primary artist wikidata ID** - used to fetch validated artist imagery.
5. **Additional artist names** - used to parse and display full track credits, filling in metadata gaps left by standard
   streaming exports.

It will find these values by:

1. **URL identifier lookup** - Extract the pure identifier string from both this service's lookup schema and the user's
   streaming tracking log, performing an exact indexed match on the `url_identifier` and `provider`. For Apple Music
   album URLs the track identifier lives in the `?i=<id>` query parameter (not the path), so the downstream extractor
   must read `i=` for Apple links and fall back to the last path segment otherwise — matching how the build populates
   `url_identifier` (see Deterministic URL Sanitization).
2. **Text-based fallback lookup** - Apply the normalization contract (below) to `track_title`, `release_title`, and
   `artist_name`, executing byte-for-byte exact matches (`=`).

## Normalization contract (must match on both sides)

The build and the downstream service must normalise text **identically** or the fallback will silently miss. The build
defines this once as a DuckDB macro and reuses it everywhere:

```sql
-- norm(s):
LOWER(s)
  -> strip every character that is not a Unicode letter/number/whitespace:  [^\p{L}\p{N}\s] -> ''
  -> collapse runs of whitespace to a single space:                          \s+            -> ' '
```

There is intentionally no leading/trailing `TRIM` (kept identical to the original behaviour); add one on both sides
together if you later want it.

# Extra links

* [https://musicbrainz.org/doc/MusicBrainz_Database/Schema](https://musicbrainz.org/doc/MusicBrainz_Database/Schema)
* [https://github.com/metabrainz/musicbrainz-server/blob/master/admin/sql/CreateTables.sql](https://github.com/metabrainz/musicbrainz-server/blob/master/admin/sql/CreateTables.sql)

# Technical Architecture Notes

* **GitHub Action Workspace Optimization:** The workflow executes `easimon/maximize-build-space@v10` at initialisation
  to purge unnecessary environments (.NET, Android, Haskell, Docker), freeing up approximately 30GB–35GB of working
  volume space.
* **Modular Pipeline Separation:** The system uses a clean separation of concerns:
* `main.py`: Orchestrates execution runtimes and manages transactional state.
* `schema.py`: Sets up destination SQLite structures and handles index execution.
* `utils.py`: Extracts raw dump files safely and performs disk clean-up.
* `transformations.sql`: Executes the relational data transformations inside DuckDB.


* **Column Projection On Load:** Each manifest table declares a `keep` list, and the loader persists only those columns
  (`CREATE TABLE ... AS SELECT <keep> FROM read_csv(... columns={<all>} ...)`). The full column set is still declared for
  positional alignment, but wide tables (e.g. the 19-column `artist`) are stored as just the 2–3 columns actually used.
  This is the dominant lever for RAM, spill, and final size.
* **Phased Drops + Checkpoint:** Raw tables are dropped as soon as they are no longer needed (the largest, `recording`,
  is normalised once into a compact temp table and dropped first), and a `CHECKPOINT` after the first drop block caps the
  on-disk engine-file high-water-mark.
* **Memory Settings:** `preserve_insertion_order=false` and a bounded `memory_limit`/`temp_directory` keep peak RAM under
  the runner ceiling; the DuckDB engine is closed before the native SQLite index build so its memory is fully released
  first.
* **Deterministic URL Sanitization:** Incoming raw URLs are stripped of query parameters and trailing fragments using
  regular expressions (`[\?#].*`) prior to parsing path segments. The one exception is Apple Music album links, where the
  track id is carried in the `?i=<id>` query parameter and is therefore extracted **before** the query string is
  stripped. This prevents unique query strings (like tracking or session tokens) from fragmenting index matching while
  still capturing the correct per-track identifier.
* **Storage Conservation:** The target database is compacted with `VACUUM INTO` (a defragmented copy that retains indexes
  and `ANALYZE` statistics, then atomically swapped over the original). The transient second copy is cheap relative to
  the ~30GB freed by `maximize-build-space`, and the contiguous layout compresses better. Final compression uses
  `zstd -T0 --ultra -22` (standard `zstd -d` decompresses it; no `--long` window flag is required downstream).
* **Search Alignment Optimization:** The text fallback index leads with all three equality-matched columns
  (`track_title, artist_name, release_title`) so every fallback predicate seeks a contiguous index prefix, and appends
  `recording_mbid` to make the lookup covering.
