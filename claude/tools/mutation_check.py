#!/usr/bin/env python3
"""Mutation check: each mutation deliberately breaks the code; the test suite MUST then fail.

Every run is classified from pytest's exit status AND its JUnit report, never from "exit code != 0":
  KILLED            tests were collected and at least one test failed in its own body (assertion or the exception
                    the mutated code raised); not an import / syntax error
  SURVIVED          tests were collected and all passed: the tests cannot detect that class of bug
  INVALID           the mutation did not apply (pattern found 0 or >1 times, or it changes nothing)
  COLLECTION_ERROR  the mutated source does not compile, pytest could not collect / import, or 0 tests collected
  HARNESS_ERROR     the runner or pytest itself failed (internal / usage error, no or unreadable report, a setup
                    error only, a deselected harness self-test that ran anyway, an exception in the runner)
  TIMEOUT           the suite exceeded SUITE_TIMEOUT
Only KILLED is a success: any other outcome makes the script exit 1. The UNMUTATED suite must pass cleanly in the
same kind of copy first, otherwise no mutation is run. The original sources are never modified (mutations are
applied to a temporary copy of the package).
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
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
     "        if nums != {num0}:\n            agree = False", "        if False:\n            agree = False"),
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
     "_gap(ba, bb) <= gf * min(_maxdim(ba), _maxdim(bb))", "_gap(ba, bb) <= gf * max(_maxdim(ba), _maxdim(bb))"),
    ("M28-merge-disabled", "ecci/symbols.py",
     "    if gap_frac >= 0:\n        for _ in range(16):", "    if False:\n        for _ in range(16):"),
    ("M29-text-counted-in-wire-population", "ecci/classify.py",
     "and p.prov.source == SRC_VECTOR and p.id not in text_ids]", "and p.prov.source == SRC_VECTOR]"),
    # ---- M3
    ("M30-tr1-precondition-dropped", "ecci/status.py",
     'if pre == "eq2" and e.n_prims != 2:', 'if False:'),
    ("M31-negative-evidence-ignored", "ecci/status.py",
     "    if not isinstance(e.negative, tuple) or e.negative:\n        return UNKNOWN", "    if False:\n        return UNKNOWN"),
    ("M32-weak-source-ignored", "ecci/status.py",
     "if any(r.weak is not False for r in e.roles):", "if False:"),
    ("M33-missing-coord-ignored", "ecci/status.py",
     "    if not (isinstance(e.coord, str) and e.coord.strip()):\n        return INFERRED", "    if False:\n        return INFERRED"),
    ("M34-single-evidence-confirmed", "ecci/status.py",
     "if len(set(r.evidence)) < 2:\n            return ROLE_CANDIDATE", "if False:\n            return ROLE_CANDIDATE"),
    ("M35-conventional-rule-verified", "ecci/status.py",
     'return INFERRED, "CONVENTIONAL_RULE"', 'return VERIFIED, "CONVENTIONAL_RULE"'),
    ("M36-block-split-wrong", "ecci/constraints.py",
     "if low[u] >= disc[p]:", "if low[u] > disc[p]:"),
    ("M37-c1-disabled", "ecci/constraints.py",
     "if len(by_name) > 1:", "if len(by_name) > 99:"),
    ("M38-c5-disabled", "ecci/constraints.py",
     "    if len(members) > 1:\n        for p in sorted(cons.no_connect):", "    if len(members) > 99:\n        for p in sorted(cons.no_connect):"),
    ("M39-ocr-names-conflict", "ecci/constraints.py",
     "if nm is not None and nm.confirmed:\n            by_name", "if nm is not None:\n            by_name"),
    ("M40-quarantine-not-excluded", "ecci/solver.py",
 