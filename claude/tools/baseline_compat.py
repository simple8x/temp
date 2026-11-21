#!/usr/bin/env python3
"""Baseline compatibility fingerprint (P0 closeout F1).

Runs the BASELINE pipeline only (rules.analyze_page = classify -> detect_pins -> conn graph -> solve; recovery is
never called) on a fixed set of synthetic fixture pages and prints, per page, the sha256 of
  norm    a normalized, interpreter-independent JSON of every output object (classification incl. evidence
          registry, ClusterResults incl. Resolution, built graph, solve result): dataclass fields by name,
          floats rounded to 1e-9, sets sorted
  pickle  pickle protocol 4 bytes of (cls, clusters, built, res): catches any change of class layout / fields
          (only comparable between two package trees on the SAME interpreter)

usage: baseline_compat.py ECCI_ROOT [--out FILE]
ECCI_ROOT is the directory that contains the `ecci` package to fingerprint (e.g. a worktree of the parent commit).
Fixture drawing helpers always come from THIS tree's tests/ (identical in both commits), the code under test from
ECCI_ROOT. Exit 2 if ECCI_ROOT/ecci does not exist or the imported package is not the one asked for.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FORMAT = "ecci.baseline_compat/1"


def norm(o, depth=0):
    if depth > 60:
        raise RecursionError("object graph too deep to normalize")
    if o is None or isinstance(o, (bool, int, str)):
        return o
    if isinstance(o, float):
        if not math.isfinite(o):
            return repr(o)
        r = round(o, 9)
        return 0.0 if r == 0 else r
    if isinstance(o, enum.Enum):
        return f"{type(o).__name__}.{o.name}"
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {"__type__": type(o).__name__,
                **{f.name: norm(getattr(o, f.name), depth + 1) for f in dataclasses.fields(o)}}
    if isinstance(o, dict):
        items = [(json.dumps(norm(k, depth + 1), sort_keys=True), norm(v, depth + 1)) for k, v in o.items()]
        return {"__dict__": sorted(items, key=lambda kv: kv[0])}
    if isinstance(o, (set, frozenset)):
        return {"__set__": sorted((norm(x, depth + 1) for x in o), key=lambda x: json.dumps(x, sort_keys=True))}
    if isinstance(o, (list, tuple)):
        return [norm(x, depth + 1) for x in o]
    if hasattr(o, "__dict__"):
        return {"__type__": type(o).__name__, **{k: norm(v, depth + 1) for k, v in sorted(vars(o).items())}}
    if hasattr(o, "__slots__"):
        return {"__type__": type(o).__name__,
                **{k: norm(getattr(o, k), depth + 1) for k in o.__slots__ if hasattr(o, k)}}
    return repr(o)


def scenes():
    from dataclasses import replace

    from ecci.kicad_lib import BodyPath, PinDef, SymbolDef
    from m4_scenes import all_scenes
    from test_rules import LIB
    from render_kicad import draw_symbol

    def draw(sym, o="r0", t=(150.0, 100.0), **kw):
        return draw_symbol(sym, o, 3.0, t, body_w=0.9, pin_w=0.45, **kw)

    xf = SymbolDef("XF", (PinDef("1", "A", -3.54, 1.0, 0.0, 2.54, "passive"),
                          PinDef("2", "B", 7.04, -1.0, 180.0, 2.54, "passive")),
                   (BodyPath(((-1.0, -2.0), (1.0, -2.0), (1.0, 2.0), (-1.0, 2.0)), True),
                    BodyPath(((2.5, -2.0), (4.5, -2.0), (4.5, 1.0), (2.5, 2.0)), True)),
                   "lib:test_XF.kicad_sym@0")
    d, r, u4 = LIB.symbols["D"], LIB.symbols["R"], LIB.symbols["U4"]
    # same NAME, different lib_token: the B1 situation, run through the BASELINE
    r_twin = replace(r, lib_token=r.lib_token.split("@", 1)[0].replace("lib_min.kicad_sym", "x/lib_min.kicad_sym") + "@9")
    d_twin = replace(d, lib_token="lib:vendor.kicad_sym@0")
    base = list(LIB.symbols.values())
    out = {f"m4_{s.name}": (s.content, base) for s in all_scenes()}
    out.update({
        "split_body": (draw(xf), base + [xf]),
        "chained_diodes": (draw(d) + draw(d, t=(150.0, 108.12)), base + [xf]),
        "resolved_and_recovered": (draw(r, t=(60.0, 100.0)) + draw(xf), base + [xf]),
        "no_pin_lines": (draw(xf, pins=False) + draw(r, t=(60.0, 60.0)), base + [xf]),
        "baseline_only": (draw(r, t=(60.0, 100.0)) + draw(u4, t=(200.0, 100.0)), base + [xf]),
        "same_name_two_libs_R": (draw(r, t=(60.0, 100.0)) + draw(r, "r90", t=(200.0, 100.0)), base + [r_twin]),
        "same_name_two_libs_D": (draw(d) + draw(d, "r180", t=(60.0, 60.0)), base + [d_twin]),
        "rotations_R_D": (b"".join(draw(s, o, t=(40.0 + 50 * k, 60.0 + 70 * (s is d)))
                                   for s in (r, d) for k, o in enumerate(("r0", "r90", "r180", "r270"))), base),
    })
    return out


def fingerprint() -> dict:
    from ecci.ingest_vector import ingest_pdf
    from ecci.rules import analyze_page
    from pdfgen import one_page
    from test_rules import H, W

    pages = {}
    for name, (content, syms) in sorted(scenes().items()):
        wires = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"
        pi = ingest_pdf(one_page(wires + content, size=(W, H))).pages[0]
        out = analyze_page(pi, syms)
        n = json.dumps(norm(out), sort_keys=True, separators=(",", ":"))
        cls, clusters, _built, _res = out
        pages[name] = {
            "norm_sha256": hashlib.sha256(n.encode()).hexdigest(),
            "pickle_sha256": hashlib.sha256(pickle.dumps(out, protocol=4)).hexdigest(),
            "resolutions": [[cr.cluster.id, cr.resolution.status, cr.resolution.id_state, list(cr.resolution.symbols),
                             len(cr.pins)] for cr in clusters],
            "resolution_fields": [f.name for f in dataclasses.fields(type(clusters[0].resolution))] if clusters else [],
        }
    return {"format": FORMAT, "python": sys.version.split()[0], "pages": pages}


def main(argv) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    root = Path(argv[0]).resolve()
    if not (root / "ecci" / "__init__.py").is_file():
        print(f"ERROR: {root}/ecci is not a package")
        return 2
    sys.path[:0] = [str(root), str(HERE / "tests"), str(HERE / "tests" / "unit")]
    import ecci
    if Path(ecci.__file__).resolve().parent != root / "ecci":
        print(f"ERROR: imported {ecci.__file__}, expected {root / 'ecci'}")
        return 2
    doc = fingerprint() | {"ecci_root": str(root)}
    txt = json.dumps(doc, indent=1, sort_keys=True)
    if "--out" in argv:
        Path(argv[argv.index("--out") + 1]).write_text(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
