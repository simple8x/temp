"""Evaluate ECCI against KiCad-computed ground truth (M4.0). Prints one JSON line per project.

Two measurements, so that failures are attributed to the right stage:
  oracle   GT pin POSITIONS (from the .kicad_sch) are attached to wires by geometry; measures wire
           connectivity and site handling independently of symbol/pin detection.
  detected our own detected pins are matched to GT pin positions; measures the whole pipeline.
A GT pair is positive iff KiCad's netlist puts both pins in one net. Only single-sheet, single-page
projects are evaluated; hierarchical designs are reported as skipped.
"""
import collections
import signal
import json
import math
import pathlib
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pikepdf  # noqa: E402

from ecci.gt_kicad import embedded_library_text, find_root, gt_pins, hierarchy, wire_component_index  # noqa: E402
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


MAX_PAGE_PINS = 1500
PAGE_SECONDS = 150            # wall-clock budget per page; a page over budget is reported, never silently dropped


def _on_alarm(signum, frame):
    raise TimeoutError(f"page exceeded {PAGE_SECONDS}s")


def _grid(pts):
    return {(round(x / 0.5), round(y / 0.5)) for x, y in pts}


def _near(g, x, y):
    cx, cy = round(x / 0.5), round(y / 0.5)
    return any((cx + i, cy + j) in g for i in (-1, 0, 1) for j in (-1, 0, 1))


def map_pages_to_sheets(pdf_path: str, schs: list, npages: int):
    """Page -> sheet by geometry only: the share of a sheet's wire end points (mm -> pt) found among the end
    points of the page's polylines. Both come from KiCad (file vs plot), neither from ECCI. A page is mapped only
    if one sheet clearly wins (>= 0.8 and >= 0.2 above the runner-up) and no other page claims the same sheet."""
    from ecci.gt_kicad import MM_TO_PT, read_sch
    sheets = []
    for sp in schs:
        sc = read_sch(str(sp))
        sheets.append([(x * MM_TO_PT, y * MM_TO_PT) for w in sc["wires"] for x, y in w])
    res = {}
    ingest = ingest_pdf(pdf_path, pages=list(range(npages)))
    for k, pg in enumerate(ingest.pages):
        g = _grid([q for p in pg.prims for q in (p.pts[0], p.pts[-1])])
        sc = [sum(_near(g, x, y) for x, y in ends) / max(1, len(ends)) if ends else 0.0 for ends in sheets]
        order = sorted(range(len(sc)), key=lambda i: -sc[i])
        best = order[0]
        second = sc[order[1]] if len(order) > 1 else 0.0
        top3 = [(schs[i].name, round(sc[i], 2)) for i in order[:3]]
        if sc[best] >= 0.8 and sc[best] - second >= 0.2:
            res[k] = (best, round(sc[best], 3), round(second, 3), top3)
        else:
            res[k] = (None, round(sc[best], 3), round(second, 3), top3)
    claimed = collections.Counter(v[0] for v in res.values() if v[0] is not None)
    return {k: (v if v[0] is None or claimed[v[0]] == 1 else (None, v[1], v[2], v[3] + ["claimed-by-several-pages"]))
            for k, v in res.items()}


def evaluate_project(pdir: pathlib.Path) -> list[dict]:
    xml = sorted(pdir.glob("*.xml"))
    pdf = sorted(pdir.glob("*.pdf"))
    if not xml or not pdf:
        return [{"project": pdir.name, "skipped": "no pdf/netlist"}]
    schs = sorted((pdir / "src").rglob("*.kicad_sch"))
    with pikepdf.open(pdf[0]) as p:
        npages = len(p.pages)
    if npages == 1 and len(schs) == 1:
        return [evaluate(pdir, 0, schs[0], pdir.name)]
    if npages > 80 or len(schs) > 80:
        return [{"project": pdir.name, "skipped": f"too large ({npages} pages, {len(schs)} sheets)"}]
    mp = map_pages_to_sheets(str(pdf[0]), schs, npages)
    inst = {}
    try:      # repeated sheets: pages follow the depth-first sheet order; trust it only if EVERY page agrees with geometry
        root = find_root([str(x) for x in schs], str(pdir / "src"))
        hier = hierarchy(str(root)) if root else []
        geo_top = {k: pathlib.Path(v[3][0][0]).name for k, v in mp.items()}
        if len(hier) == npages and all(hier[k][1].name == geo_top[k] and mp[k][1] >= 0.8 for k in range(npages)):
            for k in range(npages):
                si = next(i for i, x in enumerate(schs) if x == hier[k][1])
                inst[k] = hier[k][0]
                mp[k] = (si, mp[k][1], mp[k][2], mp[k][3])
    except Exception as e:  # noqa: BLE001
        print("GT_HIER_FAIL", pdir.name, type(e).__name__, e, flush=True)
    out = []
    for k, (si, score, second, top3) in sorted(mp.items()):
        name = f"{pdir.name}#p{k}"
        if si is None:
            out.append({"project": name, "skipped": f"page not mapped to one sheet (best {score}, runner-up {second})", "map_top3": top3})
            continue
        try:
            print("GT_PAGE_START", name, flush=True)
            signal.signal(signal.SIGALRM, _on_alarm)
            signal.alarm(PAGE_SECONDS)
            try:
                r = evaluate(pdir, k, schs[si], name, inst.get(k))
            finally:
                signal.alarm(0)
            r["sheet"] = str(schs[si].relative_to(pdir / "src"))
            r["instance"] = inst.get(k)
            r["map_score"] = score
        except Exception as e:  # noqa: BLE001
            r = {"project": name, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-600:]}
        out.append(r)
    return out


def evaluate(pdir: pathlib.Path, page: int, sch: pathlib.Path, name: str, instance_path: str | None = None) -> dict:
    t0 = time.time()
    xml = sorted(pdir.glob("*.xml"))
    pdf = sorted(pdir.glob("*.pdf"))
    schs = [sch]
    pins, info = gt_pins(str(schs[0]), str(xml[0]), instance_path=instance_path)
    vis = [p for p in pins if not p.hidden and p.net]
    if len(vis) > MAX_PAGE_PINS:      # pair metrics are O(n^2) with a path search per false positive
        return {"project": name, "skipped": f"{len(vis)} scoreable pins on one page > {MAX_PAGE_PINS} (pair metric budget)"}
    with tempfile.TemporaryDirectory() as td:
        lib_path = pathlib.Path(td) / "embedded.kicad_sym"
        lib_path.write_text(embedded_library_text(str(schs[0])))
        lib = load_library(str(lib_path))
    pi = ingest_pdf(str(pdf[0]), pages=[page]).pages[0]
    cls, clusters, built, res = analyze_page(pi, lib.symbols.values())
    g = built.graph
    prim = {p.id: p for p in pi.prims}
    wire_ends = []
    for o in g.objects:
        if o.kind == "WIRE":
            p = prim[int(o.id[2:])]
            ends = [p.pts[0], p.pts[-1]] if not p.line_closed else []
            wire_ends.append((o.id, ends))
    comp_of = wire_component_index(str(schs[0]))
    # oracle attachment
    attached = []
    comps = []
    for gp in vis:
        ws = sorted({oid for oid, ends in wire_ends
                     if any(math.hypot(e[0] - gp.x, e[1] - gp.y) <= TOL_WIRE for e in ends)})
        if ws:
            attached.append((tuple(ws), gp.net))
            comps.append(comp_of(gp.x, gp.y))
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
    # recall split: pairs whose pins sit on the SAME connected set of wires in the sheet file ("local": visible in
    # the drawing as wires) versus pairs on the same net only through labels, power symbols or other sheets
    loc = collections.Counter()
    for i in range(len(attached)):
        for j in range(i + 1, len(attached)):
            if attached[i][1] != attached[j][1]:
                continue
            kind = "local" if comps[i] is not None and comps[i] == comps[j] else "nonlocal"
            st = oracle_status(attached[i][0], attached[j][0])
            loc[kind + "_pos"] += 1
            if st == "VERIFIED":
                loc[kind + "_V"] += 1
            if st in LEVELS:
                loc[kind + "_VI"] += 1
    oracle["split"] = dict(loc)
    # diagnostics: which rules sit on the path of every false positive; details of VERIFIED false positives
    emap = {e.id: e for e in g.edges}
    fp_rules = collections.Counter()
    fp_v_detail = []
    fp_i_detail = []
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
            if (st == "VERIFIED" and len(fp_v_detail) < 4) or (st == "INFERRED" and len(fp_i_detail) < 3):
                ga, gb = gp_of.get((attached[i][0], attached[i][1])), gp_of.get((attached[j][0], attached[j][1]))
                (fp_v_detail if st == "VERIFIED" else fp_i_detail).append({
                    "pins": [f"{ga.ref}.{ga.number}@{ga.x:.1f},{ga.y:.1f}:{ga.net}" if ga else "?",
                             f"{gb.ref}.{gb.number}@{gb.x:.1f},{gb.y:.1f}:{gb.net}" if gb else "?"],
                    "path": [{"edge": e, "rule": emap[e].rule if e in emap else "?",
                              "coord": emap[e].coord if e in emap else "", "n_prims": emap[e].n_prims if e in emap else -1,
                              "roles": [(r.kind, r.status, len(r.evidence)) for r in emap[e].roles] if e in emap else [],
                              "prov": list(emap[e].prov) if e in emap else []} for e in best[2]],
                    "same_prim": best[0] == best[1]})
    # detected-pin matching (greedy nearest, one-to-one)
    tips = sorted(built.pin_tips.items())
    cand = sorted(((math.hypot(t[0] - gp.x, t[1] - gp.y), pi_, oid) for pi_, gp in enumerate(vis) for oid, t in tips
                   if math.hypot(t[0] - gp.x, t[1] - gp.y) <= TOL_PIN))
    used_gt, used_obj, matched, matched_comp = set(), set(), [], []
    for d, gi, oid in cand:
        if gi in used_gt or oid in used_obj:
            continue
        used_gt.add(gi)
        used_obj.add(oid)
        matched.append((oid, vis[gi].net))
        matched_comp.append(comp_of(vis[gi].x, vis[gi].y))
    detected = pair_metrics(matched, lambda a, b: res.pair_status(a, b))
    dloc = collections.Counter()
    for i in range(len(matched)):
        for j in range(i + 1, len(matched)):
            if matched[i][1] != matched[j][1]:
                continue
            kind = "local" if matched_comp[i] is not None and matched_comp[i] == matched_comp[j] else "nonlocal"
            st = res.pair_status(matched[i][0], matched[j][0])
            dloc[kind + "_pos"] += 1
            dloc[kind + "_V"] += st == "VERIFIED"
            dloc[kind + "_VI"] += st in LEVELS
    detected["split"] = dict(dloc)
    # pins of the GT that sit on a wire component (the ones a reader can follow) and whether we found them
    n_local_pins = sum(1 for gp in vis if comp_of(gp.x, gp.y) is not None)
    n_local_found = sum(1 for k in used_gt if comp_of(vis[k].x, vis[k].y) is not None)
    miss = collections.Counter()
    miss_lib = collections.Counter()
    for k, gp in enumerate(vis):
        if k in used_gt or comp_of(gp.x, gp.y) is None:
            continue
        near = [c for c in clusters if c.cluster.bbox[0] - 4 <= gp.x <= c.cluster.bbox[2] + 4
                and c.cluster.bbox[1] - 4 <= gp.y <= c.cluster.bbox[3] + 4]
        if not near:
            miss["no_cluster"] += 1
            miss_lib["no_cluster:" + gp.lib_id] += 1
        else:
            near.sort(key=lambda c: (c.cluster.bbox[2] - c.cluster.bbox[0]) * (c.cluster.bbox[3] - c.cluster.bbox[1]))
            c = near[0]
            big = len(c.cluster.prim_ids) > 150
            miss[("giant_" if big else "") + c.resolution.status] += 1
            miss_lib[("giant_" if big else "") + c.resolution.status + ":" + gp.lib_id] += 1
    nets = res.nets
    return {
        "project": name, "seconds": round(time.time() - t0, 1), "gt": info,
        "gt_pins": len(pins), "gt_pins_visible_with_net": len(vis),
        "oracle_attached_pins": len(attached), "local_pins": n_local_pins, "local_pins_found": n_local_found, "miss_reasons": dict(miss), "miss_libs": dict(miss_lib), "detected_pins": len(tips), "detected_matched_gt": len(matched),
        "prims": len(pi.prims), "wire_objects": sum(1 for o in g.objects if o.kind == "WIRE"),
        "edges": dict(collections.Counter(e.rule for e in g.edges)), "sites": len(g.sites),
        "dots": len(built.dots), "nets": len(nets), "open_nets": sum(n.closure == "OPEN" for n in nets),
        "quarantined": len(res.quarantined), "library_symbols": len(lib.symbols),
        "library_warnings": len(lib.warnings), "oracle": oracle, "detected": detected,
        "oracle_fp_rules": dict(fp_rules), "oracle_fp_verified_detail": fp_v_detail, "oracle_fp_inferred_detail": fp_i_detail,
        "cluster_status": dict(collections.Counter(c.resolution.status for c in clusters)),
        "clusters": len(clusters), "wire_style": str(cls.wire_style), "style_notes": [n for n in cls.notes if "wire style" in n],
        "library_names_sample": sorted(lib.symbols)[:3],
        "dot_radii": sorted(round(v[1], 2) for v in built.dots.values())[:8],
    }


def main(root: str) -> int:
    tot = collections.Counter()
    miss_libs_tot = collections.Counter()
    for d in sorted(p for p in pathlib.Path(root).iterdir() if p.is_dir()):
        try:
            rs = evaluate_project(d)
        except Exception as e:  # noqa: BLE001  one project must never hide the others
            rs = [{"project": d.name, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-600:]}]
        for r in rs:
            print("GT_EVAL " + json.dumps(r, sort_keys=True), flush=True)
            for k, v in (r.get("oracle", {}).get("split") or {}).items():
                tot["split_" + k] += v
            for k, v in (r.get("detected", {}).get("split") or {}).items():
                tot["dsplit_" + k] += v
            for k, v in (r.get("miss_libs") or {}).items():
                miss_libs_tot[k] += v
            for k, v in (r.get("miss_reasons") or {}).items():
                tot["miss_" + k] += v
            tot["local_pins"] += r.get("local_pins", 0)
            tot["local_pins_found"] += r.get("local_pins_found", 0)
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
    for k, v in miss_libs_tot.most_common(45):
        print("MISSLIB", v, k)
    sp = {k[6:]: v for k, v in tot.items() if k.startswith("split_")}
    for kind in ("local", "nonlocal"):
        n = max(1, sp.get(kind + "_pos", 0))
        out["oracle_" + kind] = {"pos": sp.get(kind + "_pos", 0), "recall_V": round(sp.get(kind + "_V", 0) / n, 4),
                                 "recall_VI": round(sp.get(kind + "_VI", 0) / n, 4)}
    dsp = {k[7:]: v for k, v in tot.items() if k.startswith("dsplit_")}
    n = max(1, dsp.get("local_pos", 0))
    out["detected_local"] = {"pos": dsp.get("local_pos", 0), "recall_V": round(dsp.get("local_V", 0) / n, 4),
                             "recall_VI": round(dsp.get("local_VI", 0) / n, 4),
                             "pin_recall": round(tot["local_pins_found"] / max(1, tot["local_pins"]), 4),
                             "pins": tot["local_pins"],
                             "misses": {k[5:]: v for k, v in tot.items() if k.startswith("miss_")}}
    print("GT_TOTAL " + json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
