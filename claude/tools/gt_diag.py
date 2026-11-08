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
