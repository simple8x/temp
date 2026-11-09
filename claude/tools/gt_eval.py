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


def shortest_path(final_edges, a, b):
    """Edge ids on a shortest a-b path over the edges that form the nets (diagnostics only)."""
    adj = collections.defaultdict(list)
    for eid, x, y in final_edges:
        adj[x].append((y, eid))
        adj[y].append((x, eid))
    prev = {a: None}
    q = collections.deque([a])
    while q:
        u = q.popleft()
        if u == b:
            break
        for v, eid in adj[u]:
            if v not in prev:
                prev[v] = (u, eid)
                q.append(v)
    if b not in prev:
        return []
    out, cur = [], b
    while prev[cur] is not None:
        cur, eid = prev[cur][0], prev[cur][1]
        out.append(eid)
    return out[::-1]


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
    attached_records = []
    for gp in vis:
        ws = sorted({oid for oid, ends in wire_ends
                     if any(math.hypot(e[0] - gp.x, e[1] - gp.y) <= TOL_WIRE for e in ends)})
        if ws:
            attached_records.append({"wire_ids": tuple(ws), "net": gp.net, "pin": gp})
    attached = [(r["wire_ids"], r["net"]) for r in attached_records]

    def wire_diagnostic(wire_ids):
        rows = []
        for wid in wire_ids:
            pid = int(wid[2:])
            p = prim[pid]
            rec = cls.records[pid]
            rows.append({
                "id": wid, "prim_id": pid, "class": rec.cls, "role_status": rec.role_status,
                "reasons": list(rec.reasons), "points": [[round(x, 3), round(y, 3)] for x, y in p.pts],
                "stroke_w": p.stroke_w, "stroke_color": list(p.stroke_color) if p.stroke_color else None,
                "origin": p.origin, "provenance": p.prov.content_ref,
            })
        return rows

    def pin_attachment_diagnostic(record):
        gp = record["pin"]
        return {
            "pin": f"{gp.ref}.{gp.number}@{gp.x:.3f},{gp.y:.3f}:{gp.net}",
            "wire_ids": list(record["wire_ids"]),
            "wires": wire_diagnostic(record["wire_ids"]),
        }
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
    # diagnostics: which rules sit on the path of every false positive; details of VERIFIED false positives
    emap = {e.id: e for e in g.edges}
    fp_rules = collections.Counter()
    fp_v_detail = []
    gp_of = {}
    for gp in vis:
        ws = tuple(sorted({oid for oid, ends in wire_ends
                           if any(math.hypot(e[0] - gp.x, e[1] - gp.y) <= TOL_WIRE for e in ends)}))
        if ws:
            gp_of.setdefault((ws, gp.net), gp)
    for i in range(len(attached)):
        for j in range(i + 1, len(attached)):
            if attached[i][1] == attached[j][1]:
                continue
            st = oracle_status(attached[i][0], attached[j][0])
            if st not in LEVELS:
                continue
            best = None
            for wa in attached[i][0]:
                for wb in attached[j][0]:
                    pth = [] if wa == wb else shortest_path(res.final_edges, wa, wb)
                    if wa == wb or pth:
                        best = (wa, wb, pth) if best is None or len(pth) < len(best[2]) else best
            if best is None:
                continue
            rules = sorted({(emap[e].rule if e in emap else ("SITE" if e.startswith("site:") else e.split(":")[0]))
                            for e in best[2]}) or ["SAME_PRIM"]
            fp_rules[st + ":" + "+".join(rules)] += 1
            if st == "VERIFIED" and len(fp_v_detail) < 4:
                ga, gb = gp_of.get((attached[i][0], attached[i][1])), gp_of.get((attached[j][0], attached[j][1]))
                fp_v_detail.append({
                    "pins": [f"{ga.ref}.{ga.number}@{ga.x:.1f},{ga.y:.1f}:{ga.net}" if ga else "?",
                             f"{gb.ref}.{gb.number}@{gb.x:.1f},{gb.y:.1f}:{gb.net}" if gb else "?"],
                    "path": [{"edge": e, "rule": emap[e].rule if e in emap else "?",
                              "coord": emap[e].coord if e in emap else "", "n_prims": emap[e].n_prims if e in emap else -1,
                              "prov": list(emap[e].prov) if e in emap else []} for e in best[2]],
                    "same_prim": best[0] == best[1],
                    "wire_sets": [pin_attachment_diagnostic(attached_records[i]),
                                  pin_attachment_diagnostic(attached_records[j])]})
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
        "quarantined": len(res.quarantined),
        "excluded_internal_symbol_wires": len(built.excluded_wire_prims),
        "excluded_internal_symbol_wire_ids": sorted(built.excluded_wire_prims),
        "library_symbols": len(lib.symbols),
        "library_warnings": len(lib.warnings), "oracle": oracle, "detected": detected,
        "oracle_fp_rules": dict(fp_rules), "oracle_fp_verified_detail": fp_v_detail,
    }


def main(root: str) -> int:
    tot = collections.Counter()
    evaluated = skipped = errors = 0
    for d in sorted(p for p in pathlib.Path(root).iterdir() if p.is_dir()):
        try:
            r = evaluate(d)
        except Exception as e:  # noqa: BLE001  one project must never hide the others
            r = {"project": d.name, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-600:]}
        print("GT_EVAL " + json.dumps(r, sort_keys=True), flush=True)
        if "error" in r:
            errors += 1
        elif "skipped" in r:
            skipped += 1
        else:
            evaluated += 1
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
    out["dataset"] = {"evaluated_projects": evaluated, "skipped_projects": skipped, "error_projects": errors}
    print("GT_TOTAL " + json.dumps(out, sort_keys=True))
    # Skips are reported, not hidden; exceptions and an empty benchmark fail the job.
    return 1 if errors or evaluated == 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
