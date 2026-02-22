from pathlib import Path
import re

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".ts", ".webm"}
SUB_EXTS   = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".ttml", ".dfxp", ".smi"}
SIDECAR_EXTS = SUB_EXTS | {".nfo"}  # subs + NFO (all moved/copied with video)
POSTER_NAMES = ("poster.jpg", "folder.jpg", "cover.jpg")

SEASON_PATTERNS = [
    re.compile(r"(?i)(?P<season>\d{1,2})x(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]*(?P<ep2>\d{1,3}))?"),
    re.compile(r"(?i)S(?P<season>\d{1,2})[\.\s_\-]*E(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]*E?(?P<ep2>\d{1,3}))?"),
    re.compile(r"(?i)S(?P<season>\d{1,2})[\.\s_\-]+(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]+(?P<ep2>\d{1,3}))?"),
    re.compile(r"(?i)(?P<season>\d{1,2})[\.\s_\-]*E(?P<ep1>\d{1,3})"),
]



YEAR_PATTERN       = re.compile(r"(?:(?:19|20)\d{2})")

RESOLUTION_PATTERN = re.compile(r"(?i)\b(480p|576p|720p|1080p|2160p|4320p|4k|8k|uhd|hdr|hd|fhd)\b")

RESOLUTION_WITH_BRACKETS_PATTERN = re.compile(
    r"(?i)[\[\(\{]?\s*(480p|576p|720p|1080p|2160p|4320p|4k|8k|uhd|hdr|hd|fhd)\s*[\]\)\}]?"
)

# Year may be single (YYYY) or range (YYYY-YYYY) e.g. Lord of the Rings Trilogy (2001-2003)
MOVIE_DIR_RE       = re.compile(r"(?i)^(?P<title>.+?)\s*[\(\[\{]?(?P<year>(?:19|20)\d{2}(?:\s*-\s*(?:19|20)\d{2})?)[\)\]\}]?$")
GENERIC_DIRS       = {"subs", "subtitles", "other", "cd 1", "cd 2", "sample"}
IGNORED_PATH_COMPONENTS = {".AppleDouble", "__MACOSX"}
MOVIE_PART_RE      = re.compile(r"(?i)(?:cd\s*(\d+)|part\s*(\d+)|pt\s*(\d+))")
SCENE_WORDS        = re.compile(r"(?i)\b(BluRay|WEB[- ]?DL|WEBRip|WEBDL|BRRip|HDRip|DVDRip|x264|x265|h\.?264|h\.?265|HEVC|AV1|AAC|DTS|DDP?5\.1|10bit|8bit|Atmos|Remux|Proper|Repack|Extended|IMAX|HDTV|XviD|ION10|LOL|KILLERS|SVA|mSD|STRiFE|GalaxyTV)\b")

SIDE_SUFFIX_RE = re.compile(r"(?i)^({base})(?P<suffix>(?:[ ._\-](?!S\d{1,2}E)\w[\w.\-]*)?)$")
ROOT = Path("..")

