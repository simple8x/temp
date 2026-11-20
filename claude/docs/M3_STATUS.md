# M3 status: rules, constraints, solver, status

Labels: FACT = tested here; ASSUMPTION = modelling choice, not validated on ground truth.

## Delivered
- `ecci/status.py`: `classify_edge` (only way a status is created, I1), `audit_verified` (separate re-check, I2).
- `ecci/constraints.py`: C1 (confirmed names), C4 (cannot-link), C5 (NC pin singleton), witness sets via
  biconnected blocks (edges on SOME simple a-b path; whole set rejected, nothing preferred).
- `ecci/solver.py`: phases 1-3 with the v2.1 re-solve loop, quarantine, sites, nets, closure, canonical digest.
- `ecci/rules.py`: builds the candidate graph from M1/M2 (TR1, TR2 + junction dots, TR3 incl. own stub,
  TR4, TR5, TR7 sites, C4). `analyze_page` runs classify -> pins -> graph -> solve.

## Test coverage map (all local; CI evidence is in the run linked in the report)
| Item | Test |
|---|---|
| TC-X1, X2, X3, X4, X6 | test_solver (abstract), test_rules (PDF scenes) |
| TC-C1..C8 | test_solver |
| TC-C9 (rollback), TC-C10 (idempotence) | test_solver |
| TC-H1, H2, H4 | test_rules |
| TC-H3 | revised by condition 2 (see M2_STATUS) |
| TC-H5 (weak source <= INFERRED) | test_solver (abstract; no raster path before M6) |
| TC-H6 (premise record), TC-H7 (OPEN closure) | test_solver |
| TC-P1, P2, P3 linkage | test_rules |
| I5 (order independence) | permutation tests (300 + random graphs), end-to-end on a built page graph |
| I6 | random property tests, see finding 1 |
| I7 | TR1 precondition, 1000 random sites, 4-arm scene |
Mutation: M30-M60 (new) all KILLED locally; M53 first SURVIVED (aspect gate subsumed by the roundness gate
on ellipses) -> test that neutralises the roundness gate added, then KILLED. M48 and M54 survived once and got tests.

## Findings (read these)
1. **I6 literal vs site entailment.** "Adding a constraint never raises a status" holds for candidate-edge
   statuses (also with sites) and for pin-pair statuses on graphs WITHOUT sites. With sites it can fail by
   design: a constraint can make exactly one site option consistent, and that commit (INFERRED) links pins
   that were UNKNOWN before. This follows from C6/entailment in the approved design; flagged for your decision.
2. **Names are both constraint and evidence.** A confirmed net name creates a C1 constraint AND a TR6 link
   (same name). Adding such a name can therefore raise a pair status. The I6 test uses unique names.
3. **Phase 2 needs one round.** Rejecting the whole witness set removes every violating simple path, so the
   loop converges after one round; the loop is kept as a guard. This also makes the result monotone.
4. **Phase-3 guard.** A consistency-checked option can never create a violation on the normal path; the
   rollback is therefore tested white-box with a `fixed` (injected) option (TC-C9), not through a free choice.
5. **Domain exhaustiveness (ASSUMPTION).** A site resolves to INFERRED when exactly one of the 5 options
   (ALL4, 2 straight pairs, 2 corner pairs, NONE; 2 options when two straight wires cross) is consistent. This
   is relative to that domain.
6. **C4 only against template-resolved pins.** A first version also linked unresolved terminals and produced
   ~80k cannot-links and 78 false quarantines on a real page (a wire cannot-linked to its own terminal).
   Fixed and covered by test (M58).

## Real PDF (observation only, NO ground truth): `[QI9-2604-A01] Module IO.pdf`, empty symbol library
- Page 4: 18 wires, 4 unresolved terminals, no edges.
- Page 10 (11 s): 974 wire objects, 889 unresolved terminals; edges TR1 393 (VERIFIED), TR4 177 + TR5 39
  (INFERRED); 56 sites, all UNKNOWN (50 of them CLUSTER_TOO_LARGE, k_max=5); 377 nets, 344 OPEN; no quarantine.
- 0 junction dots found. 62 small filled discs exist, but each is paired with a same-size outline circle
  (terminal markers), so they touch another BODY primitive and are correctly NOT taken as dots.

## Known limits
- Net names, labels, power symbols, NC markers are not extracted from PDFs: TR6, C1, C2, C5 are only exercised
  on abstract graphs. HumanReview path (I1) is not implemented.
- Crossings without a dot make every involved net OPEN (spec TR7). On a dense page almost all nets are OPEN.
- Site interaction clusters above k_max=5 stay UNKNOWN (conservative, loses recall).
- Thresholds (dot size/roundness, g_max=3.0, tip tolerance) are ASSUMPTIONS, not calibrated.
- Clusters of BODY primitives on dense pages merge heavily (13 clusters on page 10); pin detection and C4 on
  such pages are not trustworthy yet.
- Arm direction is taken from the neighbouring node; non-axis-aligned or overlapping arms fall to a MULTI site.
