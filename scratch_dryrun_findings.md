# Dry run findings: `/Volumes/Yash4TB/Movies`

**Log file:** `scratch_dryrun.log` (run may have been interrupted; log contains 45 MOVE lines and ~1065 total lines including NFO dumps.)

---

## Issues that look wrong

### 1. **`.AppleDouble` files treated as movies (high priority)**

macOS resource-fork / metadata files under `.AppleDouble/` are being scanned as video files and would all be moved into a **single fake movie folder** named `Appledouble` with conflicting filenames (e.g. `Appledouble (2017) [720p].mp4`, `Appledouble (1971) [1080p].mp4`, `Appledouble [Other].avi`). That would:

- Create one junk folder `movies/Appledouble/` with many unrelated “movies”
- Duplicate/corrupt data (AppleDouble are metadata, not the real video)
- Waste space and confuse the library

**Examples from log:**
- `.../Despicable Me 3 (2017) [YTS.AG]/.AppleDouble/...mp4` → `movies/Appledouble/Appledouble (2017) [720p].mp4`
- `.../Willy Wonka.../.AppleDouble/...mp4` → `movies/Appledouble/Appledouble (1971) [1080p].mp4`
- Same pattern for Antz, Shrek 2, Shrek 3, Shrek Original CD1/CD2, Disney Short Films, etc.

**Recommendation:** Skip or exclude paths containing `.AppleDouble` (and optionally other hidden/system dirs like `__MACOSX`) so they are never considered as source video files.

---

### 2. **Shrek Original: CD1 and CD2 both → same destination (data loss risk)**

Two distinct files (CD1 and CD2) are both planned to move to the **same** destination:

- `.../Shrek Original/CD 1/Shrek.DVDRip.XviD.CD1-BELiAL.avi` → `movies/Shrek Original/Shrek Original [Other].avi`
- `.../Shrek Original/CD 2/Shrek.DVDRip.XviD.CD2-BELiAL.avi` → `movies/Shrek Original/Shrek Original [Other].avi`

`safe_path()` would avoid overwrite by renaming the second to `Shrek Original (2) [Other].avi`, but the **folder name** “Shrek Original” doesn’t indicate “Part 1” vs “Part 2”, so you’d get two files in one movie folder with generic names. For a two-part DVD rip, that may be acceptable, but it’s worth noting: the organiser doesn’t treat “CD1”/“CD2” as a multi-part movie and keeps a single folder with two files.

---

### 3. **Disney Short Films: all shorts → one folder, “using nfo for name None”**

Multiple different shorts (e.g. “12. Frozen Fever (2015).mkv”, “11. Feast (2014).mkv”, “02. Lorenzo (2004).mkv”) are being placed in **one** folder:

- `movies/Disney Short Films/Disney Short Films (2015) [Other].mkv`
- `movies/Disney Short Films/Disney Short Films (2014) [Other].mkv`
- `movies/Disney Short Films/Disney Short Films (2004) [Other].mkv`
- etc.

The log repeatedly shows **“using nfo for name None”**: the NFO for the collection doesn’t provide a per-file title (or the parser returns `None`), so the tool falls back to the parent folder name “Disney Short Films” for every short. So every short gets the same “movie” name and only year/quality differ in the filename. That’s a naming/UX issue rather than data loss, but the collection is not split by short title.

---

### 4. **NFO and debug output flooding the log**

- **Full NFO body printed:** In `nfo.parse_local_nfo_for_title()` the entire NFO file is `print(raw)`-ed when parsing for title. That dumps long ASCII release info (e.g. “PROUDLY PRESENTS”, “PLOT”, “RELEASE iNFO”) into stdout and into your scratch log, making the log huge and hard to read.
- **Debug prints:** `naming.py` has `print("using nfo for name", t)` and `print("\n after year:", title_tokens)` which also end up in the log.

So the log is a mix of real MOVE lines and a lot of NFO/debug noise. For a clean dry-run log, consider removing or gating these prints (e.g. only when a `--verbose` flag is set).

---

### 5. **Binary/garbage in log (possible crash or bad NFO read)**

Near the end of `scratch_dryrun.log` there is a block of binary/garbage characters, followed by “using nfo for name None”. That suggests at least one “NFO” (or file found as NFO) was read as text but was actually binary or corrupted, which can cause parse errors or odd behaviour. Worth excluding binary files or handling read errors so one bad file doesn’t pollute the run.

---

## Summary

| Issue | Severity | Action |
|-------|----------|--------|
| `.AppleDouble` files processed as movies → fake `Appledouble` folder | **High** | Exclude `.AppleDouble` (and similar) from video scan |
| Shrek Original CD1/CD2 → same folder, generic names | Medium | Accept as-is or add multi-part (CD1/CD2) naming |
| Disney Short Films all named “Disney Short Films”, “using nfo for name None” | Low | Improve NFO title use or per-file naming for collections |
| NFO + debug prints in dry-run output | Low | Reduce or gate prints for clean logs |
| Binary/corrupt “NFO” read → garbage in log | Low | Skip or handle binary / invalid NFO reads |

---

*Generated from dry run of `/Volumes/Yash4TB/Movies` with output logged to `scratch_dryrun.log`.*
