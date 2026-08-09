from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List

from .constants import VIDEO_EXTS

def xml_indent(elem: ET.Element, level: int = 0):
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            xml_indent(e, level+1)
        if not e.tail or not e.tail.strip():
            e.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i

def nfo_path_for(dst_video: Path, scope: str, layout: str) -> Path:
    if layout == "kodi" and scope == "movie":
        return dst_video.parent / "movie.nfo"
    return dst_video.with_suffix(".nfo")

def holds_single_video(folder: Path) -> bool:
    """
    Whether ``folder`` contains exactly one video file in its whole subtree.

    Used to decide if a folder-level NFO can be trusted. Stops counting at two,
    so the expensive case (a flat import folder holding hundreds of movies) is
    also the cheapest to reject.
    """
    seen = 0
    try:
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                seen += 1
                if seen > 1:
                    return False
    except OSError:
        return False
    return seen == 1


def _folder_nfo(folder: Path) -> Optional[Path]:
    """The folder's own NFO (Kodi ``movie.nfo`` layout), chosen deterministically."""
    try:
        nfos = sorted(p for p in folder.glob("*.nfo") if p.is_file())
    except OSError:
        return None
    if not nfos:
        return None
    for p in nfos:
        if p.name.lower() in ("movie.nfo", "tvshow.nfo"):
            return p
    return nfos[0]


def find_nfo(path: Path) -> Optional[Path]:
    """
    The NFO describing ``path``, or None.

    Only a same-stem NFO is a certain match. A folder-level NFO (the Kodi
    ``movie.nfo`` layout) is accepted solely when that folder holds exactly one
    video, so it cannot plausibly belong to anything else. Without that guard a
    single stray NFO in a flat import folder is handed to every video beside it,
    and — because an NFO title outranks every other signal in ``guess_movie_name``
    — silently renames the entire import to one title.
    """
    cand = path.with_suffix(".nfo")
    if cand.exists():
        return cand
    seen: set[Path] = set()
    for folder in (path.parent, path.parent.parent):
        if folder in seen:
            continue
        seen.add(folder)
        if not holds_single_video(folder):
            continue
        nfo = _folder_nfo(folder)
        if nfo is not None:
            return nfo
    return None


def _normalize_title_text(s: str) -> str:
    # replace dots/underscores/multiple whitespace with single spaces
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def parse_local_nfo_for_title(nfo_path: Path) -> Optional[str]:
    try:
        raw = nfo_path.read_text(errors="ignore").strip()
        if "\x00" in raw:
            return None
        try:
            root = ET.fromstring(raw)
            node = root if root.tag.lower() in ("movie","tvshow","episodedetails") else root.find("movie")
            if node is None:
                node = root
            el = node.find("title")
            if el is not None and (el.text or "").strip():
                from .naming import titlecase_soft  # ← do not use clean_name here
                text = _normalize_title_text(el.text)
                return titlecase_soft(text) if text else None
        except ET.ParseError:
            m = re.search(r"(?im)^\s*title\s*[:=]\s*(.+)$", raw)
            if m:
                from .naming import titlecase_soft  # ← same here
                text = _normalize_title_text(m.group(1))
                return titlecase_soft(text) if text else None
    except Exception:
        pass
    return None


def read_nfo_to_meta(nfo_path: Path) -> dict:
    meta: dict = {}
    try:
        raw = nfo_path.read_text(errors="ignore")
        root = ET.fromstring(raw)
        def txt(tag):
            el = root.find(tag)
            return (el.text or "").strip() if el is not None and el.text else None
        tag = root.tag.lower()
        if tag == "movie":
            meta["scope"] = "movie"
            for k in ("title","year","quality","extension","size","filenameandpath","originalfilename","sourcepath"):
                v = txt(k)
                if v: meta[k] = v
            for uid in root.findall("uniqueid"):
                t = uid.attrib.get("type","").lower()
                if t == "localhash" and (uid.text or "").strip():
                    meta["uniqueid_localhash"] = uid.text.strip()
                    break
        elif tag == "episodedetails":
            meta["scope"] = "tv"
            for k in ("showtitle","season","episode","episode_to","title","quality","extension","size","filenameandpath","originalfilename","sourcepath"):
                v = txt(k)
                if v: meta[k] = v
            for uid in root.findall("uniqueid"):
                t = uid.attrib.get("type","").lower()
                if t == "localhash" and (uid.text or "").strip():
                    meta["uniqueid_localhash"] = uid.text.strip()
                    break
        subs_node = root.find("subtitles")
        subs = []
        if subs_node is not None:
            for s in subs_node.findall("subtitle"):
                subs.append({"file": s.attrib.get("file",""), "lang": s.attrib.get("lang","")})
        if subs: meta["subtitles"] = subs
    except Exception:
        m = re.search(r"(?im)^\s*title\s*[:=]\s*(.+)$", locals().get("raw",""))
        if m:
            from .naming import clean_name, titlecase_soft
            meta["title"] = titlecase_soft(clean_name(m.group(1).strip()))
        y = re.search(r"(?:(?:19|20)\d{2})", locals().get("raw",""))
        if y: meta["year"] = y.group(0)
    return meta

def merge_first(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if k not in out or out[k] in (None, "", []):
            out[k] = v
    return out

def merge_subtitles(existing: List[dict] | None, new_ones: List[dict] | None) -> List[dict]:
    existing = existing or []
    new_ones = new_ones or []
    seen = {(s.get("file",""), s.get("lang","")) for s in existing}
    for s in new_ones:
        key = (s.get("file",""), s.get("lang",""))
        if key not in seen:
            existing.append({"file": s.get("file",""), "lang": s.get("lang","")})
            seen.add(key)
    return existing

def write_movie_nfo(dst_video: Path, computed: dict, base_meta: dict | None, overwrite: bool, layout: str):
    out = nfo_path_for(dst_video, "movie", layout)
    if out.exists() and not overwrite:
        print(f"NFO SKIP (exists): {out}")
        return
    base_meta = base_meta or {}
    merged = merge_first(base_meta, computed)
    subs = merge_subtitles(base_meta.get("subtitles"), computed.get("subtitles"))
    if subs:
        merged["subtitles"] = subs
    root = ET.Element("movie")
    def set_el(tag, val):
        if val not in (None,"",[]):
            ET.SubElement(root, tag).text = str(val)
    set_el("title", merged.get("title"))
    set_el("year",  merged.get("year"))
    set_el("quality", merged.get("quality"))
    set_el("extension", merged.get("extension"))
    set_el("size", str(merged.get("size")) if merged.get("size") else None)
    uid = merged.get("uniqueid_localhash")
    if uid:
        node = ET.SubElement(root, "uniqueid", {"type":"localhash","default":"true"})
        node.text = uid
    set_el("filenameandpath", merged.get("filenameandpath"))
    set_el("originalfilename", merged.get("originalfilename"))
    set_el("sourcepath", merged.get("sourcepath"))
    subs = merged.get("subtitles") or []
    if subs:
        subs_el = ET.SubElement(root, "subtitles")
        for s in subs:
            ET.SubElement(subs_el, "subtitle", {"file": s.get("file", ""), "lang": s.get("lang", "")})

    xml_indent(root)
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    out.write_bytes(xml_bytes)

    print(f"NFO WRITE: {out}")

def write_episode_nfo(dst_video: Path, computed: dict, base_meta: dict | None, overwrite: bool, layout: str):
    out = nfo_path_for(dst_video, "tv", layout)
    if out.exists() and not overwrite:
        print(f"NFO SKIP (exists): {out}")
        return
    base_meta = base_meta or {}
    merged = merge_first(base_meta, computed)

    subs = merge_subtitles(base_meta.get("subtitles"), computed.get("subtitles"))
    if subs:
        merged["subtitles"] = subs


    root = ET.Element("episodedetails")
    def set_el(tag, val):
        if val not in (None,"",[]):
            ET.SubElement(root, tag).text = str(val)
    set_el("showtitle", merged.get("showtitle"))
    set_el("season",    merged.get("season"))
    set_el("episode",   merged.get("episode"))
    set_el("episode_to",merged.get("episode_to"))
    set_el("title",     merged.get("title"))
    set_el("quality",   merged.get("quality"))
    set_el("extension", merged.get("extension"))
    set_el("size",      str(merged.get("size")) if merged.get("size") else None)
    uid = merged.get("uniqueid_localhash")
    if uid:
        node = ET.SubElement(root, "uniqueid", {"type":"localhash","default":"true"})
        node.text = uid
    set_el("filenameandpath", merged.get("filenameandpath"))
    set_el("originalfilename", merged.get("originalfilename"))
    set_el("sourcepath", merged.get("sourcepath"))

    subs = merged.get("subtitles") or []
    if subs:
        subs_el = ET.SubElement(root, "subtitles")
        for s in subs:
            ET.SubElement(subs_el, "subtitle", {"file": s.get("file", ""), "lang": s.get("lang", "")})
            

    xml_indent(root)
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    out.write_bytes(xml_bytes)

    print(f"NFO WRITE: {out}")
