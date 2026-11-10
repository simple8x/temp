# ECCI handoff (M4.0: ground-truth measurement)

State (branch `claude`, simple8x/temp): see `git log`. Last evidence: m1 run 42775917987 (363 tests, mutation
96/96, all jobs green), GT full run 42775918237 (`"complete": true`, 100 pages, 32 projects, FP_V=FP_I=0).
Rules: code only under `claude/` and `.github/workflows/claude-*.yml`; never edit other branches; no loosened
assertions; no auto UNKNOWN->VERIFIED; report SHA + run URL + PASS/FAIL/UNKNOWN + limits.

## Latest GT (100 pages, KiCad 9.0.9 demos @ c4d506e7; thresholds are ASSUMPTION)
- detected: FP_V=0, FP_I=0, precision 1.0/1.0; recall V 6.65% / VI 10.29% over ALL pairs
- detected_local: pin_recall 55.1% (of local pins), recall V 58.6% / VI 90.7%
- oracle_local recall V 97.5% / VI 99.75%; nonlocal pairs 0% by design (no net names/labels yet)
- misses: NONE 2472, giant_NONE 1995, no_cluster 847, UNIQUE 144, DIFFERENT_TIP_SETS 48
- NOT claimed: full schematic connectivity. Nonlocal (label/power/cross-sheet) pairs are unsolved, so overall
  recall stays ~10% even though local connectivity is good.

## Ground truth is fail-closed and pinned
- `claude/tools/gt_expected.txt` pins the KiCad demos commit, the KiCad package build and the expected export
  outcome (32 OK; CM5_MINIMA_3, StickHub, constraints, pic_programmer fail to export with this build).
- The cache key hashes that file plus `gt_export.sh`; `gt_export.sh` writes `PROVENANCE.txt` +
  `MANIFEST.sha256` and exits non-zero unless the outcome equals the pins; `gt_verify.sh` re-checks both before
  every evaluation (also on a cache hit).
- `gt_eval.py` prints `GT_COVERAGE` and exits 1 with `GT_INCOMPLETE` on any page error/timeout, or when a full
  run is below the pinned page/project counts. Totals over an incomplete corpus are not a result.

## Fast loop (avoid 20-min runs)
- `claude/tools/gt_only.txt`: comma list `project` or `project#pN`; empty = all. Push -> claude-gt in ~1-5 min
  (exports cached). Leave it EMPTY before a full run.
- `claude/tools/gt_dump.txt`: comma list of lib_id substrings; gt_eval prints prims, notes, nearby clusters,
  template diagnosis and (for DIFFERENT_TIP_SETS) per-hit pin-line support for the first miss of each.
- `[diag]` / `[profile]` in the commit message enable the diagnostic / profiling steps.
- Changing `gt_export.sh` or `gt_expected.txt` invalidates the cache: that run re-installs KiCad and re-exports
  (~20 min). Everything else reuses the cache.
- Artifacts cannot be downloaded from this sandbox; read a run's log with
  `gh api repos/simple8x/temp/actions/jobs/<job_id>/logs`.

## Done this stretch
- `_part_stem` ignores the library nickname and stacked unit suffixes, so KiCad's duplicate embeddings
  ("video_schlib:SIM4X32" + "SIM4X32_1..7", "XCZU4CG-1SFVC784E_1#u2") are recognised as one part and the drawn
  pin lines pick the unit: video#p5 0/448 -> 448/448 local pins, vme-wren#p3 0/98 -> 96/98. Pins stay CANDIDATE
  (the lines made the choice, so S2 is not independent).
- External review (ChatGPT) blockers, each reproduced before fixing:
  1. structural attachment (UNRESOLVED terminal -> its wire) was in the VERIFIED groups, so `pair_status`
     returned VERIFIED for it. Attachments now join the partition only; adversarial tests + mutation M93.
  2. GT runs were green with a missing page (tinytapeout-demo#p0 timeout) -> fail-closed as above (M94/M95).
  3. GT cache had no input integrity -> pinned + provenance + manifest + verify (M96/M97).
- `strip_stubs` indexed: tinytapeout-demo#p0 211s -> 67.6s, no longer times out. Same test stated twice
  (`strip_stubs_reference`) with a property test and a cell-boundary test; mutations M98/M99.

## Next
1. tinytapeout-demo#p0: now evaluated but 1/421 local pins found - diagnose (103k prims, giant clusters).
2. giant_NONE / NONE by symbol: SN74VMEH22501, HARTING, XC4005, BT812, SN74LV595; `UNIQUE` misses rose to 144.
3. Clock-wedge pins, SPICE sources, arc symbols (L_Small), symmetric triangles.
4. Net names / labels (TR6) for nonlocal pairs; M4 writer + V-W/V-G validators (approved, not started).
5. Review item 4 stands: do not claim full connectivity until nonlocal pairs are solved.
