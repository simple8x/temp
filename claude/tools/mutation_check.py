#!/usr/bin/env python3
"""Mutation check: each mutation deliberately breaks the code; the test suite MUST then fail.

A mutation that survives (suite still passes) is reported as SURVIVED and the script exits 1:
it means the tests cannot detect that class of bug. The original sources are never modified
(mutations are applied to a temporary copy of the package).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (id, file, old, new)
MUTATIONS = [
    ("M01-no-crossing-split", "ecci/arrangement.py",
     "if h0 * h1 < 0.0 and g0 * g1 < 0.0:", "if False:"),
    ("M02-ignore-a-endpoints-on-b", "ecci/arrangement.py",
     "    handle(a.p, a.rp, b, ev_b)\n    handle(a.q, a.rq, b, ev_b)\n", ""),
    ("M03-wrong-rotation-90", "ecci/transform.py",
     "rotm = (0.0, 1.0, -1.0, 0.0, h, 0.0)", "rotm = (0.0, -1.0, 1.0, 0.0, 0.0, w)"),
    ("M04-flatten-tolerance-x100", "ecci/flatten.py",
     "<= tol\n        if flat:", "<= tol * 100\n        if flat:"),
    ("M05-cm-order-swapped", "ecci/ingest_vector.py",
     "gs.ctm = mat_mul((a, b, c, d, e, f), gs.ctm)", "gs.ctm = mat_mul(gs.ctm, (a, b, c, d, e, f))"),
    ("M06-no-y-flip", "ecci/transform.py",
     "flip: Matrix = (1.0, 0.0, 0.0, -1.0, -x0, y1)", "flip: Matrix = (1.0, 0.0, 0.0, 1.0, -x0, -y0)"),
    ("M07-circular-evidence-allowed", "ecci/evidence.py",
     "elif reg.depends_on_connectivity(i):", "elif False:"),
    ("M08-weak-sources-allowed", "ecci/evidence.py",
     "elif it.source in WEAK_SOURCES:", "elif False:"),
    ("M09-shared-ancestor-ignored", "ecci/evidence.py",
     "if not ca.isdisjoint(cb):", "if a in cb or b in ca:"),
    ("M10-same-path-needs-no-vertex", "ecci/arrangement.py",
     "if sv is not None and (r1.prim_id, sv) in vert_set:", "if sv is not None:"),
    ("M11-close-path-not-closed", "ecci/ingest_vector.py",
     "if op in (\"s\", \"b\", \"b*\"):\n                        for s in subs:\n                            s.closed = True",
     "if False:\n                        for s in subs:\n                            s.closed = True"),
    ("M12-q-does-not-restore", "ecci/ingest_vector.py",
     "                        gs = stack.pop()", "                        stack.pop()"),
    ("M13-provenance-op-off-by-one", "ecci/ingest_vector.py",
     "chain = prefix + ((label, paint_idx),)", "chain = prefix + ((label, paint_idx + 1),)"),
]


def run_suite(pkg_root: Path) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"],
                       cwd=pkg_root, capture_output=True, text=True, timeout=900)
    return p.returncode, (p.stdout + p.stderr)[-400:]


def main() -> int:
    results = []
    for mid, rel, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "claude"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".hypothesis", "out", "*.egg-info"))
            target = work / rel
            src = target.read_text()
            if src.count(old) != 1:
                results.append({"id": mid, "status": "INVALID", "detail": f"pattern occurs {src.count(old)}x"})
                continue
            target.write_text(src.replace(old, new))
            code, tail = run_suite(work)
            results.append({"id": mid, "status": "KILLED" if code != 0 else "SURVIVED",
                            "detail": tail.strip().splitlines()[-1] if tail.strip() else ""})
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "mutation_report.json").write_text(json.dumps(results, indent=2))
    for r in results:
        print(f"{r['status']:9s} {r['id']:36s} {r['detail']}")
    bad = [r for r in results if r["status"] != "KILLED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} killed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
