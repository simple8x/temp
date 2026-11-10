# ECCI handoff (M4.0: ground-truth measurement)

State (branch `claude`, simple8x/temp): see `git log`. Last evidence: m1 run 42653774645 (350 tests, mutation 87/87), GT full run 42650241273.
Rules: code only under `claude/` and `.github/workflows/claude-*.yml`; never edit other branches; no loosened assertions; no auto UNKNOWN->VERIFIED; report SHA + run URL + PASS/FAIL/UNKNOWN + limits.

## Latest GT (100 pages, KiCad 9 demos; thresholds are ASSUMPTION)
- detected: FP_V=0, FP_I=0, precision 1.0/1.0
- detected_local: pin_recall 50.3% (of local pins), recall V 60.9% / VI 90.3%
- oracle_local recall V 97.5% / VI 99.75%; nonlocal pairs 0% by design (no net names/labels yet)
- misses: NONE 2639, giant_NONE 1636, no_cluster 1478, DIFFERENT_TIP_SETS 47, UNIQUE 88

## Fast loop (avoid 20-min runs)
- `claude/tools/gt_only.txt`: comma list `project` or `project#pN`; empty = all. Push -> claude-gt runs in ~20 s-5 min (exports cached).
- `claude/tools/gt_dump.txt`: comma list of lib_id substrings; gt_eval prints prims, notes, nearby clusters, template diagnosis for the first miss of each.
- `[diag]` in commit message enables the old diagnostic steps. Leave gt_only.txt empty before a full run.
- Wait for CI with ONE polling command; m1 runs on every push (mutation ~10 min).

## Done this stretch
text filter ignores rows of identical oversized parts (decoupling caps); closed outlines up to 0.8 diag are BODY; `relaxations()` retries the template match without stubs / per colour group / without small round marks; pin lines pick the unit among units of one part (pins stay CANDIDATE, S2 not independent).

## Next
1. SIM4X32 (video#p5, 448) and XCZU4CG (vme-wren#p3, ~500): clusters now match but pins do not pair with GT; diagnose predicted vs GT pin positions.
2. giant_NONE / NONE by symbol: SN74VMEH22501, HARTING, XC4005, BT812, SN74LV595.
3. Clock-wedge pins, SPICE sources, arc symbols (L_Small), symmetric triangles.
4. Net names / labels (TR6) for nonlocal pairs; M4 writer + V-W/V-G validators (approved, not started).
5. Ask the user to have ChatGPT independently review the algorithm code and this evidence.
