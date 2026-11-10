# ECCI handoff (M4.0: ground-truth measurement)

State (branch `claude`, simple8x/temp): see `git log`. Last evidence at d6aec44: m1 run 42786398844 (385 tests,
mutation 111/111 killed, all jobs green), GT full run 42786399214 (`inventory_consistent: true`, 100 pages
evaluated, 42 pinned UNKNOWN, 32 projects, FP_V=FP_I=0).
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
- `gt_source.txt` pins what the DATA depends on: KiCad demos commit, KiCad package build, expected export
  outcome (32 OK; CM5_MINIMA_3, StickHub, constraints, pic_programmer fail to export with this build). It is
  hashed into the cache key, recorded in `PROVENANCE.txt` and checked by `gt_verify.sh`.
- `gt_pages.txt` pins the disposition of every PAGE (min 100 evaluated, 142 total, the 42 repeated-sheet pages
  that stay UNKNOWN). NOT in the cache key, so an inventory fix does not cost a 20-minute re-export.
- `gt_export.sh` writes `PROVENANCE.txt` + `MANIFEST.sha256` and exits non-zero unless the export outcome equals
  the pins (sheet-copy failures included). `GT_SELFTEST=1` with `GT_SRC_DIR` runs it against a local demos tree
  and a stub kicad-cli; the provenance then says SELFTEST, so such a tree can never pass `gt_verify.sh`.
- `gt_verify.sh` checks provenance against the pins, re-hashes every manifest line, and compares the manifest
  against the files actually present (truncated manifest, deleted file, undeclared extra file), one PDF + one
  netlist + copied sheets per project, and both project sets.
- `gt_eval.py` prints `GT_COVERAGE` and exits 1 with `GT_INCOMPLETE` on a page error/timeout, an unpinned skip, a
  stale skip pin, a page total that does not add up, a changed export-failure set, or a `GT_ONLY` selection that
  was not evaluated. The key is `inventory_consistent`, NOT "complete": `unevaluated_pages` and `page_coverage`
  are reported so an unmeasured page cannot read as a measured one.
- `gt_inventory.py` prints the disposition of every page AND is the gate that a result exists at all (no
  `GT_TOTAL` line, no evaluated page, or any unpinned skip fails the step). Added after a green GT run measured
  nothing because a rewrite had dropped gt_eval's `__main__` block.

## Fast loop (avoid 20-min runs)
- `claude/tools/gt_only.txt`: comma list `project` or `project#pN`; empty = all. Push -> claude-gt in ~1-5 min
  (exports cached). Leave it EMPTY before a full run.
- `claude/tools/gt_dump.txt`: comma list of lib_id substrings; gt_eval prints prims, notes, nearby clusters,
  template diagnosis and (for DIFFERENT_TIP_SETS) per-hit pin-line support for the first miss of each.
- `[diag]` / `[profile]` in the commit message enable the diagnostic / profiling steps.
- Changing `gt_export.sh` or `gt_expected.txt` invalidates the cache: that run re-installs KiCad and re-exports
  (~20 min). Everything else reuses the cache.
- Artifacts cannot be downloaded from this sandbox; read a run's log with
  `gh api repos/simple8x/temp/actions/jobs/<job_id>/logs` and grep it - do not page whole logs through the chat.
- GT wall-clock: plan ~65 s + eval ~350 s with 4 workers (pages, not projects, are the unit of work).
  mutation_check.py runs the mutations in parallel (MUTATION_WORKERS, default cpu-1): 111 in ~5 min, report
  order fixed.

## Done this stretch
- `_part_stem` ignores the library nickname and stacked unit suffixes, so KiCad's duplicate embeddings
  ("video_schlib:SIM4X32" + "SIM4X32_1..7", "XCZU4CG-1SFVC784E_1#u2") are recognised as one part and the drawn
  pin lines pick the unit: video#p5 0/448 -> 448/448 local pins, vme-wren#p3 0/98 -> 96/98. Pins stay CANDIDATE
  (the lines made the choice, so S2 is not independent).
- External review (ChatGPT), round 1 then round 2, each item reproduced before fixing:
  1. structural attachment was in the VERIFIED groups (round 1), then still produced INFERRED through
     `_net_of` (round 2). Electrical edges now have their own partition `_elec_of`; an attachment-only pair is
     UNKNOWN and the net is still OPEN. M93, M101, M102.
  2. GT runs were green with missing data -> fail-closed, then a pinned per-page inventory. M94-M95, M100,
     M103-M106, M112-M114.
  3. GT cache/manifest integrity -> pins, provenance, manifest completeness, export failure propagation.
     M96-M97, M107-M111. One real bug fell out of it: `cp --parents` only worked while OUT was relative, so
     with an absolute OUT the source sheets were silently not copied.
- `strip_stubs` indexed: tinytapeout-demo#p0 211s -> 67.6s, no longer times out. Same test stated twice
  (`strip_stubs_reference`) with a property test and a cell-boundary test; mutations M98/M99.

## Next
1. M4 writer (approved, started then parked): .kicad_sch + netlist writer, V-W round-trip through kicad-cli,
   V-G against the GT netlist. M4.0 is FROZEN: GT is a regression gate now (FP=0, pin recall >= 55%,
   inventory_consistent true), not a target to keep chasing symbol by symbol.
2. tinytapeout-demo#p0: now evaluated but 1/421 local pins found - diagnose (103k prims, giant clusters).
3. giant_NONE / NONE by symbol: SN74VMEH22501, HARTING, XC4005, BT812, SN74LV595; `UNIQUE` misses rose to 144.
4. Clock-wedge pins, SPICE sources, arc symbols (L_Small), symmetric triangles.
5. Net names / labels (TR6) for nonlocal pairs.
6. Do not claim full connectivity until nonlocal pairs are solved (overall recall ~10%).
