"""Diagnostics: schematic items (wire/bus/junction/label/bus_entry/no_connect) within R pt of a point.
usage: gt_sch_near.py gt_out project x y [R]   (x, y in PDF points, y down)"""
import math
import pathlib
import re
import sys

K = 72 / 25.4
root, proj, x, y = pathlib.Path(sys.argv[1]), sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
R = float(sys.argv[5]) if len(sys.argv) > 5 else 20.0
sch = sorted((root / proj).glob("*.kicad_sch"))[0].read_text(errors="replace")
pat = re.compile(r"\((wire|bus|bus_entry|junction|label|global_label|hierarchical_label|no_connect|polyline)\b")
print("SCHNEAR", proj, x, y, "R", R)
for m in pat.finditer(sch):
    seg = sch[m.start():m.start() + 400]
    pts = [(float(a) * K, float(b) * K) for a, b in re.findall(r"\((?:xy|at) (-?[\d.]+) (-?[\d.]+)", seg)[:2]]
    if "(bus_entry" == seg[:10]:
        pts = pts[:1]
        sz = re.search(r"\(size (-?[\d.]+) (-?[\d.]+)", seg)
        if sz:
            pts.append((pts[0][0] + float(sz[1]) * K, pts[0][1] + float(sz[2]) * K))
    if any(math.hypot(px - x, py - y) <= R for px, py in pts):
        txt = re.search(r'"([^"]*)"', seg)
        print("SCHNEAR", m.group(1), [(round(a, 1), round(b, 1)) for a, b in pts], txt[1] if txt and m.group(1) != "wire" else "")
