#!/usr/bin/env python3
"""
Generate self-hosted coverage badge and sparkline from coverage.xml.

Outputs:
  coverage/badge.svg
  coverage/graph.svg
  coverage/coverage_history.json
"""
import json, os, sys, datetime, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COV_XML = ROOT / "coverage.xml"
OUT_DIR = ROOT / "coverage"
HIST = OUT_DIR / "coverage_history.json"

OUT_DIR.mkdir(exist_ok=True)

if not COV_XML.exists():
    print("coverage.xml not found", file=sys.stderr)
    sys.exit(1)

# --- parse coverage.xml ---
tree = ET.parse(COV_XML)
root = tree.getroot()

# coverage.py writes <coverage lines-valid="X" lines-covered="Y" ...>
valid = int(root.attrib.get("lines-valid", 0))
covered = int(root.attrib.get("lines-covered", 0))

pct = 0.0 if valid == 0 else (covered / valid * 100.0)
pct_str = f"{pct:.1f}%"

# --- update history ---
now = datetime.datetime.utcnow().strftime("%Y-%m-%d")
history = []
if HIST.exists():
    try:
        history = json.loads(HIST.read_text())
    except Exception:
        history = []
history.append({"date": now, "pct": round(pct, 2)})
# keep last 180 points
history = history[-180:]
HIST.write_text(json.dumps(history, indent=2))

# --- helper: color scale (like shields) ---
def color_for(p):
    if p >= 90: return "#4c1"     # bright green
    if p >= 80: return "#97CA00"  # green
    if p >= 70: return "#a4a61d"  # yellow-green
    if p >= 60: return "#dfb317"  # yellow
    if p >= 50: return "#fe7d37"  # orange
    return "#e05d44"              # red

color = color_for(pct)

# --- badge.svg (simple two-segment badge) ---
label = "coverage"
label_w = 72
val_w = 64
w = label_w + val_w
h = 20
badge_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" role="img" aria-label="{label}: {pct_str}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <mask id="m"><rect width="{w}" height="{h}" rx="3" fill="#fff"/></mask>
  <g mask="url(#m)">
    <rect width="{label_w}" height="{h}" fill="#555"/>
    <rect x="{label_w}" width="{val_w}" height="{h}" fill="{color}"/>
    <rect width="{w}" height="{h}" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="{label_w/2}" y="14">{label}</text>
    <text x="{label_w + val_w/2}" y="14">{pct_str}</text>
  </g>
</svg>
"""
(OUT_DIR / "badge.svg").write_text(badge_svg)

print(f"wrote: {OUT_DIR/'badge.svg'}, {OUT_DIR/'graph.svg'}, {HIST}")
