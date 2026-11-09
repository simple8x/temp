"""Diagnostics: why do GT symbols fail template matching?  usage: gt_clusters.py gt_out project [max_report]"""
import collections
import math
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ecci.gt_kicad import embedded_library_text, gt_pins  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402
from ecci.kicad_lib import load_library  # noqa: E402
from ecci.rules import analyze_page  # noqa: E402
from ecci.symbols import strip_stubs  # noqa: E402

root, proj = pathlib.Path(sys.argv[1]), sys.argv[2]
maxn = int(sys.argv[3]) if len(sys.argv) > 3 else 8
pdir = root / proj
sch = sorted((pdir / "src").rglob("*.kicad_sch"))[0]
pins, info = gt_pins(str(sch), str(sorted(pdir.glob("*.xml"))[0]))
with tempfile.TemporaryDirectory() as td:
    lp = pathlib.Path(td) / "e.kicad_sym"
    lp.write_text(embedded_library_text(str(sch)))
    lib = load_library(str(lp))
pi = ingest_pdf(str(sorted(pdir.glob("*.pdf"))[0]), pages=[0]).pages[0]
cls, clusters, built, res = analyze_page(pi, lib.symbols.values())
by_id = {p.id: p for p in pi.prims}
byref = collections.defaultdict(list)
for p in pins:
    byref[(p.ref, p.lib_id)].append(p)
print("CL", proj, "library", len(lib.symbols), "warnings", len(lib.warnings), lib.warnings[:3])
fails = ok = 0
for (ref, lid), ps in sorted(byref.items()):
    cx = sum(p.x for p in ps) / len(ps)
    cy = sum(p.y for p in ps) / len(ps)
    cand = [c for c in clusters if c.cluster.bbox[0] - 3 <= cx <= c.cluster.bbox[2] + 3
            and c.cluster.bbox[1] - 3 <= cy <= c.cluster.bbox[3] + 3]
    st = [c.resolution.status for c in cand]
    if "UNIQUE" in st:
        ok += 1
        continue
    fails += 1
    if fails <= maxn:
        sd = lib.symbols.get(lid.split(":")[-1]) or lib.symbols.get(lid)
        print("CL FAIL", ref, lid, "npins", len(ps), "lib_known", sd is not None,
              "lib_pins", len(sd.pins) if sd else None, "lib_segs", len(sd.segments()) if sd else None)
        for c in cand[:2]:
            sp = strip_stubs(c.cluster, by_id)
            print("CL    cluster", c.cluster.id, c.resolution.status, "prims", len(c.cluster.prim_ids),
                  "segs", len(c.cluster.segs), "bbox", [round(v, 1) for v in c.cluster.bbox],
                  "stripped", None if sp is None else (len(sp[1]), len(sp[0].segs)))
        if not cand:
            print("CL    no cluster near", round(cx, 1), round(cy, 1))
from ecci.classify import style_key  # noqa: E402
for c in clusters:
    if len(c.cluster.prim_ids) > 150:
        h = collections.Counter((style_key(by_id[i]), by_id[i].kind, len(by_id[i].pts) if len(by_id[i].pts) < 4 else 4)
                                for i in c.cluster.prim_ids)
        print("CL BIG cluster", c.cluster.id, len(c.cluster.prim_ids), "wire_style", cls.wire_style)
        for k, n in h.most_common(8):
            print("CL    ", n, k)
from ecci.symbols import body_clusters  # noqa: E402
from ecci.rules import off_colour_body  # noqa: E402
for gf in (0.0, 0.1, 0.25, 0.5):
    cc = body_clusters(pi, cls, gap_frac=gf, exclude=off_colour_body(pi, cls))
    sizes = sorted((len(c.prim_ids) for c in cc), reverse=True)
    print("CL GAP", gf, "clusters", len(cc), "largest", sizes[:5])
print("CL SUMMARY", proj, "instances ok", ok, "fail", fails)
