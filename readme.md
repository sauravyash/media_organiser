# Media Organiser (offline)

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
```

---

## Requirements

* **Python 3.9+**
* No mandatory third-party packages.
* (Optional) **Pillow** for better poster checks (`pip install Pillow`) if you enable the poster sieve.

---

## Installation

Clone or copy the `media_organiser` folder somewhere on your PYTHONPATH, or just run in place:

```bash
# from the directory that contains media_organiser/
python -m media_organiser --help
```

If you prefer a script entry point, add your own tiny wrapper or package it later—this repo is ready for that.

---

## Usage

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

### Arguments

* `SOURCE` – folder to scan recursively.
* `DEST` – destination root (defaults to in-place if omitted).
* `--mode` – `move` (default) or `copy`.
* `--dry-run` – print actions, make no changes.
* `--dupe-mode` (dest-side duplicate detection in the target **movie folder / season folder**):

  * `off`  – never consider as duplicate (will fall back to ` (2)` suffixes)
  * `name` – same **normalised** stem (ignoring quality) → duplicate
  * `size` – same byte size → duplicate
  * `hash` – **size + MD5 of head/tail 1 MiB** (default; fast & robust)

### NFO controls

* `--emit-nfo` – `movie`, `tv`, `all` (default), or `off`.
* `--nfo-layout` – `same-stem` (write `<video>.nfo`) or `kodi` (for movies, write `movie.nfo` inside the movie folder).
* `--overwrite-nfo` – overwrite an existing NFO (otherwise we skip rewriting it).

### Poster sieve (optional; default off)

* `--carry-posters` – `off` (default), `keep`, `skip`, `quarantine`

  * `keep`: copy posters only if they **pass** checks
  * `quarantine`: suspected posters get moved/copied into `<movie>/_quarantine/`
* `--poster-min-wh` – minimum poster dimensions (default `600x900`).
* `--poster-aspect` – accepted width/height ratio range (default `0.66-0.75`).
* `--poster-keywords` – blacklist tokens, e.g. `yify,yts,rarbg,ettv`.

> Poster handling uses only local files (`poster.jpg`, `folder.jpg`, `cover.jpg`) near the source. No downloads.

---

## Examples

**Dry run, organise in place:**

```bash
python -m media_organiser ~/Downloads --dry-run
```

**Copy to a library, prefer hash-based duplicate skipping:**

```bash
python -m media_organiser ~/torrents /srv/media --mode copy --dupe-mode hash
```

**Move into NAS and write NFOs for both movies & TV (same-stem):**

```bash
python -m media_organiser /mnt/incoming /mnt/media --emit-nfo all
```

**Write only movie NFOs in Kodi layout (movie.nfo in each film folder):**

```bash
python -m media_organiser /mnt/incoming /mnt/media --emit-nfo movie --nfo-layout kodi
```

**Enable poster sieve and quarantine suspects:**

```bash
python -m media_organiser /mnt/incoming /mnt/media \
  --carry-posters quarantine \
  --poster-min-wh 600x900 \
  --poster-aspect 0.66-0.75 \
  --poster-keywords "yify,yts,rarbg,ettv"
```

---

## What it does (in detail)

### 1) TV detection & naming

Understands:

* `S02E01`, `S02E01E02`, `S02E01-02`
* `2x01`, `2x01-02`
* `S02 01` and `S2E1`

Outputs:

```
/tv/<Series>/season-02/<Series> - S02E01 (1080p).mkv
/tv/<Series>/season-04/<Series> - S04E01-E02 (2160p).mkv
```

### 2) Movie naming

Cleans scene noise (`BluRay`, `x265`, `[eztv]`, `1080p`, etc.), removes leading indices (`01 `), and prefers:

1. Nearby **.nfo** `<title>` (for naming only; we don’t write back to media).
2. Parent/grandparent folder of the form `Title (Year)` / `Title [Year]`.
3. Filename up to the first standalone year token.

### 3) Quality detection

* `4k/uhd → 2160p`, `8k → 4320p`, otherwise uses the found token (`1080p`, `720p`, …)
* If none found → `Other`.

### 4) Subtitles (sidecars)

Finds sidecars that start with the video’s stem plus a suffix (e.g., `.en`, `.eng.forced`, `-hi`, `.pt-BR`) and carries them over:

```
My.Movie.2019.en.forced.srt → /movies/My Movie/My Movie (1080p).en.forced.srt
```

VobSub pairs (`.idx` + `.sub`) are handled if they share the same base stem.

### 5) Duplicate handling

Within each movie folder / season folder:

* `hash` mode (default) — skip if size + quick MD5 fingerprint matches an existing file.
* If not a duplicate but the **filename** already exists, we suffix ` (2)`, ` (3)`, etc.

### 6) NFO writing (merge-first)

When `--emit-nfo` is enabled:

* For **TV**: writes `<episodedetails>` next to the episode (or same-stem).
* For **Movies**: writes `<movie>` either as `<video>.nfo` (same-stem) or `movie.nfo` (Kodi).

**Merge-first semantics:**

1. Read **existing** NFOs (source side and/or destination side).
2. Use their fields **as defaults**.
3. Fill only the **missing** pieces with what we can infer:

   * `title`/`showtitle`, `season`, `episode`/`episode_to`, `quality`, `extension`
   * `size`, `filenameandpath`, `originalfilename`, `sourcepath`
   * `uniqueid` with `type="localhash"` (fast fingerprint: size + MD5 of head/tail)
   * merged `subtitles` list (existing entries kept, new entries appended)

No external IDs are fetched; if your existing NFO has them, they remain intact.

### 7) Poster sieve (optional)

If enabled, looks for `poster.jpg`, `folder.jpg`, `cover.jpg` near the source (or its parent).
Rejects/quarantines *suspects* using:

* filename/metadata keywords (`yify`, `yts`, `rarbg`, …),
* too-small dimensions,
* odd aspect ratios (outside your allowed range),
* near-solid borders on all sides (common with spam composites).

---

## Directory layout after running

```
/movies/
  <Title>/
    <Title> (<quality>).<ext>
    <Title> (<quality>).<lang[.flag]>.srt
    <Title> (<quality>).nfo   # or movie.nfo if using kodi layout
    [optional] poster.jpg / _quarantine/

/tv/
  <Series>/
    season-<NN>/
      <Series> - S<NN>E<NN>(-E<NN>) (<quality>).<ext>
      <Series> - S<NN>E<NN>...(<quality>).<lang>.srt
      <Series> - S<NN>E<NN>...(<quality>).nfo
```

---

## Tips & Troubleshooting

* **Nothing happens** → try `--dry-run` to see what the tool detects; confirm file extensions are supported.
* **Episode missed** → ensure filenames follow common patterns (`SxxExx`, `2xNN`, or `Sxx NN`).
* **Subtitles not copied** → the subtitle’s stem must begin with the source video’s stem.
* **Duplicates not skipped** → try `--dupe-mode size` or `name` if releases differ slightly but you still want to skip.
* **Posters flagged** → adjust `--poster-min-wh`, `--poster-aspect`, or `--poster-keywords`, or switch to `quarantine` to review later.

---

## FAQ

**Does it fetch internet metadata or write tags into the media files?**
No. It is strictly **offline** and writes only small **NFO** files.

**Can it also move `.nfo`/posters/chapters as data (not just sieve)?**
Posters are optional via the sieve; carrying over additional sidecars (e.g., chapters, thumbnails, NFO copies) can be added—open an issue or ask and we’ll wire a `--carry` flag for those.

**Will it overwrite my existing NFOs?**
Not unless you pass `--overwrite-nfo`. Otherwise, existing NFOs are **read and respected**, and only missing fields are filled.

---

## License

MIT—do what you like; no warranty.

---

## Roadmap / Ideas

* `--carry` for additional sidecars (chapters, thumbs, original .nfo copy).
* Quality preference rules (e.g., keep highest quality duplicate).
* CSV/JSON action report for auditing.
* Packaged release (`pyproject.toml`) and simple unit tests.
