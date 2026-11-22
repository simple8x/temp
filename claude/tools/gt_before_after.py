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
PAGE_KEY_GT = ("project", "sheet", "instance")
GT_TOTAL_FIELDS = ("detected", "detected_local", "coverage", "inventory_consistent", "recovery")
GT_DETECTED_FIELDS = ("pos", "neg", "FP_V", "FP_I")
GT_LOCAL_FIELDS = ("pins", "pin_recall")


def _index_pages(records: list[dict], key_fields: tuple[str, ...], required_fields: tuple[str, ...], label: str) -> tuple[dict, list[str]]:
    """Index page records without silently overwriting duplicates or treating missing as explicit null."""
    pages: dict = {}
    errors: list[str] = []
    if not isinstance(records, list):
        return pages, [f"{label} page records are not a list"]
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"{label} page record {i} is not an object")
            continue
        missing = [k for k in (*key_fields, *required_fields) if k not in record]
        if missing:
            errors.append(f"{label} page record {i} missing fields: {','.join(missing)}")
            continue
        key = tuple(record[k] for k in key_fields)
        try:
            hash(key)
        except TypeError:
            errors.append(f"{label} page record {i} has an unhashable identity")
            continue
        if key in pages:
            errors.append(f"{label} duplicate page identity: {key}")
            continue
        pages[key] = record
    return pages, errors


def _lines(path: str, tag: str) -> list[dict]:
    out = []
    for ln in pathlib.Path(path).read_text(errors="replace").splitlines():
        if ln.startswith(tag + " "):
            out.append(json.loads(ln[len(tag) + 1:]))
    return out


def _pages(path: str) -> tuple[dict, list[str], dict]:
    """Index evaluated sheet records and separately retain explicitly skipped inventory rows.

    GT_EVAL is a mixed stream: evaluated sheet records have prediction fields, while planned
    projects/pages that cannot be evaluated are emitted as {project, skipped}. Treating those
    intentional skip records as malformed page results made a complete run fail its comparator.
    Skips remain part of the comparison and must match exactly between readers.
    """
    evaluated, skipped, errors = [], [], []
    for i, record in enumerate(_lines(path, "GT_EVAL")):
        if not isinstance(record, dict):
            errors.append(f"GT_EVAL record {i} is not an object")
            continue
        if "skipped" in record:
            if not isinstance(record.get("project"), str) or not record["project"] or not isinstance(record["skipped"], str):
                errors.append(f"GT_EVAL skipped record {i} has invalid project/reason")
            else:
                skipped.append(record)
        else:
            evaluated.append(record)
    pages, page_errors = _index_pages(evaluated, PAGE_KEY_GT, (*PRED_KEYS_GT, "recovery"), "GT_EVAL")
    errors.extend(page_errors)
    skip_map, skip_errors = _index_pages(skipped, ("project",), ("skipped",), "GT_EVAL skipped")
    errors.extend(skip_errors)
    return pages, errors, skip_map


def gt_mode(a: str, b: str, out: pathlib.Path) -> int:
    ta, tb = _lines(a, "GT_TOTAL"), _lines(b, "GT_TOTAL")
    if len(ta) != 1 or len(tb) != 1:
        print("GT_BEFORE_AFTER_FAIL missing GT_TOTAL")
        return 1
    ta, tb = ta[0], tb[0]
    pa, ea, sa = _pages(a)
    pb, eb, sb = _pages(b)
    fails, budget_pages = [*ea, *eb], []
    if set(sa) != set(sb):
        fails.append(f"skipped project sets differ: {len(set(sa) ^ set(sb))}")
    for project in sorted(set(sa) & set(sb)):
        if sa[project].get("skipped") != sb[project].get("skipped"):
            fails.append(f"skip disposition differs for project {project}")
    if not isinstance(ta, dict) or not isinstance(tb, dict):
        print("GT_BEFORE_AFTER_FAIL GT_TOTAL is not an object")
        return 1
    for label, total in (("baseline", ta), ("repaired", tb)):
        missing = [k for k in GT_TOTAL_FIELDS if k not in total]
        if missing:
            fails.append(f"{label} GT_TOTAL missing fields: {','.join(missing)}")
            continue
        for parent, fields in (("detected", GT_DETECTED_FIELDS), ("detected_local", GT_LOCAL_FIELDS),
                               ("coverage", ("page_coverage",))):
            obj = total[parent]
            if not isinstance(obj, dict):
                fails.append(f"{label} GT_TOTAL.{parent} is not an object")
            else:
                absent = [k for k in fields if k not in obj]
                if absent:
                    fails.append(f"{label} GT_TOTAL.{parent} missing fields: {','.join(absent)}")
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
    if not isinstance(sa, dict) or not isinstance(sb, dict):
        print("M4_BEFORE_AFTER_FAIL summary is not an object")
        return 1
    for label, summary in (("baseline", sa), ("repaired", sb)):
        required = ("pages", "pairs", "aggregate", "pages_planned", "pages_skipped_by_plan",
                    "pages_with_error", "partial_selection")
        missing = [k for k in required if k not in summary]
        if missing:
            fails.append(f"{label} M4 summary missing fields: {','.join(missing)}")
            continue
        if type(summary["pages_planned"]) is not int or summary["pages_planned"] < 0:
            fails.append(f"{label} M4 pages_planned is not a non-negative integer")
        if type(summary["pages_skipped_by_plan"]) is not int or summary["pages_skipped_by_plan"] != 0:
            fails.append(f"{label} M4 has skipped pages or invalid pages_skipped_by_plan")
        if not isinstance(summary["pages_with_error"], list) or summary["pages_with_error"]:
            fails.append(f"{label} M4 has page errors or invalid pages_with_error")
        if summary["partial_selection"] not in ("", None):
            fails.append(f"{label} M4 is a partial corpus selection")
        if not isinstance(summary["pages"], list) or summary["pages_planned"] != len(summary["pages"]):
            fails.append(f"{label} M4 planned page count does not match evaluated page records")
        for parent, fields in (("pairs", ("TP", "FP", "FN", "TN")),
                               ("aggregate", ("gt_scoreable_pins", "exported_pins_mapped", "mapping_coverage",
                                               "gt_pins_not_exported_UNKNOWN"))):
            obj = summary[parent]
            if not isinstance(obj, dict):
                fails.append(f"{label} M4 summary.{parent} is not an object")
            else:
                absent = [k for k in fields if k not in obj]
                if absent:
                    fails.append(f"{label} M4 summary.{parent} missing fields: {','.join(absent)}")
    pa, ea = _index_pages(sa.get("pages", []), ("page",), PRED_KEYS_M4, "baseline M4")
    pb, eb = _index_pages(sb.get("pages", []), ("page",), PRED_KEYS_M4, "repaired M4")
    fails.extend(ea + eb)
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
