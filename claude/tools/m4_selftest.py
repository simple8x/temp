#!/usr/bin/env python3
"""M4.2 self-test: generated scenes (tests/m4_scenes.py) -> documents -> .kicad_sch -> real kicad-cli -> checks.

Ground truth = the connectivity each scene's drawing means (written from its construction, not from ECCI).
Writes out/m4/selftest/<scene>/* and out/m4/selftest/summary.json; prints M4_SELFTEST_SUMMARY. Exit 0 only if
every gate is PASS; a missing kicad-cli makes the KiCad gates BLOCKED (exit 2), never PASS.
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
        if not r or any(d["decision"] == "PLACED" for d in r) or not any("transform AMBIGUOUS" in x for d in r for x in d["reasons"]):
            P.append("ambiguous_r: the symmetric R must be NOT_PLACED for its AMBIGUOUS transform")
        if nets:
            P.append("ambiguous_r: no net may be exported (one exportable pin only)")
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
    records, fc = [], []
    for sc in all_scenes():
        pi, cls, cl, built, res = sc.run()
        try:
            rec = run_page(out / sc.name, pi, cls, cl, built, res, LIB, lib_text, {"name": f"{sc.name}.pdf"},
                           sc.gt_pins(), cli)
        except Exception as e:  # noqa: BLE001  a crash is a FAIL of that scene, with its reason
            rec = {"source": sc.name, "stages": {"connectivity_schema_roundtrip": FAIL}, "error": f"{type(e).__name__}: {e}"}
        rec["scene"] = sc.name
        if (out / sc.name / "manifest.json").exists():
            fc += failclosed(sc.name, out / sc.name / "manifest.json")
        else:
            fc.append(f"{sc.name}: no manifest")
        records.append(rec)
    summary = gate_table(records, cli, {"ambiguity_unknown_failclosed": PASS if not fc else FAIL})
    summary.update({"kicad_version": version, "failclosed_problems": fc, "scenes": records})
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    for r in records:
        print(f"SCENE {r['scene']:12s} " + " ".join(f"{k}={v}" for k, v in sorted(r["stages"].items())),
              r.get("ground_truth", {}).get("pairs", ""), r.get("error", ""), r.get("roundtrip_problems", "") or "")
    print("M4_SELFTEST_SUMMARY " + json.dumps({"kicad_version": version, "gates": summary["gates"],
                                               "overall": summary["overall"], "failclosed_problems": fc}))
    if summary["overall"] == PASS:
        return 0
    return 2 if BLOCKED in summary["gates"].values() and FAIL not in summary["gates"].values() else 1


if __name__ == "__main__":
    sys.exit(main())
