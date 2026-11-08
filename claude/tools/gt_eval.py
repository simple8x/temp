"""Evaluate ECCI against KiCad-computed ground truth (M4.0). Prints one JSON line per project.

Two measurements, so that failures are attributed to the right stage:
  oracle   GT pin POSITIONS (from the .kicad_sch) are attached to wires by geometry; measures wire
           connectivity and site handling independently of symbol/pin detection.
  detected our own detected pins are matched to GT pin positions; measures the whole pipeline.
A GT pair is positive iff KiCad's netlist puts both pins in one net. Only single-sheet, single-page
projects are evaluated; hierarchical designs are reported as skipped.
"""
import collections
import json
import math
import pathlib
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pikepdf  # noqa: E402

from ecci.gt_kicad import embedded_library_text, gt_pins  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402
from ecci.kicad_lib import load_library  # noqa: E402
from ecci.rules import analyze_page  # noqa: E402

TOL_WIRE = 0.6      # pt: wire end within this distance of a GT pin position
TOL_PIN = 1.0       # pt: detected pin tip within this distance of a GT pin position
LEVELS = ("VERIFIED", "INFERRED")


def pair_metrics(items, status_fn):
    """items: list of (key, net). Returns counters over all unordered pairs."""
    c = collections.Counter()
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            same = items[i][1] == items[j][1]
            st = status_fn(items[i][0], items[j][0])
            c["pos" if same else "neg"] += 1
            if st in LEVELS:
                c[("TP_" if same else "FP_") + st] += 1
    out = {"pairs": c["pos"] + c["neg"], "pos": c["pos"], "neg": c["neg"]}
    for lv in LEVELS:
        tp, fp = c["TP_" + lv], c["FP_" + lv]
        out[lv] = {"tp": tp, "fp": fp}
    tp, fp = c["TP_VERIFIED"] + c["TP_INFERRED"], c["FP_VERIFIED"] + c["FP_INFERRED"]
    out["precision_V"] = round(c["TP_VERIFIED"] / max(1, c["TP_VERIFIED"] + c["FP_VERIFIED"]), 4)
    out["recall_V"] = round(c["TP_VERIFIED"] / max(1, c["pos"]), 4)
    out["precision_VI"] = round(tp / max(1, tp + fp), 4)
    out["recall_VI"] = round(tp / max(1, c["pos"]), 4)
    return out


def evaluate(pdir: pathlib.Path) -> dict:
    t0 = time.time()
    xml = sorted(pdir.glob("*.xml"))
    pdf = sorted(pdir.glob("*.pdf"))
    if not xml or not pdf:
        return {"project": pdir.name, "skipped": "no pdf/netlist"}
    schs = sorted((pdir / "src").rglob("*.kicad_sch"))
    with pikepdf.open(pdf[0]) as p:
        npages = len(p.pages)
    if npages != 1 or len(schs) != 1:
        return {"project": pdir.name, "skipped": f"hierarchical or multi-page ({npages} pages, {len(schs)} sheets)"}
    pins, info = gt_pins(str(schs[0]), str(xml[0]))
    vis = [p for p in pins if not p.hidden and p.net]
    with tempfile.TemporaryDirectory() as td:
        lib_path = pathlib.Path(td) / "embedded.kicad_sym"
        lib_path.write_text(embedded_library_text(str(schs[0])))
        lib = load_library(str(lib_path))
    pi = ingest_pdf(str(pdf[0]), pages=[0]).pages[0]
    cls, clusters, built, res = analyze_page(pi, lib.symbols.values())
    g = built.graph
    prim = {p.id: p for p in pi.prims}
    wire_ends = []
    for o in g.objects:
        if o.kind == "WIRE":
            p = prim[int(o.id[2:])]
            ends = [p.pts[0], p.pts[-1]] if not p.line_closed else []
            wire_ends.append((o.id, ends))
    # oracle attachment
    attached = []
    for gp in vis:
        ws = sorted({oid for oid, ends in wire_ends
                     if any(math.hypot(e[0] - gp.x, e[1] - gp.y) <= TOL_WIRE for e in ends)})
        if ws:
            attached.append((tuple(ws), gp.net))
    rank = {"VERIFIED": 3, "INFERRED": 2, "UNKNOWN": 1, "CONFLICT": 0}

    def oracle_status(a, b):
        best = "UNKNOWN"
        for wa in a:
            for wb in b:
                st = "VERIFIED" if wa == wb else res.pair_status(wa, wb)
                if rank[st] > rank[best]:
                    best = st
        return best

    oracle = pair_metrics(attached, oracle_status)
    # detected-pin matching (greedy nearest, one-to-one)
    tips = sorted(built.pin_tips.items())
    cand = sorted(((math.hypot(t[0] - gp.x, t[1] - gp.y), pi_, oid) for pi_, gp in enumerate(vis) for oid, t in tips
                   if math.hypot(t[0] - gp.x, t[1] - gp.y) <= TOL_PIN))
    used_gt, used_obj, matched = set(), set(), []
    for d, gi, oid in cand:
        if gi in used_gt or oid in used_obj:
            continue
        used_gt.add(gi)
        used_obj.add(oid)
        matched.append((oid, vis[gi].net))
    detected = pair_metrics(matched, lambda a, b: res.pair_status(a, b))
    nets = res.nets
    return {
        "project": pdir.name, "seconds": round(time.time() - t0, 1), "gt": info,
        "gt_pins": len(pins), "gt_pins_visible_with_net": len(vis),
        "oracle_attached_pins": len(attached), "detected_pins": len(tips), "detected_matched_gt": len(matched),
        "prims": len(pi.prims), "wire_objects": sum(1 for o in g.objects if o.kind == "WIRE"),
        "edges": dict(collections.Counter(e.rule for e in g.edges)), "sites": len(g.sites),
        "dots": len(built.dots), "nets": len(nets), "open_nets": sum(n.closure == "OPEN" for n in nets),
        "quarantined": len(res.quarantined), "library_symbols": len(lib.symbols),
        "library_warnings": len(lib.warnings), "oracle": oracle, "detected": detected,
    }


def main(root: str) -> int:
    tot = collections.Counter()
    for d in sorted(p for p in pathlib.Path(root).iterdir() if p.is_dir()):
        try:
            r = evaluate(d)
        except Exception as e:  # noqa: BLE001  one project must never hide the others
            r = {"project": d.name, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-600:]}
        print("GT_EVAL " + json.dumps(r, sort_keys=True), flush=True)
        for mode in ("oracle", "detected"):
            m = r.get(mode)
            if m:
                for k in ("pos", "neg"):
                    tot[f"{mode}_{k}"] += m[k]
                for lv in LEVELS:
                    tot[f"{mode}_TP_{lv}"] += m[lv]["tp"]
                    tot[f"{mode}_FP_{lv}"] += m[lv]["fp"]
    out = {}
    for mode in ("oracle", "detected"):
        tpv, fpv = tot[f"{mode}_TP_VERIFIED"], tot[f"{mode}_FP_VERIFIED"]
        tpi, fpi = tot[f"{mode}_TP_INFERRED"], tot[f"{mode}_FP_INFERRED"]
        pos = max(1, tot[f"{mode}_pos"])
        out[mode] = {"pos": tot[f"{mode}_pos"], "neg": tot[f"{mode}_neg"],
                     "precision_V": round(tpv / max(1, tpv + fpv), 4), "recall_V": round(tpv / pos, 4),
                     "precision_VI": round((tpv + tpi) / max(1, tpv + tpi + fpv + fpi), 4),
                     "recall_VI": round((tpv + tpi) / pos, 4), "FP_V": fpv, "FP_I": fpi}
    print("GT_TOTAL " + json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
