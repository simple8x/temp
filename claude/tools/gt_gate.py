"""M4.0 regression gate, read back from gt_eval.log. Thresholds are pinned in gt_gate.txt.

PASS only if the run is a FULL run with inventory_consistent true, FP_V = FP_I = 0 in both the detected and the
oracle mode, detected_local.pin_recall >= the pinned minimum, and the pinned page / project counts. Anything
missing (no GT_TOTAL, no field, a non-numeric value) is a FAIL, never a PASS.
usage: gt_gate.py gt_eval.log [gt_gate.txt]
"""
import json
import math
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
        k = k.strip()
        if k in g:
            raise ValueError(f"gate pin {k} given twice")
        g[k] = float(v.strip())
        if not math.isfinite(g[k]):
            raise ValueError(f"gate pin {k}={v.strip()} is not finite")
    missing = [k for k in REQUIRED if k not in g]
    if missing:
        raise ValueError(f"gate pins missing: {missing}")
    return g


def _num(x):
    """A finite real number, or None. bool, str, None, NaN and +/-Infinity are never numbers here."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return x if math.isfinite(x) else None


def _count(x):
    """A non-negative integer count (int, or a float with an integral value), or None."""
    v = _num(x)
    return v if v is not None and v >= 0 and float(v).is_integer() else None


def _reject_constant(c):
    raise ValueError(f"non-finite JSON constant {c}")


MISSING = object()


def check(log_text: str, g: dict) -> list[str]:
    problems: list[str] = []

    def fail(msg):
        problems.append(msg)

    recs: dict[str, list] = {"GT_TOTAL": [], "GT_COVERAGE": []}
    for line in log_text.splitlines():
        for tag in recs:
            if line.startswith(tag + " "):
                body = line[len(tag) + 1:]
                try:
                    recs[tag].append(json.loads(body, parse_constant=_reject_constant))
                except ValueError as x:          # JSONDecodeError is a ValueError
                    fail(f"malformed {tag} record: {x}")
                    recs[tag].append(MISSING)
    for tag, rs in recs.items():
        if len(rs) > 1:
            same = all(r == rs[0] for r in rs[1:])
            fail(f"{len(rs)} {tag} records ({'duplicate' if same else 'conflicting'}): exactly one is required")
    tot = recs["GT_TOTAL"][0] if len(recs["GT_TOTAL"]) == 1 else None
    cov = recs["GT_COVERAGE"][0] if len(recs["GT_COVERAGE"]) == 1 else None
    if not recs["GT_TOTAL"] or not recs["GT_COVERAGE"]:
        fail("no GT_TOTAL / GT_COVERAGE line: the evaluation did not finish")
    if not isinstance(tot, dict) or not isinstance(cov, dict):
        if (tot is not None and tot is not MISSING) or (cov is not None and cov is not MISSING):
            fail("GT_TOTAL / GT_COVERAGE record is not a JSON object")
        return problems or ["no usable GT_TOTAL / GT_COVERAGE record"]

    def get(rec, path):
        cur = rec
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                return MISSING
            cur = cur[k]
        return cur

    def field(rec, name, *path):
        v = get(rec, path)
        if v is MISSING:
            fail(f"missing field {name}.{'.'.join(path)}")
        return v

    full = field(cov, "GT_COVERAGE", "full_run")
    if full is not MISSING and full is not True:
        fail("partial run (GT_ONLY): the M4.0 gate needs the full corpus")
    inv = field(tot, "GT_TOTAL", "inventory_consistent")
    if inv is not MISSING and inv is not True:
        fail(f"inventory_consistent={inv!r}")
    cp = field(cov, "GT_COVERAGE", "problems")
    if cp is not MISSING:
        if not isinstance(cp, list):
            fail(f"coverage problems is {type(cp).__name__} {cp!r}, not a list")
        elif cp:
            fail(f"coverage problems: {cp}")
        if inv is True and cp != []:
            fail("contradictory records: inventory_consistent=true but coverage problems are not empty")
    raw = field(cov, "GT_COVERAGE", "evaluated")
    if raw is not MISSING:
        ev = _count(raw)
        if ev is None or ev < g["GATE_MIN_PAGES"]:
            fail(f"evaluated={raw!r} < {g['GATE_MIN_PAGES']}")
    raw = field(cov, "GT_COVERAGE", "projects")
    if raw is not MISSING:
        pj = _count(raw)
        if pj is None or pj != g["GATE_PROJECTS"]:
            fail(f"projects={raw!r} != {g['GATE_PROJECTS']}")
    for mode in ("detected", "oracle"):
        for k, lim in (("FP_V", g["GATE_MAX_FP_V"]), ("FP_I", g["GATE_MAX_FP_I"])):
            raw = field(tot, "GT_TOTAL", mode, k)
            if raw is MISSING:
                fail(f"{mode}.{k}=None > {lim}")
                continue
            v = _count(raw)
            if v is None or v > lim:
                fail(f"{mode}.{k}={raw!r} > {lim}")
    raw = field(tot, "GT_TOTAL", "detected_local", "pin_recall")
    pr = None if raw is MISSING else _num(raw)
    if pr is not None and not 0 <= pr <= 1:
        fail(f"detected_local.pin_recall={raw!r} outside [0, 1]")
        pr = None
    if pr is None or pr < g["GATE_MIN_PIN_RECALL"]:
        fail(f"detected_local.pin_recall={None if raw is MISSING or _num(raw) is None else raw} < {g['GATE_MIN_PIN_RECALL']}")
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
