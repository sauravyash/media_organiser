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

## 🧪 CI Status

| Python version |                                                                                                Status                                                                                                 |
| -------------: |:-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------:|
|           3.10 | [![3.10](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.10)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml) |
|           3.11 | [![3.11](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.11)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml) |
|           3.12 | [![3.12](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.12)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml) |

This project is tested automatically on each push and pull request using [pytest](https://docs.pytest.org/) across Python 3.10–3.12. Coverage is collected and stored as build artifacts.

---

## 🧪 Testing

This repository uses **pytest** for integration and unit testing. Major test coverage includes:

* **CLI integration** – end-to-end execution of organise/move and NFO writing
* **Duplicate detection** – hash-based and size-based dupe handling
* **Naming and quality detection** – including `SxxExx`, `2xNN`, and `UHD/8K` normalization
* **NFO writing and merge-first logic**
* **Poster sieve** (optional; only runs if `Pillow` is installed)
* **Sidecar subtitles** handling

### Running tests locally

```bash
python -m pip install -U pip
pip install pytest
# Optional (enables poster sieve test)
pip install Pillow

pytest -q
```

Generate coverage:

```bash
pytest --cov=1771768315media_organiser --cov-report=term --cov-report=xml
```

---

## 🐳 Docker / Compose

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
    ports:
      - "6767:6767"
    volumes:
      - /data/import:/data/import
      - /data/content:/data/library
```

The entrypoint (`entrypoint.sh`) does three things:

1. **One-off organise** on startup (import → library).
2. **Background watch** of the import folder with `inotifywait`; when files change, it runs the organiser again (with `--dupe-mode name --emit-nfo all --carry-posters keep`).
3. **Web upload UI** on port **6767** (Flask). You can upload any files into the import folder via the browser. Use “choose folder” to upload a directory and preserve its structure (NFO, subtitles, images).

So you can either drop files into the mounted import directory on the host, or use the web interface at `http://<host>:6767/` to upload; the container will organise them into the library.

---

## 📁 Project layout

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
  web.py               # Flask upload UI (optional; used by Docker)
  templates/           # HTML for web upload (e.g. upload.html)
  cleanup.py           # cleanup helpers
  stabilize.py         # stabilisation helpers
```

---

## 📦 Requirements

* **Python 3.10+**
* For **CLI-only** use: no mandatory third-party packages.
* (Optional) **Pillow** for poster sieve support.
* (Optional) **Flask** for the web upload interface (e.g. when using Docker or running `flask --app media_organiser.web:app run`).

---

## 🚀 Installation

```bash
git clone https://github.com/sauravyash/media_organiser.git
cd media_organiser
python -m media_organiser --help
```

---

## 🧰 Usage

```bash
python -m media_organiser SOURCE [DEST]
  [--mode move|copy]
  [--dry-run]
  [--dupe-mode off|name|size|hash]
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

## 🧠 Naming logic

* Detects `SxxExx`, `SxxExx-Exx`, `2xNN`, `Sxx NN`
* Cleans scene noise (`BluRay`, `x265`, `[eztv]`...)
* Infers quality (`4k` → `2160p`, `8k` → `4320p`)
* Writes NFOs without online metadata lookups

---

## 🧼 Example output

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

## 🧭 Roadmap

* `--carry` for extra sidecars (chapters, thumbs)
* Quality preference rules
* CSV/JSON action reporting
* PyPI packaging & release
---

## 📝 License

MIT — do what you like except steal credit; no warranty; I hold no responsibility for anything at all.
