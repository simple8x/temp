"""M4.0 regression gate, read back from gt_eval.log. Thresholds are pinned in gt_gate.txt.

PASS only if the run is a FULL run with inventory_consistent true, FP_V = FP_I = 0 in both the detected and the
oracle mode, detected_local.pin_recall >= the pinned minimum, and the pinned page / project counts. Anything
missing (no GT_TOTAL, no field, a non-numeric value) is a FAIL, never a PASS.
usage: gt_gate.py gt_eval.log [gt_gate.txt]
"""
import json
import math
import pathlib
import re
from decimal import Decimal, InvalidOperation
import sys

HERE = pathlib.Path(__file__).resolve().parent
# M4.0 is FROZEN: the pin file must state exactly these keys with exactly these values. Anything else (a weakened,
# tightened, missing, duplicated, unknown or malformed pin) is a configuration error, never a different gate.
# Values are exact decimals and are validated with Decimal, never float: "0.54999999999999999" rounds to the same
# float as 0.55 but is a different (weaker) pin and must be rejected.
FROZEN_TEXT = {"GATE_MAX_FP_V": "0", "GATE_MAX_FP_I": "0", "GATE_MIN_PIN_RECALL": "0.55",
               "GATE_MIN_PAGES": "100", "GATE_PROJECTS": "32"}
FROZEN_DEC = {k: Decimal(v) for k, v in FROZEN_TEXT.items()}
FROZEN = {k: float(v) for k, v in FROZEN_TEXT.items()}     # what load_gate returns (always from these constants)
REQUIRED = tuple(FROZEN)
_KEY = re.compile(r"[A-Z][A-Z0-9_]*")
_LITERAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")     # plain decimal: no sign, exponent, nan, inf, bool, _ or spaces


def load_gate(path) -> dict:
    g: dict[str, float] = {}
    for n, line in enumerate(pathlib.Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.count("=") != 1:
            raise ValueError(f"line {n}: not KEY=VALUE: {line!r}")
        k, v = (x.strip() for x in line.split("="))
        if not _KEY.fullmatch(k):
            raise ValueError(f"line {n}: malformed key {k!r}")
        if k not in FROZEN:
            raise ValueError(f"line {n}: unexpected gate pin {k}")
        if k in g:
            raise ValueError(f"gate pin {k} given twice")
        if not _LITERAL.fullmatch(v):
            raise ValueError(f"line {n}: {k}={v!r} is not a plain decimal number")
        try:
            x = Decimal(v)
        except InvalidOperation:
            raise ValueError(f"line {n}: {k}={v!r} is not a decimal number") from None
        if not x.is_finite() or x != FROZEN_DEC[k]:
            raise ValueError(f"gate pin {k}={v} differs from the frozen M4.0 value {FROZEN_TEXT[k]}")
        g[k] = FROZEN[k]                  # the frozen constant itself, never a float parsed from the file
    missing = [k for k in REQUIRED if k not in g]
    if missing:
        raise ValueError(f"gate pins missing: {missing}")
    return g


class _Dec(Decimal):
    """A JSON number from the log, kept exact (no float rounding: 0.54999999999999999 stays below 0.55)."""
    def __repr__(self):
        return str(self)


def _dec(x):
    """Exact Decimal of an int / float / Decimal (a float by its shortest repr, i.e. the value that was written)."""
    if isinstance(x, Decimal):
        return x
    return _Dec(repr(x)) if isinstance(x, float) else _Dec(x)


def _num(x):
    """A finite real number as an exact Decimal, or None. bool, str, None, NaN and +/-Infinity are never numbers."""
    if isinstance(x, bool) or not isinstance(x, (int, float, Decimal)):
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    v = _dec(x)
    return v if v.is_finite() else None


def _count(x):
    """A non-negative integer count (an integral number), or None."""
    v = _num(x)
    return v if v is not None and v >= 0 and v == v.to_integral_value() else None


def _reject_constant(c):
    raise ValueError(f"non-finite JSON constant {c}")


def _no_dup_keys(pairs):
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ValueError(f"duplicate JSON keys {dup}")
    return dict(pairs)


MISSING = object()


def check(log_text: str, g: dict) -> list[str]:
    problems: list[str] = []
    g = {k: _dec(v) for k, v in g.items()}   # thresholds compared exactly, as Decimal

    def fail(msg):
        problems.append(msg)

    recs: dict[str, list] = {"GT_TOTAL": [], "GT_COVERAGE": []}
    for line in log_text.splitlines():
        for tag in recs:
            if line.startswith(tag + " "):
                body = line[len(tag) + 1:]
                try:
                    recs[tag].append(json.loads(body, parse_constant=_reject_constant, parse_float=_Dec,
                                                  object_pairs_hook=_no_dup_keys))
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
