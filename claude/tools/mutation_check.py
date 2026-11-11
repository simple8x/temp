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
     "V_ok = [e for e in V + A if e[1] not in Q]", "V_ok = list(V + A)"),
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
    ("M76-no-bridge-stub", "ecci/symbols.py",
     "elif t0 and t1 and (dangling(i, touched[1], touched[0]) or dangling(i, touched[0], touched[1])):", "elif False:"),
    ("M77-bare-never-joins-containing-part", "ecci/symbols.py",
     "if (a in bare) != (b in bare) and _gap(ba, bb) > 0:", "if (a in bare) != (b in bare):"),
    ("M78-double-plotted-line-not-bare", "ecci/symbols.py",
     "and len({_geom_key(prims[i]) for i in ids}) == 1}", "and len(ids) == 1}"),
    ("M79-repeat-rule-off", "ecci/classify.py",
     "if cnt[sg] >= params.repeat_min_copies:", "if False:"),
    ("M80-repeat-threshold-1", "ecci/classify.py",
     "if cnt[sg] >= params.repeat_min_copies:", "if cnt[sg] >= 1:"),
    ("M81-closed-uses-open-limit", "ecci/classify.py",
     "(params.body_closed_max_frac if p.line_closed else params.body_max_frac)", "params.body_max_frac"),
    ("M82-open-uses-closed-limit", "ecci/classify.py",
     "(params.body_closed_max_frac if p.line_closed else params.body_max_frac)", "params.body_closed_max_frac"),
    ("M83-relaxations-colour-groups-off", "ecci/symbols.py",
     "    if len(groups) < 2:\n        variants_src = [(cl, ())]", "    if True:\n        variants_src = [(cl, ())]"),
    ("M85-decor-never-stripped", "ecci/symbols.py",
     "    if not dropped or not rest:\n        return None\n    return _rebuild(cl.id, rest, by_id), dropped",
     "    return None"),
    ("M86-decor-strips-everything", "ecci/symbols.py",
     "<= max_frac * big)", "<= 10.0 * big)"),
    ("M87-unit-selection-off", "ecci/pins.py",
     "if sel and len(sel) < len(res.hits):", "if False:"),
    ("M88-unit-selection-accepts-any-line", "ecci/pins.py",
     "                    if not (0.5 * plen <= ln <= 1.3 * plen):\n                        continue\n", ""),
    ("M89-unit-selection-keeps-s2-independent", "ecci/pins.py",
     "if plen > 0 and not stub_selected:", "if plen > 0:"),
    ("M90-stem-ignored", "ecci/pins.py",
     "len({_part_stem(h.symbol.name) for h in res.hits}) == 1:", "len({1 for h in res.hits}) == 1:"),
    ("M91-stem-keeps-library-nickname", "ecci/pins.py",
     'name.rsplit(":", 1)[-1])', "name)"),
    ("M93-attachment-promoted-to-verified", "ecci/solver.py",
     "    vrep = _components(objs, V)\n", "    vrep = _components(objs, V + A)\n"),
    ("M98-strip-stubs-counts-own-copy", "ecci/symbols.py",
     "            if j == i or gk[j] == gk[i]:", "            if j == i:"),
    ("M99-strip-stubs-grid-margin-ignores-stroke", "ecci/symbols.py",
     "    margin = eps + 0.5 * (wmax + wmax)", "    margin = eps"),
    ("M112-gt-eval-entrypoint-removed", "tools/gt_eval.py",
     'if __name__ == "__main__":\n    sys.exit(main(sys.argv[1]))',
     'if False:\n    sys.exit(main(sys.argv[1]))'),
    ("M113-inventory-gate-off", "tools/gt_inventory.py",
     '    return 1 if problems else 0', "    return 0"),
    ("M114-inventory-accepts-missing-total", "tools/gt_inventory.py",
     '        problems.append("no GT_TOTAL line: the evaluation did not finish")', "        pass"),
    ("M103-unlisted-skip-allowed", "tools/gt_eval.py",
     '            problems.append(f"page skipped but not pinned ({bucket}): {name}")',
     "            pass"),
    ("M104-stale-skip-pin-ignored", "tools/gt_eval.py",
     '        stale = sorted(allowed_repeat & set(evaluated_names))', "        stale = []"),
    ("M105-page-total-not-checked", "tools/gt_eval.py",
     '        if total and seen_pages != total:', "        if False:"),
    ("M106-gt-only-unevaluated-selection-ignored", "tools/gt_eval.py",
     '            problems.append(f"GT_ONLY selected pages that were not evaluated: {sorted(missed)}")',
     "            pass"),
    ("M107-manifest-inventory-not-compared", "tools/gt_verify.sh",
     'if ! diff -q "$listed" "$present" >/dev/null; then', "if false; then"),
    ("M108-project-pair-count-not-checked", "tools/gt_verify.sh",
     '    fail "$name: $npdf pdf / $nxml netlist (expected 1/1 or 0/0)"', '    true'),
    ("M109-export-failure-set-not-checked", "tools/gt_verify.sh",
     '[ "$got_fail" = "$want_fail" ] || fail', "true || fail"),
    ("M110-sheet-copy-failure-hidden", "tools/gt_export.sh",
     '''  if ! (cd "$d" && find . -name '*.kicad_sch' -print0 | xargs -0 -I{} cp --parents {} "$tgt_abs/src/"); then''', "  if false; then"),
    ("M111-export-rc-ignored", "tools/gt_export.sh",
     'kicad-cli sch export pdf --output "$tgt/$name.pdf" "$sch" >"$tgt/pdf.log" 2>&1 || { echo "FAIL pdf $name"; ok=0; }',
     'kicad-cli sch export pdf --output "$tgt/$name.pdf" "$sch" >"$tgt/pdf.log" 2>&1 || true'),
    ("M101-attachment-only-pair-inferred", "ecci/solver.py",
     '        if self._elec_of.get(p, p) != self._elec_of.get(q, q):\n            return UNKNOWN',
     "        if False:\n            return UNKNOWN"),
    ("M102-electrical-partition-includes-attachments", "ecci/solver.py",
     "dict(_components(objs, elec_edges)))", "dict(_components(objs, final_edges)))"),
    ("M100-gt-unevaluated-page-ignored", "tools/gt_eval.py",
     'problems += [f"page never evaluated: {n}" for n in never_ran]', "problems += []"),
    ("M94-gt-incomplete-run-passes", "tools/gt_eval.py",
     '    if cov["problems"]:\n', "    if False:\n"),
    ("M95-gt-page-error-not-a-problem", "tools/gt_eval.py",
     'problems = [f"page error: {e}" for e in errors]', "problems = []"),
    ("M96-gt-manifest-not-checked", "tools/gt_verify.sh",
     '(cd "$OUT" && sha256sum --quiet -c MANIFEST.sha256) ||', "true ||"),
    ("M97-gt-source-pin-not-checked", "tools/gt_verify.sh",
     '[ "$(prov KICAD_SOURCE_SHA)" = "$(pin KICAD_SRC_SHA)" ] ||', "true ||"),
    ("M92-stem-strips-one-suffix-only", "ecci/pins.py",
     '(#u\\d+|_\\d+)+$', '(#u\\d+|_\\d+)$'),
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
     "            tol = eps + 0.5 * (p.stroke_w + q.stroke_w)\n            for k, end in enumerate((a, b)):",
     "            tol = eps\n            for k, end in enumerate((a, b)):"),
    ("M68-tiny-parts-merge-by-proximity", "ecci/symbols.py",
     "never merge by proximity: only by touching.\n    # Without it, text strokes chain into one page-sized \"cluster\" that matches nothing.\n    min_part = min_gap_part_frac",
     "never merge by proximity: only by touching.\n    # Without it, text strokes chain into one page-sized \"cluster\" that matches nothing.\n    min_part = 0.0 * min_gap_part_frac"),
    ("M69-lone-glyph-needs-no-strokes", "ecci/classify.py",
     "len(short) >= params.glyph_stroke_share * len(ps)", "False"),
    # ---- trust boundary of VERIFIED (status.py / solver.audit_result)
    ("M115-sound-kind-multiset-off", "ecci/status.py",
     "if kind == SOUND and tuple(sorted(r.kind for r in e.roles)) != SOUND_KINDS[e.rule]:", "if False:"),
    ("M116-role-obj-mismatch-ignored", "ecci/status.py",
     "if (e.roles[0].obj, e.roles[1].obj) != (e.a, e.b):", "if False:"),
    ("M117-verified-without-provenance", "ecci/status.py",
     "    if not e.prov or not _valid_ids(e.prov):\n        return INFERRED", "    if False:\n        return INFERRED"),
    ("M118-blank-evidence-id-counts", "ecci/status.py",
     "        if not _valid_ids(r.evidence):\n            return ROLE_CANDIDATE", "        if False:\n            return ROLE_CANDIDATE"),
    ("M119-truthy-weak-flag-trusted", "ecci/status.py",
     "if any(r.weak is not False for r in e.roles):", "if any(r.weak is True for r in e.roles):"),
    ("M120-n-prims-type-unchecked", "ecci/status.py",
     "if not isinstance(e.n_prims, int) or isinstance(e.n_prims, bool):", "if False:"),
    ("M121-audit-kinds-off", "ecci/status.py",
     "    elif want is not None and tuple(sorted(kinds)) != want:", "    elif False:"),
    ("M122-audit-endpoints-off", "ecci/status.py",
     "    if not (ends_ok and all(_audit_str(o) for o in objs) and objs[0] == e.a and objs[1] == e.b):", "    if False:"),
    ("M123-audit-provenance-off", "ecci/status.py",
     '        problems.append("no provenance record")', "        pass"),
    ("M124-audit-n-prims-off", "ecci/status.py",
     '        problems.append(f"{rule} at a point with {e.n_prims} primitives")', "        pass"),
    ("M125-audit-result-skips-unknown-edge", "ecci/solver.py",
     '            problems.append(f"I7 {eid}: VERIFIED edge is not in the graph")', "            pass"),
    ("M126-audit-result-trusts-edge-status", "ecci/solver.py",
     "        if classify_edge(emap[eid])[0] != VERIFIED:", "        if False:"),
    ("M127-gate-fp-ignored", "tools/gt_gate.py",
     '                fail(f"{mode}.{k}={raw!r} > {lim}")', "                pass"),
    ("M128-gate-pin-recall-ignored", "tools/gt_gate.py",
     "    if pr is None or pr < g[\"GATE_MIN_PIN_RECALL\"]:", "    if False:"),
    ("M129-gate-accepts-partial-run", "tools/gt_gate.py",
     '        fail("partial run (GT_ONLY): the M4.0 gate needs the full corpus")', "        pass"),
    ("M130-gate-accepts-inconsistent", "tools/gt_gate.py",
     '        fail(f"inventory_consistent={inv!r}")', "        pass"),
    ("M131-gate-min-pages-ignored", "tools/gt_gate.py",
     '            fail(f"evaluated={raw!r} < {g[\'GATE_MIN_PAGES\']}")', "            pass"),
    ("M132-verify-ok-list-ignored", "tools/gt_verify.sh",
     '[ "$got_ok_list" = "$want_ok_list" ] || fail', '[ "$got_ok_list" = "$want_ok_list" ] || true'),
    ("M133-eval-ok-list-ignored", "tools/gt_eval.py",
     '            problems.append(f"exported project set != pinned EXPECT_EXPORT_OK_LIST: "', '            (f"exported project set != pinned EXPECT_EXPORT_OK_LIST: "'),
    ("M134-pins-list-count-ignored", "tools/gt_eval.py",
     '        problems.append(f"EXPECT_EXPORT_OK_LIST has {len(ok_list)} names != EXPECT_EXPORT_OK={exp_proj}")',
     "        pass"),
]


HARNESS_TESTS = (
    "tests/unit/test_gt_failclosed.py::test_mutation_runner_reports_in_a_fixed_order_and_counts_every_mutation",
    # the runner's own adversarial tests: they test this file on a fake package, not ECCI, so they can neither
    # detect nor be broken by a mutation; running them in each copy would only cost time
    "tests/unit/test_mutation_runner.py",
)


def _is_harness(classname: str, name: str) -> bool:
    for t in HARNESS_TESTS:
        mod, _, fn = t.partition("::")
        dotted = mod[:-3].replace("/", ".") if mod.endswith(".py") else mod.replace("/", ".")
        if (classname == dotted or classname.startswith(dotted + ".")) and (not fn or name.split("[")[0] == fn):
            return True
    return False


KILLED, SURVIVED, INVALID = "KILLED", "SURVIVED", "INVALID"
COLLECTION_ERROR, HARNESS_ERROR, TIMEOUT = "COLLECTION_ERROR", "HARNESS_ERROR", "TIMEOUT"
STATUSES = (KILLED, SURVIVED, INVALID, COLLECTION_ERROR, HARNESS_ERROR, TIMEOUT)
SUITE_TIMEOUT = 900
# an exception of one of these types in a test is a broken import / source, not a test detecting the mutation
IMPORTISH = ("SyntaxError", "IndentationError", "TabError", "ImportError", "ModuleNotFoundError")


def run_suite(pkg_root: Path, junit: Path) -> tuple[int, str]:
    # HARNESS_TESTS check this file's own patterns against the (mutated) sources, so they fail for EVERY mutation
    # and would make each one look KILLED. They are tests of the runner, not of ECCI: deselected in the copy.
    desel = [a for t in HARNESS_TESTS for a in ("--deselect", t)]
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *desel,
                        f"--junitxml={junit}", "tests"],
                       cwd=pkg_root, capture_output=True, text=True, timeout=SUITE_TIMEOUT)
    return p.returncode, (p.stdout + p.stderr)


def _importish(text: str) -> bool:
    t = text or ""
    return any(t.startswith(n) or f"E   {n}" in t or f"\n{n}:" in t for n in IMPORTISH)


def classify(code: int, junit_text: str | None, out: str) -> tuple[str, str]:
    """(status, detail) of one pytest run. SURVIVED here means "collected and all passed"."""
    tail = (out or "").strip().splitlines()
    last = tail[-1][:160] if tail else ""
    if code == 3:
        return HARNESS_ERROR, f"pytest internal error (exit 3): {last}"
    if code == 4:
        return HARNESS_ERROR, f"pytest usage error (exit 4): {last}"
    if code == 5:
        return COLLECTION_ERROR, "no tests collected (exit 5)"
    if code == 2:
        text = (out or "") + (junit_text or "")
        if "collection failure" in text or ("error" in text.lower() and ("collect" in text.lower() or _importish(out))):
            return COLLECTION_ERROR, f"collection / import error (exit 2): {last}"
        return HARNESS_ERROR, f"pytest interrupted (exit 2): {last}"
    if code not in (0, 1):
        return HARNESS_ERROR, f"unexpected pytest exit status {code}: {last}"
    if not junit_text:
        return HARNESS_ERROR, f"no JUnit report (exit {code})"
    try:
        root = ET.fromstring(junit_text)
    except ET.ParseError as x:
        return HARNESS_ERROR, f"unreadable JUnit report: {x}"
    cases, failures, errors = 0, [], []
    for c in root.iter("testcase"):
        cases += 1
        nid = f"{c.get('classname', '')}::{c.get('name', '')}"
        if _is_harness(c.get("classname", ""), c.get("name", "")):
            return HARNESS_ERROR, f"deselected harness self-test ran: {nid}"
        for ch in c:
            info = (nid, ch.get("message") or "", ch.text or "")
            if ch.tag == "failure":
                failures.append(info)
            elif ch.tag == "error":
                errors.append(info)
    if cases == 0:
        return COLLECTION_ERROR, "0 tests collected"
    coll = [x for x in errors if "collection failure" in x[1] or not x[0].split("::")[-1]]
    if coll:
        return COLLECTION_ERROR, f"collection error: {coll[0][0]} {coll[0][1]}"[:160]
    imp = [x for x in failures + errors if _importish(x[1]) or _importish(x[2])]
    if imp:
        return COLLECTION_ERROR, f"import / syntax error in {imp[0][0]}: {imp[0][1]}"[:160]
    if code == 0:
        if failures or errors:
            return HARNESS_ERROR, "exit 0 but the report has failures / errors"
        return SURVIVED, f"{cases} tests passed"
    if failures:
        return KILLED, f"FAILED {failures[0][0]}: {failures[0][1]}"[:160]
    if errors:
        return HARNESS_ERROR, f"only setup / teardown errors, no test failed: {errors[0][0]} {errors[0][1]}"[:160]
    return HARNESS_ERROR, "exit 1 but the report has no failure"


def run_and_classify(work: Path) -> tuple[str, str]:
    junit = work.parent / "junit.xml"
    try:
        code, out = run_suite(work, junit)
    except subprocess.TimeoutExpired:
        return TIMEOUT, f"suite exceeded {SUITE_TIMEOUT}s"
    except Exception as x:                                  # the runner itself failed: never KILLED
        return HARNESS_ERROR, f"runner failed: {type(x).__name__}: {x}"[:160]
    try:
        text = junit.read_text() if junit.is_file() else None
    except OSError as x:
        return HARNESS_ERROR, f"cannot read JUnit report: {x}"
    return classify(code, text, out)


def _compiles(rel: str, path: Path) -> str:
    """'' if the mutated file is still syntactically valid, else the error (a broken source is not a kill)."""
    if rel.endswith(".py"):
        try:
            compile(path.read_text(), str(path), "exec")
        except SyntaxError as x:
            return f"{type(x).__name__}: {x}"
    elif rel.endswith(".sh"):
        p = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        if p.returncode != 0:
            return f"bash -n: {p.stderr.strip()[:120]}"
    return ""


def make_copy(td: Path) -> Path:
    """Private copy of the package AND the claude-* workflows the suite reads. Without the workflows every run in
    the copy failed on a missing file, so every mutation was reported KILLED whatever it changed."""
    work = td / "claude"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", ".hypothesis", "out", "*.egg-info"))
    wf = ROOT.parent / ".github" / "workflows"
    (td / ".github" / "workflows").mkdir(parents=True)
    for f in sorted(wf.glob("claude-*.yml")):
        shutil.copy2(f, td / ".github" / "workflows" / f.name)
    return work


def baseline() -> tuple[int, str]:
    """The UNMUTATED suite must pass cleanly (collected, all passed) in the same kind of copy; otherwise KILLED
    means nothing. Returns (0, detail) only for a clean pass."""
    with tempfile.TemporaryDirectory() as td:
        st, detail = run_and_classify(make_copy(Path(td)))
    return (0, detail) if st == SURVIVED else (1, f"{st}: {detail}")


def check_one(mut) -> dict:
    """Apply one mutation to a private copy of the package and run the suite there. No shared state, so these
    run in parallel; the report is still printed in MUTATIONS order. Never raises."""
    mid = mut[0] if isinstance(mut, tuple) and mut else repr(mut)
    try:
        _, rel, old, new = mut
        if not old or old == new:
            return {"id": mid, "status": INVALID, "detail": "mutation changes nothing"}
        with tempfile.TemporaryDirectory() as td:
            work = make_copy(Path(td))
            target = work / rel
            if not target.is_file():
                return {"id": mid, "status": INVALID, "detail": f"no such file {rel}"}
            src = target.read_text()
            if src.count(old) != 1:
                return {"id": mid, "status": INVALID, "detail": f"pattern occurs {src.count(old)}x"}
            target.write_text(src.replace(old, new))
            if target.read_text() == src:
                return {"id": mid, "status": INVALID, "detail": "mutated file is unchanged"}
            bad = _compiles(rel, target)
            if bad:
                return {"id": mid, "status": COLLECTION_ERROR, "detail": f"mutated source does not compile: {bad}"[:160]}
            st, detail = run_and_classify(work)
            return {"id": mid, "status": st, "detail": detail}
    except Exception as x:
        return {"id": mid, "status": HARNESS_ERROR, "detail": f"runner failed: {type(x).__name__}: {x}"[:160]}


def main() -> int:
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    muts = [m for m in MUTATIONS if not only or m[0].split("-")[0] in only or m[0] in only]
    # one worker per CPU, but each worker runs a whole pytest suite, so leave one core free
    workers = max(1, int(os.environ.get("MUTATION_WORKERS", max(1, (os.cpu_count() or 2) - 1))))
    t0 = time.time()
    code, tail = baseline()
    if code != 0:
        print("MUTATION_BASELINE_FAIL the unmutated suite fails in the copy, so no mutation result is valid:")
        print(tail)
        return 1
    print(f"MUTATION_BASELINE_OK {round(time.time() - t0)}s", flush=True)
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check_one, m): m[0] for m in muts}
        for f in concurrent.futures.as_completed(futs):
            try:
                r = f.result()
            except Exception as x:                          # a crashed worker is a harness failure, never a kill
                r = {"id": futs[f], "status": HARNESS_ERROR, "detail": f"worker failed: {type(x).__name__}"}
            if r.get("status") not in STATUSES:
                r = {"id": futs[f], "status": HARNESS_ERROR, "detail": f"unknown status {r.get('status')!r}"}
            results[r["id"]] = r
            print(f"  {r['status']:9s} {r['id']}", flush=True)       # progress, unordered
    ordered = [results[m[0]] for m in muts]
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "mutation_report.json").write_text(json.dumps(ordered, indent=2))
    print()
    for r in ordered:
        print(f"{r['status']:9s} {r['id']:36s} {r['detail']}")
    bad = [r for r in ordered if r["status"] != KILLED]
    counts = {s: sum(r["status"] == s for r in ordered) for s in STATUSES}
    print("\nMUTATION_COUNTS " + json.dumps(counts))
    print(f"{len(ordered) - len(bad)}/{len(ordered)} killed in {round(time.time() - t0)}s "
          f"with {workers} workers")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
