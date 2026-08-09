# Media Organiser (offline)

[![Python 3.11](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.11)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml)
![Coverage](coverage/badge.svg?)

A fast, offline Python tool that **sorts** your media library, **copies/moves** sidecar subtitles, and **writes/merges NFOs**—no internet calls and no tag embedding in the media files.

```
/movies/<Title>/<Title> (<quality>).<ext>
/tv/<Series>/season-<NN>/<Series> - S<NN>E<NN>(-E<NN>) (<quality>).<ext>
```

* Detects TV episodes (`S02E01`, `S02E01-02`, `2x01`, `S02 01`, `S2E1`).
* Names movies robustly (scene cleanup, prefers `Title (Year)` folders, and nearby **.nfo** `<title>` when present).
* Normalises quality (`4k/uhd → 2160p`, `8k → 4320p`, else uses found token or `Other`).
* Copies/moves **subtitle sidecars** (`.srt .ass .ssa .sub .idx .vtt .sup .ttml .dfxp .smi`) next to the destination video—preserving language/flags in the filename.
* **Duplicate detection** on the destination side (`off | name | size | hash`).
* **Writes NFOs** and **merges** with any existing NFOs (source or destination): *existing fields win; only missing values are filled*.
* Optional **poster sieve** (no downloads) to keep or quarantine suspected spammy covers (e.g., “YIFY/YTS” branded posters).

> **Always offline.** No OMDb/TMDb lookups. No metadata written into the media files.

---

## CI Status

| Python version |                                                                                                Status                                                                                                 |
| -------------: |:-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------:|
|           3.10 | [![3.10](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.10)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml) |
|           3.11 | [![3.11](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.11)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml) |
|           3.12 | [![3.12](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.12)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml) |

This project is tested automatically on each push and pull request using [pytest](https://docs.pytest.org/) across Python 3.10–3.12. Coverage is collected and stored as build artifacts.

---

## Testing

This repository uses **pytest** for integration and unit testing. Major test coverage includes:

* **CLI integration** – end-to-end execution of organise/move and NFO writing
* **Duplicate detection** – hash-based and size-based dupe handling
* **Naming and quality detection** – including `SxxExx`, `2xNN`, and `UHD/8K` normalization
* **NFO writing and merge-first logic**
* **Poster sieve** (optional; only runs if `Pillow` is installed)
* **Sidecar subtitles** handling

### Running tests locally

```bash
poetry install
poetry run pytest -q
```

Generate coverage:

```bash
poetry run pytest --cov=1786269558media_organiser --cov-report=term --cov-report=xml
```

---

## Docker / Compose

Media Organiser is Docker-ready. Build from the **project root** (where `Dockerfile` and `docker-compose.yml` live). Example:

```yaml
# docker-compose.yml
services:
  media-organiser:
    build: .
    container_name: media_organiser
    restart: unless-stopped
    environment:
      IMPORT_DIR: /data/import
      LIB_DIR: /data/library
      MUSIC_LIB_DIR: /data/music
    ports:
      - "6767:6767"
    volumes:
      - /data/import:/data/import
      - /data/content:/data/library
      - /data/music:/data/music
```

The entrypoint (`entrypoint.sh`) does three things:

1. **One-off organise** on startup (import → library).
2. **Background watch** of the import folder with `inotifywait`; when files change, it runs the organiser again (with `--dupe-mode name --emit-nfo all --carry-posters keep`).
3. **Web upload UI** on port **6767** (Flask). You can upload any files into the import folder via the browser. Use “choose folder” to upload a directory and preserve its structure (NFO, subtitles, images).

So you can either drop files into the mounted import directory on the host, or use the web interface at `http://<host>:6767/` to upload; the container will organise them into the library.

---

## Project layout

```
media_organiser/
  __init__.py
  __main__.py          # allows: python -m media_organiser ...
  cli.py               # CLI + orchestration
  constants.py         # regexes, extensions, shared constants
  naming.py            # title/series detection, cleaning, quality detection
  duplicates.py        # size/hash/name dupe checks + fast fingerprint
  io_ops.py            # safe move/copy helpers
  sidecars.py          # subtitle discovery + move/copy
  nfo.py               # read existing NFO, merge-first, write movie/episode NFOs
  posters.py           # (optional) local poster sieve and carry logic
  web.py               # Flask upload UI + library dashboards (optional; used by Docker)
  audit.py             # shared Issue model for the dashboards
  library.py           # read-only audit of LIB_DIR/movies
  music.py             # read-only audit of the beets library via `beet ls`
  templates/           # HTML for web upload and dashboards
  static/              # dashboard.css / dashboard.js
  cleanup.py           # cleanup helpers
  stabilize.py         # stabilisation helpers
```

---

## Requirements

* **Python 3.10+**
* For **CLI-only** use: no mandatory third-party packages.
* (Optional) **Pillow** for poster sieve support.
* (Optional) **Flask** for the web upload interface (e.g. when using Docker or running `flask --app media_organiser.web:app run`).
* (Optional) **Mutagen** and **requests** are installed automatically when using Poetry, for music metadata handling.
* For best results with the Music Upload workflow, install system tools **ffmpeg** (with `libmp3lame`) to enable audio analysis/transcoding.
* (Optional) **beets** for the music library dashboard at `/library/music`. It is invoked as an external command, so any install that puts `beet` on `PATH` works (`pipx install beets`, a distro package, or the same virtualenv).

---

## Installation

```bash
git clone https://github.com/sauravyash/media_organiser.git
cd media_organiser
poetry install
poetry run media-organiser --help
```

---

## Usage

```bash
poetry run media-organiser SOURCE [DEST]
  [--mode move|copy]
  [--dry-run]
  [--dupe-mode off|name|size|hash]
  [--no-import-dedupe]
  [--emit-nfo off|movie|tv|all]
  [--nfo-layout same-stem|kodi]
  [--overwrite-nfo]
  [--carry-posters off|keep|skip|quarantine]
  [--poster-min-wh WxH]
  [--poster-aspect A-B]
  [--poster-keywords kw1,kw2,...]
```

Key flags:

* `--dupe-mode` supports `hash` (fast fingerprint), `size`, or `name`.
* Import-side library scan is enabled by default for video; use `--no-import-dedupe` to disable removing duplicate imports already present in `/movies` or `/tv`.
* `--emit-nfo` writes NFO files (merge-first).
* `--carry-posters` enables optional local poster filtering.

### Web upload (optional)

If Flask is installed, you can run a local upload UI that saves files into an import directory (e.g. for use with the Docker workflow):

```bash
export IMPORT_DIR=/path/to/import   # optional; default /data/import
flask --app media_organiser.web:app run --host 0.0.0.0 --port 6767
```

Then open `http://localhost:6767/`, upload files (or a folder to preserve structure); they are written to `IMPORT_DIR`. The CLI (or Docker watch) can then organise them into your library.

---

## Music upload (optional)

If `mutagen`, `requests`, and `ffmpeg` are available, the web UI also exposes a **Music Upload** workflow at `/music`:

* Upload audio files/folders into `IMPORT_DIR`.
* Inspect and edit detected metadata in a table (title, artist, album, track #, year, bitrate, duration).
* Play individual tracks with an inline audio player.
* Call out to MusicBrainz to fetch recommended metadata for a track.
* Enforce quality rules:
  * Reject files below 256 kbps.
  * Flag non-320 kbps or non-MP3 files as needing transcode.
* Transcode to 320 kbps MP3 using `ffmpeg`/LAME and export into a **separate music library** directory.

Environment variables:

```bash
export IMPORT_DIR=/path/to/import          # default: ./data/import
export MUSIC_LIB_DIR=/path/to/music_lib    # default: ./data/music
export MUSIC_IMPORT_DEDUPE=1               # default enabled; set 0/false/no/off to disable library duplicate scan
```

Music uploads (from the Music UI) and music transcode export both use `MUSIC_LIB_DIR`. During `/api/music/transcode`, the tool scans the music library for duplicate tracks (fingerprint + filename preference) and removes duplicate attempted imports by default; disable with `MUSIC_IMPORT_DEDUPE=0`. The existing video workflow continues to use the main library directory (`LIB_DIR`) for organise; video uploads go to `IMPORT_DIR`.

---

## Library dashboards (read-only)

Two pages list what is **already in the destination library** so you can eyeball mislabels
and metadata problems. Both are strictly read-only: they never rename, move or retag
anything. Every finding comes with a suggested change for you to verify and apply yourself.

### Movie library — `/library/movies`

Lists every folder under `LIB_DIR/movies` with its video, NFO, subtitles and posters, and
flags:

| Issue | Severity | What it means |
| --- | --- | --- |
| `tv-episode-in-movies` | high | A `SxxExx` file or season pack landed under `/movies` |
| `no-video-file` | high | Folder holds sidecars but no video (or is empty) |
| `multiple-videos` | high | Several unrelated videos share one movie folder |
| `duplicate-title` | high | Two folders resolve to the same movie |
| `messy-folder-name` | medium | Folder still carries scene words, release-site branding, brackets or a year |
| `leading-index` | medium | Folder starts with a collection index (`1. `, `02 - `) |
| `missing-year` / `suspect-year` | medium | No release year, or a year that is really part of the title (`Blade Runner 2049`) |
| `nfo-title-mismatch` | medium | The NFO `<title>` disagrees with the folder |
| `quality-mismatch` | medium | Filename quality disagrees with the NFO |
| `unreadable-nfo` | medium | The NFO could not be parsed |
| `missing-nfo` | low | No NFO next to the video |
| `unknown-quality` | low | Filed as `[Other]` because no resolution was detected |
| `filename-mismatch` | low | Filename does not follow `Title (Year) [Quality]` |
| `stale-nfo-path` | low | The NFO still points at the file's old name |

```bash
export LIB_DIR=/path/to/library      # default: ./data/library; movies are read from $LIB_DIR/movies
export MOVIES_DIR=/somewhere/movies  # optional; overrides $LIB_DIR/movies outright
```

### Music library — `/library/music`

Backed by **[beets](https://beets.io/)**: the page shells out to `beet ls` and never writes to
the beets database. Tracks and albums each get their own tab, and each recommendation is the
exact `beet` command that would fix it (e.g. `beet modify id:42 year=1994`), so you can copy
it, check it, and run it yourself.

Flags missing or placeholder artist/album/title/year/track/genre tags, tracks and albums with
no MusicBrainz id (imported as-is rather than matched), lossy files under 128 kbps, duplicate
tracks, albums with no tracks, and rows whose file is no longer on disk.

beets is optional — without it the page explains what to install and which variables to set:

```bash
export BEET_BIN=/usr/local/bin/beet          # default: beet (must be on PATH)
export BEETS_CONFIG=/path/to/config.yaml     # optional; passed as beet -c
export BEETS_LIBRARY=/path/to/musiclibrary.db  # optional; passed as beet -l
export BEETS_DIRECTORY=/path/to/music        # optional; passed as beet -d
```

> `BEETS_DIRECTORY` is beets' own music directory and is **not** the same as `MUSIC_LIB_DIR`,
> which is where the Music Upload workflow exports transcoded MP3s.

Both dashboards cache their scan for 60s (a large library is slow to walk); the **Rescan**
button bypasses the cache. Tune with `DASHBOARD_CACHE_TTL=<seconds>`, or `0` to disable caching.

---

## Naming logic

* Detects `SxxExx`, `SxxExx-Exx`, `2xNN`, `Sxx NN`
* Cleans scene noise (`BluRay`, `x265`, `[eztv]`...)
* Infers quality (`4k` → `2160p`, `8k` → `4320p`)
* Writes NFOs without online metadata lookups

---

## Example output

```
/movies/
  Title (2023)/
    Title (2160p).mkv
    Title (2160p).nfo
    Title (2160p).en.srt

/tv/
  Series Name/
    season-01/
      Series Name - S01E01 (1080p).mkv
      Series Name - S01E01 (1080p).nfo
```

---

## Roadmap

* `--carry` for extra sidecars (chapters, thumbs)
* Quality preference rules
* CSV/JSON action reporting
* PyPI packaging & release
---

## License

MIT — do what you like except steal credit; no warranty; I hold no responsibility for anything at all.
