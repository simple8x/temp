#!/usr/bin/env python3
"""M4.2 on the KiCad ground-truth corpus (gt_out from claude-gt): every page that gt_eval plans is run through
PDF -> documents -> .kicad_sch -> FRESH kicad-cli netlist -> V-W round trip -> V-G partition vs KiCad's own
netlist of the source design.

The GT data is the cached export of claude-gt (its provenance is checked by gt_verify.sh in the same job);
the KiCad netlist of OUR schematic is always produced fresh here (the file is deleted first and must be newer
than the call). Prints M4_GT_PAGE lines and M4_GT_SUMMARY; writes out/m4/gt/<page>/* + summary.json.
Exit 0 only if every page was evaluated and every gate is PASS. Env: M4_ONLY (comma list like GT_ONLY),
M4_WORKERS, M4_PAGE_SECONDS, M4_GT_POLICY (JSON {"min_mapping_coverage": x}; default tools/m4_gt_policy.json).
The project documents no M4.2 mapping-coverage threshold, so no policy file is shipped: without one every
ground_truth gate is BLOCKED (never PASS) until the owner sets the threshold.
"""
import concurrent.futures
import json
import os
import pathlib
import re
import signal
import sys
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
os.environ.setdefault("GT_ONLY", os.environ.get("M4_ONLY", ""))

import gt_eval  # noqa: E402
from ecci import kicad_roundtrip as kr  # noqa: E402
from gt_audit import select_gt_pins  # noqa: E402
from ecci.kicad_export import raw_library_from_schematic  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402
from ecci.kicad_lib import load_library  # noqa: E402
from ecci.m4_pipeline import FAIL, PASS, env_cli, gate_table, run_page  # noqa: E402
from ecci.rules import analyze_page  # noqa: E402

PAGE_SECONDS = int(os.environ.get("M4_PAGE_SECONDS", "600"))
POLICY_PATH = os.environ.get("M4_GT_POLICY") or str(ROOT / "tools" / "m4_gt_policy.json")
OUT = pathlib.Path(os.environ["M4_GT_OUT"]) if os.environ.get("M4_GT_OUT") else ROOT / "out" / "m4" / "gt"
gt_pins = select_gt_pins()


def _alarm(signum, frame):
    raise TimeoutError(f"page exceeded {PAGE_SECONDS}s")


def one(t: dict, cli: str | None, policy: dict | None) -> dict:
    name = t["name"]
    safe = name.replace("/", "_").replace("#", "__").replace(" ", "_")
    t0 = time.time()
    try:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(PAGE_SECONDS)
        try:
            pdir, sch = pathlib.Path(t["pdir"]), pathlib.Path(t["sch"])
            xml = sorted(pdir.glob("*.xml"))[0]
            pdf = sorted(pdir.glob("*.pdf"))[0]
            pins, _ = gt_pins(str(sch), str(xml), instance_path=t["instance"])
            out = OUT / safe
            out.mkdir(parents=True, exist_ok=True)
            lib_path = out / "library.kicad_sym"
            # the exact lib_symbols text of the source design (what was drawn), not a re-serialization
            lib_path.write_text(raw_library_from_schematic(sch.read_text(encoding="utf-8")), encoding="utf-8")
            lib = load_library(str(lib_path))
            lib_text = lib_path.read_text(encoding="utf-8")
            pi = ingest_pdf(str(pdf), pages=[t["page"]]).pages[0]
            cls, clusters, built, res = analyze_page(pi, lib.symbols.values())
            rec = run_page(out, pi, cls, clusters, built, res, lib, lib_text,
                           {"name": pdf.name, "page": t["page"], "project": pdir.name}, pins, cli, gt_policy=policy)
        finally:
            signal.alarm(0)
    except Exception as e:  # noqa: BLE001  never hides: an error is a FAIL record for this page
        rec = {"stages": {"connectivity_schema_roundtrip": FAIL}, "error": f"{type(e).__name__}: {e}",
               "trace": traceback.format_exc()[-600:]}
    rec["page"] = name
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main(root: str) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cli = env_cli()
    policy = kr.load_gt_policy(POLICY_PATH)       # malformed -> raises (fail closed); missing -> None -> BLOCKED
    version = kr.kicad_version(cli) if cli else None
    dirs = sorted(p for p in pathlib.Path(root).iterdir() if p.is_dir() and gt_eval.wanted(p.name))
    workers = max(1, int(os.environ.get("M4_WORKERS", os.cpu_count() or 1)))
    t0 = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
        slots = list(ex.map(gt_eval._safe_plan, dirs))
        tasks = [s["task"] for ps in slots for s in ps if "task" in s]
        skipped = [s for ps in slots for s in ps if "task" not in s]
        futs = [ex.submit(one, t, cli, policy) for t in tasks]
        records = []
        for f in concurrent.futures.as_completed(futs):
            r = f.result()
            records.append(r)
            print("M4_GT_PAGE " + json.dumps({k: r.get(k) for k in ("page", "seconds", "stages", "symbols",
                                                                     "nets_exported", "roundtrip", "ground_truth",
                                                                     "error", "kicad_error")}), flush=True)
    records.sort(key=lambda r: r["page"])
    tot = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    agg = {"placed": 0, "clusters": 0, "nets": 0, "gt_scoreable_pins": 0, "exported_pins_mapped": 0,
           "gt_pins_not_exported_UNKNOWN": 0}
    for r in records:
        g = r.get("ground_truth") or {}
        for k in tot:
            tot[k] += g.get("pairs", {}).get(k, 0)
        agg["placed"] += (r.get("symbols") or {}).get("placed", 0)
        agg["clusters"] += (r.get("symbols") or {}).get("clusters", 0)
        agg["nets"] += r.get("nets_exported", 0) or 0
        for k in ("gt_scoreable_pins", "exported_pins_mapped", "gt_pins_not_exported_UNKNOWN"):
            agg[k] += g.get(k, 0)
    errors = [r["page"] for r in records if r.get("error")]
    gt_states, gt_reasons = {}, {}
    for r in records:
        gate = (r.get("ground_truth") or {}).get("gate") or {"state": r["stages"].get("ground_truth", "BLOCKED"),
                                                              "reason": r.get("blocked") or r.get("error") or "not run"}
        gt_states[gate["state"]] = gt_states.get(gate["state"], 0) + 1
        key = f'{gate["state"]}: ' + re.sub(r"\d+(\.\d+)?", "N", gate["reason"])   # reasons grouped, numbers elided
        gt_reasons[key] = gt_reasons.get(key, 0) + 1
    agg["mapping_coverage"] = round(agg["exported_pins_mapped"] / agg["gt_scoreable_pins"], 4) \
        if agg["gt_scoreable_pins"] else None
    summary = gate_table(records, cli, {"all_planned_pages_evaluated": PASS if records and not errors else FAIL})
    summary.update({"kicad_version": version, "pages_planned": len(tasks), "pages_skipped_by_plan": len(skipped),
                    "pages_with_error": errors, "pairs": tot, "aggregate": agg,
                    "precision": round(tot["TP"] / (tot["TP"] + tot["FP"]), 4) if tot["TP"] + tot["FP"] else None,
                    "recall_over_mapped_pairs": round(tot["TP"] / (tot["TP"] + tot["FN"]), 4) if tot["TP"] + tot["FN"] else None,
                    "gt_policy": policy, "gt_gate_states": dict(sorted(gt_states.items())),
                    "gt_gate_reasons": dict(sorted(gt_reasons.items())),
                    "seconds": round(time.time() - t0, 1), "partial_selection": os.environ.get("GT_ONLY", ""),
                    "pages": records})
    if summary["partial_selection"]:
        summary["overall"] = "PARTIAL"
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    print("M4_GT_SUMMARY " + json.dumps({k: summary[k] for k in ("kicad_version", "gates", "overall", "pages_planned",
                                                                 "pages_skipped_by_plan", "pages_with_error", "pairs",
                                                                 "aggregate", "precision", "recall_over_mapped_pairs",
                                                                 "gt_policy", "gt_gate_states", "gt_gate_reasons",
                                                                 "seconds", "partial_selection")}))
    return 0 if summary["overall"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "gt_out"))
