"""Diagnostics: cluster/template outcome per project.  usage: gt_diag.py gt_out project..."""
import collections
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ecci.gt_kicad import embedded_library_text  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402
from ecci.kicad_lib import load_library  # noqa: E402
from ecci.rules import analyze_page  # noqa: E402

root = pathlib.Path(sys.argv[1])
for proj in sys.argv[2:]:
    d = root / proj
    sch = sorted((d / "src").rglob("*.kicad_sch"))[0]
    with tempfile.TemporaryDirectory() as td:
        lp = pathlib.Path(td) / "e.kicad_sym"
        lp.write_text(embedded_library_text(str(sch)))
        lib = load_library(str(lp))
    pi = ingest_pdf(str(sorted(d.glob("*.pdf"))[0]), pages=[0]).pages[0]
    cls, clusters, built, res = analyze_page(pi, lib.symbols.values())
    st = collections.Counter(cr.resolution.status for cr in clusters)
    cc = collections.Counter(r.cls for r in cls.records.values())
    print("DIAG", proj, "wire_style", cls.wire_style, "classes", dict(cc), "clusters", len(clusters), dict(st),
          "libsyms", len(lib.symbols), "notes", [n for n in cls.notes if "wire style" in n])
    big = sorted(clusters, key=lambda c: -len(c.cluster.prim_ids))[:4]
    for cr in big:
        b = cr.cluster.bbox
        print("DIAG  big cluster", cr.cluster.id, "prims", len(cr.cluster.prim_ids), "bbox",
              [round(v) for v in b], cr.resolution.status, "pins", len(cr.pins))
    small = collections.Counter(len(cr.cluster.prim_ids) for cr in clusters)
    print("DIAG  size hist", sorted(small.items())[:12])

    # ground-truth view: which cluster holds each placed symbol, and why it did not resolve
    from ecci.gt_kicad import gt_pins  # noqa: E402
    xml = sorted(d.glob("*.xml"))[0]
    pins, _ = gt_pins(str(sch), str(xml))
    by_ref = collections.defaultdict(list)
    for gp in pins:
        by_ref[(gp.ref, gp.lib_id)].append(gp)
    tally = collections.Counter()
    shown = 0
    for (ref, lib_id), gps in sorted(by_ref.items()):
        cx, cy = sum(g.x for g in gps) / len(gps), sum(g.y for g in gps) / len(gps)
        cand = [cr for cr in clusters if cr.cluster.bbox[0] - 1 <= cx <= cr.cluster.bbox[2] + 1
                and cr.cluster.bbox[1] - 1 <= cy <= cr.cluster.bbox[3] + 1]
        if not cand:
            tally["no_cluster"] += 1
            continue
        cr = min(cand, key=lambda c: (c.cluster.bbox[2] - c.cluster.bbox[0]) * (c.cluster.bbox[3] - c.cluster.bbox[1]))
        tally[cr.resolution.status] += 1
        if cr.resolution.status != "UNIQUE" and shown < 8:
            shown += 1
            tb = (min(g.x for g in gps), min(g.y for g in gps), max(g.x for g in gps), max(g.y for g in gps))
            print("DIAG  GT", ref, lib_id, "pins", len(gps), "tips bbox", [round(v, 1) for v in tb],
                  "cluster prims", len(cr.cluster.prim_ids), "bbox", [round(v, 1) for v in cr.cluster.bbox],
                  cr.resolution.status, "hits", len(cr.resolution.hits))
    print("DIAG  GT tally", dict(tally))
