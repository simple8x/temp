#!/usr/bin/env python3
"""M4.2 self-test: generated scenes (tests/m4_scenes.py) -> documents -> .kicad_sch -> real kicad-cli -> checks.

Ground truth = the connectivity each scene's drawing means (written from its construction, not from ECCI).
Writes out/m4/selftest/<scene>/* and out/m4/selftest/summary.json; prints M4_SELFTEST_SUMMARY. Exit 0 only if
every positive scene passes and each explicitly declared non-PASS control remains non-PASS as expected; a missing kicad-cli makes the KiCad gates BLOCKED (exit 2), never PASS. A negative-control GT FAIL is preserved in the scene record and is not an electrical PASS.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "tests" / "unit")]

from ecci import kicad_roundtrip as kr  # noqa: E402
from ecci.m4_pipeline import BLOCKED, FAIL, PASS, env_cli, gate_table, run_page  # noqa: E402
from m4_scenes import all_scenes  # noqa: E402
from test_rules import LIB  # noqa: E402


def failclosed(name: str, man_path: Path) -> list[str]:
    man = json.loads(man_path.read_text())
    placed = [d for d in man["symbols"] if d["decision"] == "PLACED"]
    nets = man["nets"]
    P = []
    if name.startswith("chain_") and (len(placed) != 2 or len(nets) != 1):
        P.append(f"{name}: expected 2 placed symbols and 1 net, got {len(placed)} / {len(nets)}")
    if name == "ambiguous_r":
        r = [d for d in man["symbols"] if any(c["symbol"] == "R" for c in d["candidates"])]
        if len(r) != 1 or r[0]["decision"] != "PLACED":
            P.append("ambiguous_r: electrically equivalent resistor pins should be placed with a canonical mapping")
        elif any(p["number_assignment"] != "EQUIVALENT_CANONICAL" for p in r[0]["pins"]):
            P.append("ambiguous_r: source pin-number ambiguity must remain explicit in the export manifest")
        if len(nets) != 1 or len(nets[0]["pins"]) != 2:
            P.append("ambiguous_r: the independently constructed wire must connect exactly the R and D terminals")
    if name == "crossing":
        if len(nets) != 2 or any(len({p[0] for p in n["pins"]} & {"ECCI0", "ECCI1"}) and len(n["pins"]) > 2 for n in nets):
            P.append("crossing: the two crossing wires must stay two nets of two pins each")
        if any(len(n["pins"]) != 2 for n in nets):
            P.append("crossing: a net has other than 2 pins (crossing merged?)")
    if name == "t_nodot":
        if not man["not_exported"]["edges"]["INFERRED"]:
            P.append("t_nodot: the T-junction must be listed as INFERRED, not exported")
        if any(len(n["pins"]) > 2 for n in nets):
            P.append("t_nodot: the INFERRED T-junction was exported into a net")
    if name == "t_dot" and not any(len(n["pins"]) == 3 for n in nets):
        P.append("t_dot: the dot-confirmed T must give one 3-pin net")
    return P


def main() -> int:
    out = ROOT / "out" / "m4" / "selftest"
    out.mkdir(parents=True, exist_ok=True)
    cli = env_cli()
    version = kr.kicad_version(cli) if cli else None
    lib_text = open(LIB.path, encoding="utf-8").read()
    records, fc, expected_nonpass_controls = [], [], []
    for sc in all_scenes():
        pi, cls, cl, built, res = sc.run()
        try:
            rec = run_page(out / sc.name, pi, cls, cl, built, res, LIB, lib_text, {"name": f"{sc.name}.pdf"},
                           sc.gt_pins(), cli, gt_policy=sc.gt_policy())
        except Exception as e:  # noqa: BLE001  a crash is a FAIL of that scene, with its reason
            rec = {"source": sc.name, "stages": {"connectivity_schema_roundtrip": FAIL}, "error": f"{type(e).__name__}: {e}"}
        rec["scene"] = sc.name
        if (out / sc.name / "manifest.json").exists():
            fc += failclosed(sc.name, out / sc.name / "manifest.json")
        else:
            fc.append(f"{sc.name}: no manifest")
        records.append(rec)
        if sc.name in ("t_nodot", "ambiguous_r"):
            # These are explicit non-PASS controls. t_nodot must expose a real GT false
            # negative because the no-dot T-touch remains INFERRED; ambiguous_r may export
            # synthetic canonical pins but their source pin identity must remain UNKNOWN.
            gt = rec.get("ground_truth", {})
            pairs = gt.get("pairs", {})
            gate = gt.get("gate", {})
            man = json.loads((out / sc.name / "manifest.json").read_text()) if (out / sc.name / "manifest.json").exists() else {}
            inferred = bool(man.get("not_exported", {}).get("edges", {}).get("INFERRED"))
            if sc.name == "t_nodot":
                expected = (rec.get("stages", {}).get("ground_truth") == FAIL and gate.get("state") == FAIL
                            and pairs.get("FP") == 0 and pairs.get("FN", 0) > 0 and inferred)
            else:
                expected = (rec.get("stages", {}).get("ground_truth") == "INCONCLUSIVE"
                            and bool(gt.get("pin_identity_unverified_synthetic_canonical"))
                            and gt.get("exported_pins_mapped", 0) < gt.get("gt_scoreable_pins", 0))
            expected = expected and all(v == PASS for k, v in rec.get("stages", {}).items() if k != "ground_truth")
            if expected:
                expected_nonpass_controls.append({"scene": sc.name, "expected_state": gate.get("state"),
                                                  "reason": gate.get("reason"), "pairs": pairs,
                                                  "synthetic_identity_unverified": bool(gt.get("pin_identity_unverified_synthetic_canonical")),
                                                  "t_junction_inferred_not_exported": inferred})
            else:
                fc.append(f"{sc.name}: expected non-PASS control did not match its fail-closed contract")
    expected_control_names = {x["scene"] for x in expected_nonpass_controls}
    gate_records = []
    for rec in records:
        if rec.get("scene") in expected_control_names:
            normalized = dict(rec)
            normalized["stages"] = dict(rec["stages"])
            normalized["stages"]["ground_truth"] = PASS  # test passed: the expected negative control was detected
            gate_records.append(normalized)
        else:
            gate_records.append(rec)
    summary = gate_table(gate_records, cli, {"ambiguity_unknown_failclosed": PASS if not fc else FAIL,
                                             "expected_nonpass_controls": PASS if len(expected_nonpass_controls) == 2 else FAIL})
    if summary["overall"] == PASS and expected_nonpass_controls:
        summary["overall"] = "PASS_WITH_EXPECTED_NONPASS_CONTROLS"
    summary.update({"kicad_version": version, "failclosed_problems": fc,
                    "expected_nonpass_controls": expected_nonpass_controls, "scenes": records})
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    for r in records:
        print(f"SCENE {r['scene']:12s} " + " ".join(f"{k}={v}" for k, v in sorted(r["stages"].items())),
              r.get("ground_truth", {}).get("pairs", ""), r.get("error", ""), r.get("roundtrip_problems", "") or "")
    print("M4_SELFTEST_SUMMARY " + json.dumps({"kicad_version": version, "gates": summary["gates"],
                                               "overall": summary["overall"], "failclosed_problems": fc}))
    if summary["overall"] in (PASS, "PASS_WITH_EXPECTED_NONPASS_CONTROLS"):
        return 0
    return 2 if BLOCKED in summary["gates"].values() and FAIL not in summary["gates"].values() else 1


if __name__ == "__main__":
    sys.exit(main())