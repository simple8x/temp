"""M4.0 regression gate, read back from gt_eval.log. Thresholds are pinned in gt_gate.txt.

PASS only if the run is a FULL run with inventory_consistent true, FP_V = FP_I = 0 in both the detected and the
oracle mode, detected_local.pin_recall >= the pinned minimum, and the pinned page / project counts. Anything
missing (no GT_TOTAL, no field, a non-numeric value) is a FAIL, never a PASS.
usage: gt_gate.py gt_eval.log [gt_gate.txt]
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REQUIRED = ("GATE_MAX_FP_V", "GATE_MAX_FP_I", "GATE_MIN_PIN_RECALL", "GATE_MIN_PAGES", "GATE_PROJECTS")


def load_gate(path) -> dict:
    g = {}
    for line in pathlib.Path(path).read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        g[k.strip()] = float(v.strip())
    missing = [k for k in REQUIRED if k not in g]
    if missing:
        raise ValueError(f"gate pins missing: {missing}")
    return g


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def check(log_text: str, g: dict) -> list[str]:
    tot = cov = None
    for line in log_text.splitlines():
        if line.startswith("GT_TOTAL "):
            tot = json.loads(line[9:])
        elif line.startswith("GT_COVERAGE "):
            cov = json.loads(line[12:])
    problems: list[str] = []

    def fail(msg):
        problems.append(msg)

    if tot is None or cov is None:
        fail("no GT_TOTAL / GT_COVERAGE line: the evaluation did not finish")
        return problems
    if cov.get("full_run") is not True:
        fail("partial run (GT_ONLY): the M4.0 gate needs the full corpus")
    if tot.get("inventory_consistent") is not True:
        fail(f"inventory_consistent={tot.get('inventory_consistent')}")
    if cov.get("problems"):
        fail(f"coverage problems: {cov.get('problems')}")
    ev = _num(cov.get("evaluated"))
    if ev is None or ev < g["GATE_MIN_PAGES"]:
        fail(f"evaluated={cov.get('evaluated')} < {g['GATE_MIN_PAGES']}")
    pj = _num(cov.get("projects"))
    if pj is None or pj != g["GATE_PROJECTS"]:
        fail(f"projects={cov.get('projects')} != {g['GATE_PROJECTS']}")
    for mode in ("detected", "oracle"):
        m = tot.get(mode) or {}
        for k, lim in (("FP_V", g["GATE_MAX_FP_V"]), ("FP_I", g["GATE_MAX_FP_I"])):
            v = _num(m.get(k))
            if v is None or v > lim:
                fail(f"{mode}.{k}={v} > {lim}")
    pr = _num((tot.get("detected_local") or {}).get("pin_recall"))
    if pr is None or pr < g["GATE_MIN_PIN_RECALL"]:
        fail(f"detected_local.pin_recall={pr} < {g['GATE_MIN_PIN_RECALL']}")
    return problems


def main(argv) -> int:
    g = load_gate(argv[2] if len(argv) > 2 else HERE / "gt_gate.txt")
    problems = check(pathlib.Path(argv[1]).read_text(errors="replace"), g)
    for p in problems:
        print("M4_GATE_FAIL " + p)
    print("M4_GATE " + ("FAIL" if problems else "PASS") + " " + json.dumps(g, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
