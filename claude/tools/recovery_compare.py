#!/usr/bin/env python3
"""P0 acceptance: baseline (recovery OFF) vs candidate (recovery ON) on the SAME GT run configuration.

    recovery_compare.py ON_LOG OFF_LOG

ON_LOG / OFF_LOG are gt_eval logs (GT_RECOVERY=1 / GT_RECOVERY=0) of the same corpus. Checks, fail-closed:
  * every evaluated page appears in both logs, with the same coverage record;
  * every BASELINE field of every page is identical (the "recovery" key and wall-clock "seconds" excluded);
  * every page has a recovery block with invariant == true (the in-process fingerprint guard);
  * candidate (baseline + PROMOTABLE pins) loses nothing per page and adds fp == dup == number_mismatch == 0;
  * every page of the required corpus (CORPUS) was evaluated in both logs.
Prints a per-page table (RECOVERY_PAGE), totals, and RECOVERY_COMPARE PASS|FAIL. Exit 1 unless PASS.
A page that cannot be compared is a FAIL, never a PASS.
"""
from __future__ import annotations

import json
import sys

HARMED = ["One-Air-Max#p1", "One-Air-Max#p2", "sonde xilinx", "complex_hierarchy#p1", "complex_hierarchy#p2",
          "video#p3"]
GAINED = ["interf_u", "video#p6", "Q17ng", "Class-D", "RoyalBlue54L-Feather#p2"]
CORPUS = HARMED + GAINED
VOLATILE = {"recovery", "seconds"}


def read(path: str):
    pages, total, cov = {}, None, None
    with open(path, errors="replace") as f:
        for ln in f:
            if ln.startswith("GT_EVAL "):
                r = json.loads(ln[8:])
                key = (r.get("project"), r.get("sheet"), r.get("instance"))
                if key in pages:
                    raise SystemExit(f"duplicate page record {key} in {path}")
                pages[key] = r
            elif ln.startswith("GT_TOTAL "):
                total = json.loads(ln[9:])
            elif ln.startswith("GT_COVERAGE "):
                cov = json.loads(ln[12:])
    return pages, total, cov


def strip(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in VOLATILE}


def resolve_name(name: str, names) -> str | None:
    if name in names:
        return name
    low = [n for n in names if n.lower() == name.lower()]
    return low[0] if len(low) == 1 else None


def main(on_path: str, off_path: str) -> int:
    on, on_tot, on_cov = read(on_path)
    off, off_tot, off_cov = read(off_path)
    fails = []
    if on_tot is None or off_tot is None:
        fails.append("missing GT_TOTAL (evaluation did not finish)")
    if on_cov != off_cov:
        fails.append("coverage records differ between ON and OFF")
    if set(on) != set(off):
        fails.append(f"page sets differ: only ON {sorted(set(on) - set(off))[:5]}, only OFF {sorted(set(off) - set(on))[:5]}")
    rows, tot = [], {"base": 0, "cand": 0, "local": 0, "new_tp": 0, "fp": 0, "dup": 0, "nm": 0, "promotable": 0,
                     "rec_s": 0.0, "base_s_on": 0.0, "base_s_off": 0.0, "states": {}, "reasons": {}}
    for key in sorted(set(on) & set(off), key=lambda k: tuple(map(str, k))):
        a, b = on[key], off[key]
        if "skipped" in a or "error" in a or "skipped" in b or "error" in b:
            if strip(a) != strip(b):
                fails.append(f"{key[0]}: skip/error record differs")
            continue
        if strip(a) != strip(b):
            diff = sorted(k for k in set(a) | set(b) if k not in VOLATILE and a.get(k) != b.get(k))
            fails.append(f"{key[0]}: BASELINE REGRESSION, fields differ {diff[:6]}")
        if "recovery" in b:
            fails.append(f"{key[0]}: OFF log has a recovery block")
        rv = a.get("recovery")
        if not rv:
            fails.append(f"{key[0]}: no recovery block in ON log")
            continue
        if rv.get("invariant") is not True:
            fails.append(f"{key[0]}: recovery {rv.get('status')} invariant={rv.get('invariant')}")
            continue
        base = b["local_pins_found"]
        cand = base + rv["new_local_found"]
        for bad in ("fp", "dup", "number_mismatch"):
            if rv.get(bad):
                fails.append(f"{key[0]}: recovery {bad}={rv[bad]}")
        if cand < base:
            fails.append(f"{key[0]}: candidate {cand} < baseline {base}")
        tot["base"] += base
        tot["cand"] += cand
        tot["local"] += b["local_pins"]
        tot["new_tp"] += rv["new_tp"]
        tot["fp"] += rv["fp"]
        tot["dup"] += rv["dup"]
        tot["nm"] += rv["number_mismatch"]
        tot["promotable"] += rv["promotable_pins"]
        tot["rec_s"] += rv.get("seconds", 0.0)
        tot["base_s_on"] += a.get("seconds", 0.0)
        tot["base_s_off"] += b.get("seconds", 0.0)
        for k, v in rv["candidates"].items():
            tot["states"][k] = tot["states"].get(k, 0) + v
        for k, v in rv["reasons"].items():
            tot["reasons"][k] = tot["reasons"].get(k, 0) + v
        d, dv = b["detected"], b["detected"]
        rows.append({"page": key[0], "sheet": key[1], "scoreable": b["gt_pins_visible_with_net"],
                     "local_pins": b["local_pins"], "detected_pins": b["detected_pins"],
                     "baseline_found": base, "candidate_found": cand, "new_tp": rv["new_tp"], "fp": rv["fp"],
                     "dup": rv["dup"], "number_mismatch": rv["number_mismatch"],
                     "V_tp": d["VERIFIED"]["tp"], "V_fp": d["VERIFIED"]["fp"],
                     "I_tp": dv["INFERRED"]["tp"], "I_fp": dv["INFERRED"]["fp"],
                     "states": rv["candidates"], "base_seconds": b.get("seconds"), "rec_seconds": rv.get("seconds")})
    names = {k[0] for k in set(on) & set(off) if "skipped" not in on[k] and "error" not in on[k]}
    for name in CORPUS:
        n = resolve_name(name, names)
        tag = "HARMED" if name in HARMED else "GAINED"
        row = next((r for r in rows if r["page"] == n), None) if n else None
        if row is None:
            fails.append(f"required corpus page {name} not evaluated/compared")
            print(f"RECOVERY_CORPUS {tag} {name} MISSING")
        else:
            print(f"RECOVERY_CORPUS {tag} {name} baseline={row['baseline_found']} candidate={row['candidate_found']} "
                  f"new_tp={row['new_tp']} fp={row['fp']} dup={row['dup']} states={json.dumps(row['states'], sort_keys=True)}")
    for r in rows:
        print("RECOVERY_PAGE " + json.dumps(r, sort_keys=True))
    lp = max(1, tot["local"])
    summary = {"pages": len(rows), "local_pins": tot["local"], "baseline_found": tot["base"],
               "candidate_found": tot["cand"], "baseline_pin_recall": round(tot["base"] / lp, 4),
               "candidate_pin_recall": round(tot["cand"] / lp, 4), "promotable_pins": tot["promotable"],
               "new_tp": tot["new_tp"], "fp": tot["fp"], "dup": tot["dup"], "number_mismatch": tot["nm"],
               "candidate_pin_precision": round(tot["new_tp"] / max(1, tot["promotable"]), 4),
               "states": tot["states"], "reasons": tot["reasons"], "recovery_seconds": round(tot["rec_s"], 1),
               "baseline_seconds_on": round(tot["base_s_on"], 1), "baseline_seconds_off": round(tot["base_s_off"], 1),
               "detected_on": (on_tot or {}).get("detected"), "detected_off": (off_tot or {}).get("detected")}
    if on_tot and off_tot and {k: v for k, v in on_tot.items() if k != "recovery"} != off_tot:
        fails.append("GT_TOTAL baseline fields differ between ON and OFF")
    print("RECOVERY_SUMMARY " + json.dumps(summary, sort_keys=True))
    for f in fails:
        print("RECOVERY_FAIL " + f)
    print("RECOVERY_COMPARE " + ("FAIL" if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
