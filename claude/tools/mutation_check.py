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
    # ---- M2
    ("M14-no-template-sampling", "ecci/template.py", "COVER_SAMPLES = 5", "COVER_SAMPLES = 0"),
    ("M15-no-cluster-midpoint-check", "ecci/template.py",
     "                r3 = max(r3, min(point_segment_distance(q, a, b) for a, b in segs_t))", "                pass"),
    ("M16-s2-lineage-includes-body", "ecci/pins.py",
     "frozenset({f\"geom:{pid}\"}))\n                            _add(reg, s2)",
     "frozenset({f\"geom:{pid}\"} | {f\"geom:{i}\" for i in cl.prim_ids}))\n                            _add(reg, s2)"),
    ("M17-ambiguity-ignored", "ecci/template.py",
     "if not all(_same_set(first, s, tau) for s in sets[1:]):", "if False:"),
    ("M18-id-agreement-ignored", "ecci/template.py",
     "        if nums != {num0}:\n            agree = False", "        pass"),
    ("M19-style-difference-ignored", "ecci/classify.py",
     "differs = mode is not None and sk != mode", "differs = False"),
    ("M20-unexpected-contact-ignored", "ecci/pins.py",
     "cr.conflicts.append(PinConflict(cl.id, \"UNEXPECTED_CONTACT\", end, pid))", "pass"),
    ("M21-border-threshold-removed", "ecci/classify.py",
     "bw >= params.border_frac * W and bh >= params.border_frac * H", "bw >= 0 and bh >= 0"),
    ("M22-overlap-merge-disabled", "ecci/symbols.py",
     "                    if find(a) != find(b) and _gap(ba, bb)", "                    if False and find(a) != find(b) and _gap(ba, bb)"),
    # ---- M2.1
    ("M23-text-needs-no-row-validity", "ecci/classify.py",
     "        if _row_valid(ms, boxes, params):", "        if True:"),
    ("M24-row-alignment-ignored", "ecci/classify.py",
     "if aligned and bx0 - ax1 <= params.row_gap_frac * h:", "if bx0 - ax1 <= params.row_gap_frac * h:"),
    ("M25-pair-tolerance-huge", "ecci/classify.py",
     "pair_tol: float = 0.15", "pair_tol: float = 50.0"),
    ("M26-glyph-size-limit-removed", "ecci/classify.py",
     "> params.glyph_max_diag_frac * diag_page:", "> 1e9 * diag_page:"),
    ("M27-merge-uses-larger-part", "ecci/symbols.py",
     "_gap(ba, bb) <= gap_frac * min(_maxdim(ba), _maxdim(bb))", "_gap(ba, bb) <= gap_frac * max(_maxdim(ba), _maxdim(bb))"),
    ("M28-merge-disabled", "ecci/symbols.py",
     "    if gap_frac >= 0:\n        for _ in range(16):", "    if False:\n        for _ in range(16):"),
    ("M29-text-counted-in-wire-population", "ecci/classify.py",
     "and p.prov.source == SRC_VECTOR and p.id not in text_ids]", "and p.prov.source == SRC_VECTOR]"),
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
