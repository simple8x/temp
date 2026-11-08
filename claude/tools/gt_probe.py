"""Diagnostics: styles on a page and primitives near a point.  usage: gt_probe.py gt_out project x y radius"""
import collections
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ecci.classify import classify_page, style_key  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402

root, proj = pathlib.Path(sys.argv[1]), sys.argv[2]
pts = [(float(sys.argv[i]), float(sys.argv[i + 1])) for i in range(3, len(sys.argv) - 1, 2)]
rad = 6.0
pdf = sorted((root / proj).glob("*.pdf"))[0]
pi = ingest_pdf(str(pdf), pages=[0]).pages[0]
cls = classify_page(pi)
print("PROBE", proj, "wire_style", cls.wire_style)
c = collections.Counter((style_key(p), cls.records[p.id].cls) for p in pi.prims if p.stroke)
for (k, cl), n in c.most_common(12):
    print("PROBE style", k, cl, n)
for (x, y) in pts:
    print("PROBE near", x, y)
    for p in pi.prims:
        if any(math.hypot(q[0] - x, q[1] - y) <= rad for q in p.pts):
            print("PROBE  ", p.id, cls.records[p.id].cls, "stroke" if p.stroke else "", "fill" if p.fill else "",
                  style_key(p), [(round(a, 1), round(b, 1)) for a, b in p.pts][:6])
