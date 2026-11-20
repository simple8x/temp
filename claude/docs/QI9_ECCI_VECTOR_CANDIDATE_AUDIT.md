# QI9 ECCI vector candidate audit

**Source:** `[QI9-2604-A01] Module IO.pdf`  
**SHA-256:** `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`  
**Pages:** schematic sheets 5–11 (PDF page indices 4–10)

## What was run

The existing ECCI PDF vector interpreter, `classify_page`, `find_dots`, `detect_pins`, and `build_conn_graph` were run against the actual source PDF. No KiCad symbol library was loaded. The solver, writer, export, and production graph were not run.

## Defect found and fixed

The PDF paints many junction markers as two separate vector paths: a filled closed disc and a stroke-only outline with the same contour. The old `find_dots` treated the companion outline as a second BODY primitive touching the disc, so it rejected every such marker as if it were a filled circle belonging to a symbol (TC-X3).

`find_dots` now excludes only a geometrically matching stroke companion from the disc-vs-body contact test. The companion remains in the page IR and all other processing. A separate third BODY primitive still suppresses the dot. Synthetic regression tests cover both the valid paired-path marker and the real symbol-body contact case.

## Actual QI9 results

| PDF page | Vector primitives | WIRE primitives | Dot candidates | Dots attached to wire nodes | Candidate edges (TR1/TR2/TR4/TR5) | Ambiguity sites | Unresolved terminal-contact records |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 11,341 | 487 | 35 | 35 | 278 (154/106/15/3) | 29 | 236 |
| 6 | 9,330 | 318 | 26 | 17 | 157 (90/53/4/10) | 3 | 176 |
| 7 | 10,138 | 306 | 20 | 20 | 160 (78/65/2/15) | 3 | 140 |
| 8 | 5,292 | 196 | 8 | 8 | 101 (68/24/8/1) | 4 | 100 |
| 9 | 8,221 | 212 | 3 | 3 | 37 (25/9/3/0) | 1 | 212 |
| 10 | 11,031 | 617 | 62 | 62 | 595 (381/188/26/0) | 52 | 374 |
| 11 | 13,291 | 567 | 48 | 48 | 400 (232/152/16/0) | 26 | 352 |
| **Total** | **68,644** | **2,703** | **202** | **193** | **1,728 (1,028/597/74/29)** | **118** | **1,590** |

The 1,590 count is a count of unresolved terminal-contact records, not a count of unique electrical terminals. The 9 detected dots not attached to a wire node remain unconnected candidates. Candidate edge and dot records retain source primitive/content references.

## Validation

- `claude/tests/unit/test_rules.py`: **27 passed**.
- Full `claude/tests` run, excluding an unrelated pre-existing property-test failure in `test_text_vs_symbols.py` and an untracked test file not part of this branch: **1,013 passed**.
- The failing property test concerns glyph/text classification and does not import or exercise the modified `find_dots` path.
- `git diff --check`: passed.

## Authority boundary and remaining blockers

All outputs are `CANDIDATE` or `UNKNOWN`; no `VERIFIED` items were emitted, no solver promotion was executed, and no net was created or modified. This is not a KiCad netlist.

Still blocked:

1. No source KiCad symbol library was supplied, so reference-to-body identity and pin-number mapping remain UNKNOWN.
2. The vector importer reports `clip_not_applied` and `text_show_ops` on each schematic page.
3. Net labels, power symbols, hierarchical labels, and no-connect markers are not available to this vector-only pass.
4. The 118 ambiguity sites require additional independent evidence and review; do not resolve crossings by geometric coincidence alone.

## Reproduction

From the repository root:

```bash
PYTHONPATH=claude python claude/tools/qi9_vector_candidate_audit.py \
  --pdf '/path/to/[QI9-2604-A01] Module IO.pdf' \
  --output /tmp/qi9_ecci_dot_reconciliation_v1.json
```

The generated JSON includes page summaries, dot candidates, rule candidates, ambiguity sites, unresolved terminal-contact records, and the source PDF hash.
[exit status 0]