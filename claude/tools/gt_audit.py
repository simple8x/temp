#!/usr/bin/env python3
"""Forensic GT audit: baseline GT reader (frozen copy of 873bfbb, tools/baseline/gt_kicad_873bfbb.py) versus the
repaired reader (ecci.gt_kicad.gt_pin_records) on the SAME planned pages. Reads only KiCad files (sch + netlist),
never ECCI output, so nothing here can move with a prediction.

Writes into OUT (default out/gt_audit):
  gt_audit.json              per-record claims (position / net / consistency kept apart) + counts by status, reason
  gt_change_ledger.json      every record whose scoreable net differs baseline -> repaired, with source + rule
  gt_unresolved.csv          every record that is not VERIFIED (INFERRED / UNKNOWN / CONFLICT), for manual review
  gt_baseline_vs_repaired.json  denominators per page and in total, before and after, with the ledgered delta

Exit 1 (fail closed) when: a record exists in one reader and not the other (records are never deleted), the
denominator delta is not exactly the ledgered delta, a ledger entry has no rule, or any change PROMOTES a pin
(baseline unscoreable -> repaired scoreable) by a rule other than F2 or for a record that is UNKNOWN/CONFLICT.
Usage: gt_audit.py GT_ROOT [OUT] ; GT_ONLY selects pages like gt_eval.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

from ecci.gt_kicad import gt_pin_records  # noqa: E402

BASELINE_FILE = ROOT / "tools" / "baseline" / "gt_kicad_873bfbb.py"
# sha256 of `git show 873bfbb:claude/ecci/gt_kicad.py` (blob 553b6c95...) BEFORE the one-line import rewrite
BASELINE_BLOB = "553b6c95e13f5b83686421f528be9b97c2a88aff"

RULES = {
    "netlist_node_on_several_nets": ("F1", "netlist XML", "baseline took the LAST net for a node listed under "
                                     "several nets (no evidence for the choice); repaired marks CONFLICT"),
    "coincident_pin_on_other_net": ("F2", "sch geometry + netlist", "same-point test: baseline 0.1 pt cells "
                                    "(split at cell borders, merged up to ~0.14 pt); repaired 1 um + neighbours"),
    "body_style_unhandled": ("F3", "sch symbol record", "symbol carries body_style but no convert; reader cannot "
                             "select the body: fail closed (UNKNOWN)"),
}


# Only F2 can make a pin scoreable again: the baseline excluded it for a same-point conflict between pins that are
# NOT at the same point (> 1 um apart). Every other rule is fail-closed and may only remove a pin from scoring.
RESTORING_RULES = {"F2"}


def load_baseline():
    spec = importlib.util.spec_from_file_location("gt_kicad_873bfbb", BASELINE_FILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gt_kicad_873bfbb"] = mod
    spec.loader.exec_module(mod)
    return mod


def select_gt_pins():
    """gt_pins of the reader named by GT_PINS_IMPL: unset/'repaired' -> ecci.gt_kicad, '873bfbb' -> frozen baseline.
    Anything else fails (no silent fallback). Used by gt_eval/m4_gt so both readers run on the same prediction."""
    impl = os.environ.get("GT_PINS_IMPL", "repaired")
    if impl == "repaired":
        from ecci.gt_kicad import gt_pins
        return gt_pins
    if impl == "873bfbb":
        return load_baseline().gt_pins
    raise SystemExit(f"GT_PINS_IMPL={impl!r} unknown")


def sha(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _key(ref, number, x, y):
    return (ref, number, round(x, 6), round(y, 6))


def baseline_cell_bad_refs(recs: list[dict]) -> set[str]:
    """The 873bfbb same-point test (0.1 pt cells, no neighbours) re-run on the repaired pre-consistency nets."""
    at: dict = {}
    for r in recs:
        if r.get("net_pre_consistency") and not r["hidden"]:
            at.setdefault((round(r["x"] / 0.1), round(r["y"] / 0.1)), []).append(r)
    bad: set[str] = set()
    for rs in at.values():
        if len({r["net_pre_consistency"] for r in rs}) > 1:
            bad |= {r["ref"] for r in rs}
    return bad


def audit_task(base, sch: str, xml: str, instance: str | None, page: str) -> dict:
    old, _ = base.gt_pins(sch, xml, instance_path=instance)
    recs, info = gt_pin_records(sch, xml, instance_path=instance)
    new = [r for r in recs if r["number"] is not None and r["x"] is not None]
    old_by: dict = {}
    for p in old:
        old_by.setdefault(_key(p.ref, p.number, p.x, p.y), []).append(p)
    new_by: dict = {}
    for r in new:
        new_by.setdefault(_key(r["ref"], r["number"], r["x"], r["y"]), []).append(r)
    problems = []
    if sorted((k, len(v)) for k, v in old_by.items()) != sorted((k, len(v)) for k, v in new_by.items()):
        problems.append("record_set_differs")
    src = {"sch": sch, "sch_sha256": sha(sch), "netlist": xml, "netlist_sha256": sha(xml), "instance_path": instance}
    ledger = []
    old_bad = baseline_cell_bad_refs(new)
    for k, rs in new_by.items():
        for i, r in enumerate(rs):
            ops = old_by.get(k, [])
            o = ops[i] if i < len(ops) else None
            before = (o.net if o is not None and not o.hidden else None)
            after = r["net"] if not r["hidden"] else None
            r["id"] = f"{page}|{r['ref']}|{r['number']}|{r['symbol_uuid']}|{i}"
            r["scoreable_before"] = before is not None
            if before == after:
                continue
            rule = next((RULES[x] for x in r["reasons"] if x in RULES), None)
            if rule is None and before is None and r["ref"] in old_bad and r["consistency"] == "OK":
                rule = RULES["coincident_pin_on_other_net"]
            ledger.append({"record_id": r["id"], "before": before, "after": after,
                           "source": src | {"symbol_uuid": r["symbol_uuid"], "lib_pin_at_mm": r.get("lib_pin_at"),
                                            "pin_at_pt": [round(r["x"], 4), round(r["y"], 4)]},
                           "rule": rule[0] if rule else None, "evidence_type": rule[1] if rule else None,
                           "reason": rule[2] if rule else "UNEXPLAINED", "reasons": r["reasons"],
                           "status": r["status"], "promotion": before is None and after is not None})
    d_old = sum(1 for p in old if p.net and not p.hidden)
    d_new = sum(1 for r in new if r["net"] and not r["hidden"])
    return {"page": page, "records": recs, "info": info, "ledger": ledger, "problems": problems,
            "denominator_before": d_old, "denominator_after": d_new}


def check(results: list[dict]) -> list[str]:
    errs = []
    for t in results:
        for p in t["problems"]:
            errs.append(f"{t['page']}: {p}")
        delta = t["denominator_after"] - t["denominator_before"]
        led = sum((1 if e["after"] else 0) - (1 if e["before"] else 0) for e in t["ledger"])
        if delta != led:
            errs.append(f"{t['page']}: denominator delta {delta} != ledgered {led}")
        for e in t["ledger"]:
            if e["rule"] is None:
                errs.append(f"{t['page']}: unexplained change {e['record_id']}")
            if e["promotion"] and (e["rule"] not in RESTORING_RULES or e["status"] in ("UNKNOWN", "CONFLICT")):
                errs.append(f"{t['page']}: promotion {e['record_id']} without a restoring rule")
    return errs


def write(results: list[dict], out: pathlib.Path, commit: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    by_status: dict = {}
    by_reason: dict = {}
    recs = [r for t in results for r in t["records"]]
    for r in recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        for x in r["reasons"]:
            by_reason[x] = by_reason.get(x, 0) + 1
    summary = {"commit": commit, "baseline_reader_blob": BASELINE_BLOB, "pages": len(results),
               "records": len(recs), "by_status": by_status, "by_reason": by_reason,
               "scored_by_status": {s: sum(1 for r in recs if r["net"] and not r["hidden"] and r["status"] == s)
                                    for s in ("VERIFIED", "INFERRED", "UNKNOWN", "CONFLICT")},
               "hidden_not_observable": sum(1 for r in recs if r["hidden"]),
               "info": {t["page"]: t["info"] for t in results}}
    (out / "gt_audit.json").write_text(json.dumps({"summary": summary, "records": recs}, default=list, indent=0))
    ledger = [e | {"commit": commit, "tests": "tests/unit/test_gt_repair.py"} for t in results for e in t["ledger"]]
    (out / "gt_change_ledger.json").write_text(json.dumps(ledger, indent=1))
    with open(out / "gt_unresolved.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "page", "ref", "number", "lib_id", "status", "position_status", "net_status",
                    "consistency", "reasons", "sch", "instance_path"])
        for t in results:
            for r in t["records"]:
                if r["status"] != "VERIFIED":
                    w.writerow([r.get("id", ""), t["page"], r["ref"], r["number"], r["lib_id"], r["status"],
                                r["position_status"], r["net_status"], r.get("consistency"),
                                ";".join(r["reasons"]), r["sch"], r["instance_path"]])
    bvr = {"commit": commit, "pages": {t["page"]: {"before": t["denominator_before"], "after": t["denominator_after"],
                                                   "ledgered_changes": len(t["ledger"])} for t in results}}
    bvr["total"] = {"before": sum(t["denominator_before"] for t in results),
                    "after": sum(t["denominator_after"] for t in results),
                    "changes": sum(len(t["ledger"]) for t in results)}
    (out / "gt_baseline_vs_repaired.json").write_text(json.dumps(bvr, indent=1))
    return summary


def main(gt_root: str, out: str) -> int:
    import gt_eval  # needs pikepdf; planning is identical to gt_eval's
    base = load_baseline()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    results = []
    for pdir in sorted(p for p in pathlib.Path(gt_root).iterdir() if p.is_dir()):
        for slot in gt_eval._safe_plan(pdir):
            t = slot.get("task")
            if not t:
                continue
            xml = sorted(pathlib.Path(t["pdir"]).glob("*.xml"))[0]
            results.append(audit_task(base, t["sch"], str(xml), t["instance"], t["name"]))
    if not results:
        print("GT_AUDIT_FAIL no page audited")
        return 1
    summary = write(results, pathlib.Path(out), commit)
    errs = check(results)
    print("GT_AUDIT " + json.dumps({k: summary[k] for k in ("pages", "records", "by_status", "scored_by_status")}))
    for e in errs:
        print("GT_AUDIT_FAIL " + e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(str(ROOT), "out", "gt_audit")))
