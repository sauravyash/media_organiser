# Media Organiser (offline)

[![Python 3.10](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.10)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml)
[![Python 3.11](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.11)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml)
[![Python 3.12](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml/badge.svg?branch=master&label=Python%203.12)](https://github.com/sauravyash/media_organiser/actions/workflows/ci.yml)

![Coverage](coverage/badge.svg)
![Coverage graph](coverage/graph.svg)


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
pytest --cov=media_organiser --cov-report=term --cov-report=xml
```

---

## 🐳 Docker / Compose

Media Organiser is Docker-ready. An example configuration:

```yaml
# docker-compose.yml
services:
  media-organiser:
    build: ./media_organiser
    container_name: media-organiser
    restart: unless-stopped
    environment:
      IMPORT_DIR: /data/import
      LIB_DIR: /data/library
    volumes:
      - /data/import:/data/import
      - /data/content:/data/library
```

Entrypoint (`entrypoint.sh`) automatically watches the import folder for new media and runs the organiser:

```bash
#!/usr/bin/env bash
set -euo pipefail

IMPORT_DIR="${IMPORT_DIR:-/data/import}"
LIB_DIR="${LIB_DIR:-/data/library}"

echo "[startup] organising once..."
python /app/organise_media.py "$IMPORT_DIR" "$LIB_DIR" --mode move

echo "[watch] monitoring $IMPORT_DIR for new or changed files..."
inotifywait -m -r -e close_write,create,move,delete "$IMPORT_DIR" | while read -r _; do
  sleep 20
  echo "[watch] change detected — organising..."
  python /app/main.py "$IMPORT_DIR" "$LIB_DIR" --mode move
done
```

This allows you to drop media into the import folder and let the container do the rest.

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
```

---

## 📦 Requirements

* **Python 3.10+**
* No mandatory third-party packages.
* (Optional) **Pillow** for poster sieve support.

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
