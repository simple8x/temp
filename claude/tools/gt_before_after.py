#!/usr/bin/env python3
"""Same prediction, two GT readers (873bfbb baseline vs repaired). Never claims a GT is better because a number
went up: it reports both, the denominators, and FAILS if the prediction itself differs between the two runs
(a GT change must not move M4/ECCI output).

  gt_before_after.py gt  BASE_GT_EVAL_LOG REPAIRED_GT_EVAL_LOG OUT_DIR   -> recovery_comparison.json (+ gt metrics)
  gt_before_after.py m4  BASE_SUMMARY_JSON REPAIRED_SUMMARY_JSON OUT_DIR -> m4_evaluation_before_after.json
"""
from __future__ import annotations

import json
import pathlib
import sys

PRED_KEYS_GT = ("detected_pins", "prims", "clusters", "wire_objects")          # prediction-side fields of a gt_eval page record
PRED_KEYS_M4 = ("symbols", "nets_exported", "roundtrip")


def _lines(path: str, tag: str) -> list[dict]:
    out = []
    for ln in pathlib.Path(path).read_text(errors="replace").splitlines():
        if ln.startswith(tag + " "):
            out.append(json.loads(ln[len(tag) + 1:]))
    return out


def _pages(path: str) -> dict:
    pages = {}
    for r in _lines(path, "GT_EVAL"):
        pages[(r.get("project"), r.get("sheet"), r.get("instance"))] = r
    return pages


def gt_mode(a: str, b: str, out: pathlib.Path) -> int:
    ta, tb = _lines(a, "GT_TOTAL"), _lines(b, "GT_TOTAL")
    if len(ta) != 1 or len(tb) != 1:
        print("GT_BEFORE_AFTER_FAIL missing GT_TOTAL")
        return 1
    ta, tb = ta[0], tb[0]
    pa, pb = _pages(a), _pages(b)
    fails, budget_pages = [], []
    if set(pa) != set(pb):
        fails.append(f"page sets differ: {len(set(pa) ^ set(pb))} pages")
    for k in sorted(set(pa) & set(pb), key=str):
        for f in PRED_KEYS_GT:
            if pa[k].get(f) != pb[k].get(f):
                fails.append(f"prediction field {f} differs on {k}")
        ra, rb = pa[k].get("recovery") or {}, pb[k].get("recovery") or {}
        timed = {"SKIPPED_BUDGET", "TIMEOUT"}
        if ra.get("status") in timed or rb.get("status") in timed:
            budget_pages.append(str(k))                          # wall-clock dependent: reported, not compared
        elif (ra.get("status"), ra.get("promotable_pins"), ra.get("candidates")) != \
                (rb.get("status"), rb.get("promotable_pins"), rb.get("candidates")):
            fails.append(f"recovery candidates differ on {k}")      # candidates never read GT

    def metrics(t):
        d = t.get("detected", {})
        dl = t.get("detected_local", {})
        return {"pairs_pos": d.get("pos"), "pairs_neg": d.get("neg"), "FP_V": d.get("FP_V"), "FP_I": d.get("FP_I"),
                "precision_V": d.get("precision_V"), "recall_V": d.get("recall_V"),
                "precision_VI": d.get("precision_VI"), "recall_VI": d.get("recall_VI"),
                "local_pins_denominator": dl.get("pins"), "local_pin_recall": dl.get("pin_recall"),
                "coverage": t.get("coverage", {}).get("page_coverage"),
                "inventory_consistent": t.get("inventory_consistent")}
    rec = {"baseline_gt": ta.get("recovery"), "repaired_gt": tb.get("recovery")}
    res = {"gt_metrics": {"baseline_gt": metrics(ta), "repaired_gt": metrics(tb)}, "recovery": rec,
           "prediction_identical": not fails, "failures": fails, "recovery_budget_dependent_pages": budget_pages,
           "note": "same ECCI prediction scored against two GT readers; a higher recall is NOT evidence of better GT"}
    out.mkdir(parents=True, exist_ok=True)
    (out / "recovery_comparison.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    print("GT_BEFORE_AFTER " + json.dumps(res["gt_metrics"], sort_keys=True))
    for f in fails:
        print("GT_BEFORE_AFTER_FAIL " + f)
    return 1 if fails else 0


def m4_mode(a: str, b: str, out: pathlib.Path) -> int:
    sa, sb = json.loads(pathlib.Path(a).read_text()), json.loads(pathlib.Path(b).read_text())
    fails = []
    pa = {r["page"]: r for r in sa.get("pages", [])}
    pb = {r["page"]: r for r in sb.get("pages", [])}
    if set(pa) != set(pb):
        fails.append("page sets differ")
    for k in sorted(set(pa) & set(pb)):
        for f in PRED_KEYS_M4:
            if pa[k].get(f) != pb[k].get(f):
                fails.append(f"prediction field {f} differs on {k}")

    def m(s):
        p, ag = s.get("pairs", {}), s.get("aggregate", {})
        return {"TP": p.get("TP"), "FP": p.get("FP"), "FN": p.get("FN"), "TN": p.get("TN"),
                "precision": s.get("precision"), "recall_over_mapped_pairs": s.get("recall_over_mapped_pairs"),
                "gt_scoreable_pins": ag.get("gt_scoreable_pins"), "exported_pins_mapped": ag.get("exported_pins_mapped"),
                "mapping_coverage": ag.get("mapping_coverage"),
                "gt_pins_not_exported_UNKNOWN": ag.get("gt_pins_not_exported_UNKNOWN"), "overall": s.get("overall")}
    res = {"baseline_gt": m(sa), "repaired_gt": m(sb), "prediction_identical": not fails, "failures": fails}
    out.mkdir(parents=True, exist_ok=True)
    (out / "m4_evaluation_before_after.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    print("M4_BEFORE_AFTER " + json.dumps({k: res[k] for k in ("baseline_gt", "repaired_gt")}, sort_keys=True))
    for f in fails:
        print("M4_BEFORE_AFTER_FAIL " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    mode, a, b, o = sys.argv[1:5]
    sys.exit({"gt": gt_mode, "m4": m4_mode}[mode](a, b, pathlib.Path(o)))
