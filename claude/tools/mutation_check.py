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
     "_gap(ba, bb) <= gf * min(_maxdim(ba), _maxdim(bb))", "_gap(ba, bb) <= gf * max(_maxdim(ba), _maxdim(bb))"),
    ("M28-merge-disabled", "ecci/symbols.py",
     "    if gap_frac >= 0:\n        for _ in range(16):", "    if False:\n        for _ in range(16):"),
    ("M29-text-counted-in-wire-population", "ecci/classify.py",
     "and p.prov.source == SRC_VECTOR and p.id not in text_ids]", "and p.prov.source == SRC_VECTOR]"),
    # ---- M3
    ("M30-tr1-precondition-dropped", "ecci/status.py",
     'if pre == "eq2" and e.n_prims != 2:', 'if False:'),
    ("M31-negative-evidence-ignored", "ecci/status.py",
     "    if e.negative:\n        return UNKNOWN", "    if False:\n        return UNKNOWN"),
    ("M32-weak-source-ignored", "ecci/status.py",
     "if any(r.weak for r in e.roles):", "if False:"),
    ("M33-missing-coord-ignored", "ecci/status.py",
     "    if not e.coord:\n        return INFERRED", "    if False:\n        return INFERRED"),
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
     "V_ok = [e for e in V if e[1] not in Q]", "V_ok = list(V)"),
    ("M41-site-rollback-disabled", "ecci/solver.py",
     "bad_links = bad & set(cur_L)", "bad_links = set()"),
    ("M42-ambiguous-site-committed", "ecci/solver.py",
     "elif len(consistent) == 1:", "elif len(consistent) >= 1:"),
    ("M43-site-commit-verified", "ecci/solver.py",
     "site_res[sid] = SiteResult(INFERRED, name,", "site_res[sid] = SiteResult(VERIFIED, name,"),
    ("M44-cluster-limit-removed", "ecci/solver.py",
     "if len(cl) > k_max:", "if len(cl) > 10**6:"),
    ("M45-ocr-names-link", "ecci/solver.py",
     "if nm.confirmed and o in kind:", "if o in kind:"),
    ("M46-closure-always-closed", "ecci/solver.py",
     '"OPEN" if reasons else "CLOSED"', '"CLOSED"'),
    ("M47-pair-always-verified", "ecci/solver.py",
     "return VERIFIED if self._vgroup.get(p) == self._vgroup.get(q) and p in self._vgroup else INFERRED",
     "return VERIFIED"),
    ("M48-touching-quarantine-allowed", "ecci/solver.py",
     "        if a in Q or b in Q:\n            status[eid] = (UNKNOWN, \"TOUCHES_CONFLICT_COMPONENT\")",
     "        if False:\n            status[eid] = (UNKNOWN, \"TOUCHES_CONFLICT_COMPONENT\")"),
    ("M49-witness-picks-one-edge", "ecci/solver.py",
     "            bad |= w\n", "            bad |= {min(w)}\n"),
    ("M51-sites-not-created", "ecci/rules.py",
     "elif arms >= 4:", "elif arms >= 99:"),
    ("M52-dot-arms-evidence-always", "ecci/rules.py",
     "if arms >= 3 and all(wrole[i].status == ROLE_CONFIRMED for i in arm_prims):", "if True:"),
    ("M53-dot-aspect-ignored", "ecci/rules.py",
     "if min(w, h) / max(w, h) < prm.dot_aspect:", "if False:"),
    ("M54-dot-roundness-ignored", "ecci/rules.py",
     "or (max(rs) - min(rs)) / rm > prm.dot_roundness:", "or False:"),
    ("M55-dot-touching-body-ignored", "ecci/rules.py",
     "        if len(ids) > 1:\n            touched |= set(ids)", "        if False:\n            touched |= set(ids)"),
    ("M56-gap-blocker-ignored", "ecci/rules.py",
     "if any(q not in (p1, p2) and _proper_cross(x1, x2, a, b, 0.0) is not None", "if False and any(q not in (p1, p2) and _proper_cross(x1, x2, a, b, 0.0) is not None"),
    ("M57-gap-gmax-ignored", "ecci/rules.py",
     "not (prm.eps < gap <= prm.g_max)", "not (prm.eps < gap)"),
    ("M58-c4-against-unresolved", "ecci/rules.py",
     "related = pin_objs_of.get(cid, [])", "related = pin_objs_of.get(cid, []) + ut_objs_of.get(cid, [])"),
    ("M59-two-wires-at-tip-tr3", "ecci/rules.py",
     'if len(near) == 1 and _is_end(by_id[near[0]], pin.tip, tol)', 'if len(near) >= 1'),
    ("M71-ambiguous-style-guessed", "ecci/classify.py",
     "no_wire = mode == AMBIGUOUS_STYLE", "no_wire = False"),
    ("M72-no-length-tie-break", "ecci/classify.py",
     "if len(cands) >= 2 and mean_len[cands[0]] >= params.style_len_dominance * mean_len[cands[1]]:",
     "if False:"),
    ("M73-off-colour-always-excluded", "ecci/rules.py",
     "    if not good:\n        return frozenset()", "    if False:\n        return frozenset()"),
    ("M74-tiny-styles-kept", "ecci/classify.py",
     "big = {k: v for k, v in big.items() if tot[k] >= params.style_min_len_share * top}",
     "big = dict(big)"),
    ("M75-no-stub-strip-retry", "ecci/pins.py",
     "if syms and not hits:", "if False:"),
    ("M60-stub-evidence-prefix", "ecci/rules.py",
     'if ev.startswith("S2:") and ev in reg.items:\n                    for tok', 'if ev.startswith("S9:") and ev in reg.items:\n                    for tok'),
    ("M61-style-length-share-off", "ecci/classify.py",
     "big = {k: v for k, v in big.items() if tot[k] >= params.style_min_len_share * top}",
     "big = big"),
    ("M62-body-colour-filter-off", "ecci/classify.py",
     "clean = {k: v for k, v in big.items() if body_colours.get(k[1], 0) < params.style_body_marks}",
     "clean = dict(big)"),
    ("M63-ambiguous-style-guesses-wires", "ecci/classify.py",
     "return AMBIGUOUS_STYLE, len(groups[best_k]) / len(pop)", "return None, len(groups[best_k]) / len(pop)"),
    ("M64-length-tiebreak-picks-shortest", "ecci/classify.py",
     "cands.sort(key=lambda k: (-mean_len[k], str(k)))", "cands.sort(key=lambda k: (mean_len[k], str(k)))"),
    ("M65-sheet-furniture-allowed", "ecci/classify.py",
     "/ len(v) <= max_len} or big", "/ len(v) <= 1e9} or big"),
    ("M66-stub-axis-check-off", "ecci/symbols.py",
     "        if min(abs(a[0] - b[0]), abs(a[1] - b[1])) > axis_tol:\n            continue\n", ""),
    ("M67-stub-contact-ignores-stroke-width", "ecci/symbols.py",
     "point_segment_distance(end, s0, s1) <= eps + 0.5 * (p.stroke_w + q.stroke_w)\n                     for q in near",
     "point_segment_distance(end, s0, s1) <= eps\n                     for q in near"),
    ("M68-tiny-parts-merge-by-proximity", "ecci/symbols.py",
     "never merge by proximity: only by touching.\n    # Without it, text strokes chain into one page-sized \"cluster\" that matches nothing.\n    min_part = min_gap_part_frac",
     "never merge by proximity: only by touching.\n    # Without it, text strokes chain into one page-sized \"cluster\" that matches nothing.\n    min_part = 0.0 * min_gap_part_frac"),
    ("M69-lone-glyph-needs-no-strokes", "ecci/classify.py",
     "len(short) >= params.glyph_stroke_share * len(ps)", "False"),
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
