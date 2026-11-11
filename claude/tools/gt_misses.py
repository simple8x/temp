"""Which library symbols fail template matching most?  usage: gt_misses.py gt_out [top_n]
Single-sheet single-page projects only. Output lines start with MISS."""
import collections
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pikepdf  # noqa: E402

from ecci.gt_kicad import embedded_library_text, gt_pins  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402
from ecci.kicad_lib import load_library  # noqa: E402
from ecci.rules import analyze_page  # noqa: E402
from ecci.symbols import strip_stubs  # noqa: E402

root = pathlib.Path(sys.argv[1])
top = int(sys.argv[2]) if len(sys.argv) > 2 else 30
stat = collections.defaultdict(lambda: collections.Counter())
shape = {}
for pdir in sorted(d for d in root.iterdir() if d.is_dir()):
    pdfs, xmls = sorted(pdir.glob("*.pdf")), sorted(pdir.glob("*.xml"))
    schs = sorted((pdir / "src").rglob("*.kicad_sch"))
    if not pdfs or not xmls or len(schs) != 1:
        continue
    with pikepdf.open(pdfs[0]) as pp:
        if len(pp.pages) != 1:
            continue
    try:
        pins, _ = gt_pins(str(schs[0]), str(xmls[0]))
        with tempfile.TemporaryDirectory() as td:
            lp = pathlib.Path(td) / "e.kicad_sym"
            lp.write_text(embedded_library_text(str(schs[0])))
            lib = load_library(str(lp))
        pi = ingest_pdf(str(pdfs[0]), pages=[0]).pages[0]
        cls, clusters, built, res = analyze_page(pi, lib.symbols.values())
    except Exception as e:  # noqa: BLE001
        print("MISS error", pdir.name, type(e).__name__, e)
        continue
    by_id = {p.id: p for p in pi.prims}
    byref = collections.defaultdict(list)
    for p in pins:
        if p.net and not p.hidden:
            byref[(p.ref, p.lib_id)].append(p)
    for (ref, lid), ps in byref.items():
        cx, cy = sum(p.x for p in ps) / len(ps), sum(p.y for p in ps) / len(ps)
        cand = [c for c in clusters if c.cluster.bbox[0] - 3 <= cx <= c.cluster.bbox[2] + 3
                and c.cluster.bbox[1] - 3 <= cy <= c.cluster.bbox[3] + 3]
        ok = any(c.resolution.status == "UNIQUE" for c in cand)
        s = stat[lid]
        s["instances"] += 1
        s["pins"] += len(ps)
        if ok:
            s["ok"] += 1
            continue
        s["fail"] += 1
        s["pins_missed"] += len(ps)
        if not cand:
            s["no_cluster"] += 1
        elif lid not in shape:
            c = min(cand, key=lambda c: (c.cluster.bbox[2] - c.cluster.bbox[0]) * (c.cluster.bbox[3] - c.cluster.bbox[1]))
            sd = lib.symbols.get(lid.split(":")[-1]) or lib.symbols.get(lid)
            sp = strip_stubs(c.cluster, by_id)
            shape[lid] = (pdir.name, ref, c.resolution.status, len(c.cluster.prim_ids), len(c.cluster.segs),
                          None if sp is None else (len(sp[1]), len(sp[0].segs)),
                          None if sd is None else (len(sd.pins), len(sd.segments())),
                          [round(v, 1) for v in c.cluster.bbox])
rows = sorted(stat.items(), key=lambda kv: -kv[1]["pins_missed"])
tot_missed = sum(s["pins_missed"] for _, s in rows)
tot_pins = sum(s["pins"] for _, s in rows)
print("MISS total pins", tot_pins, "missed", tot_missed)
for lid, s in rows[:top]:
    print("MISS", lid, dict(s), "example", shape.get(lid))
