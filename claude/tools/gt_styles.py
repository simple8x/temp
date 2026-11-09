"""Diagnostics: per-style statistics of Manhattan open lines for projects.  usage: gt_styles.py gt_out proj..."""
import collections
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ecci.classify import _connected_ends, classify_page, is_manhattan, style_key  # noqa: E402
from ecci.arrangement import EPS_DEFAULT  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402

root = pathlib.Path(sys.argv[1])
for proj in sys.argv[2:]:
    pdf = sorted((root / proj).glob("*.pdf"))[0]
    pi = ingest_pdf(str(pdf), pages=[0]).pages[0]
    cls = classify_page(pi)
    print("STYLES", proj, "chosen", cls.wire_style, "|", "; ".join(cls.notes)[:700])
    pop = [p for p in pi.prims if p.kind == "polyline" and p.stroke and not p.fill and not p.line_closed
           and is_manhattan(p, 0.5) and cls.records[p.id].cls not in ("TEXT",)]
    g = collections.defaultdict(list)
    for p in pop:
        g[style_key(p, 3)].append(p)
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))[:8]:
        L = sum(math.dist(p.pts[0], p.pts[-1]) for p in v)
        c = _connected_ends(v, EPS_DEFAULT)
        cl = collections.Counter(cls.records[p.id].cls for p in v)
        print("STYLES  ", k, "n", len(v), "len", round(L), "conn", c, "ratio", round(c / (2 * len(v)), 2), dict(cl))
