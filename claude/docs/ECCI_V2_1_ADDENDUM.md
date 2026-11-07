# ECCI Design v2.1 addendum (4 approved conditions)

Status labels: **FACT** (measured/tested), **INFERRED**, **ASSUMPTION**. M1 contains geometry
only; nothing in M1 infers connectivity. The items below are the *contracts* later milestones
must satisfy, plus the M1 code that already enforces the parts that are pure functions.

## 1. Evidence independence (S1–S4)
Two evidence items are independent only if (a) neither is an ancestor of the other, (b) they
share **no ancestor at all** (including an unresolved one), and (c) their *closed* lineage
token sets are disjoint. Different method on the same data is **not** independence.

| Source | Lineage tokens it must declare (examples) |
|---|---|
| S1 template match | `prim:<id>` of every primitive in the matched cluster, `lib:<name>@<sha>` |
| S2 stub analysis | `prim:<id>` of the stub and of the symbol body it touches |
| S3 terminal anomaly | `prim:<id>` of the wire end and the symbol body |
| S4 text | `text:<ref>`; text may never create a position |

Consequence: S1 and S2 computed from the same body primitives are **dependent**, so a pin
position is CONFIRMED only when two items with disjoint closed lineage agree.
*Implemented in M1:* `ecci/evidence.py` (`independent`, `max_independent_subset`),
tests `tests/unit/test_evidence.py`. A test found and fixed a real bug during M1 (shared
unresolved ancestor was ignored).

## 2. Wire role: no circular reasoning
A role (wire/pin/dot) can be CONFIRMED only from evidence that (a) does not depend, directly or
transitively, on any connectivity claim (`uses_connectivity`), (b) is not RASTER/OCR, and
(c) has at least two pairwise-independent items. "This is a wire because it touches a pin" is
excluded when the touch is itself a connectivity edge. *Implemented:* `decide_role`
(`CIRCULAR_CONNECTIVITY_BASIS`, `WEAK_SOURCE_CANNOT_CONFIRM`, `UNKNOWN_EVIDENCE_ID`).

## 3. TR1: continuity vs coincidence
Two arms meeting at a node are one of:
- `SAME_PATH_VERTEX`: consecutive segments of one PDF path meeting at its vertex (incl. the
  closing segment of a closed path). This is **continuity of a drawing**, not two objects touching.
- `SAME_SEGMENT_THROUGH`: halves of one straight segment passing through the node.
- `SAME_PATH_OTHER`: same path, self-touch or self-crossing.
- `INDEPENDENT_PRIMS`: different primitives whose endpoints coincide.

Rule for later milestones: TR1 counts **distinct primitive ids** (`independent_prims_at`), never
arms. Continuity of a single path may support "this polyline is one wire object" only after the
path's wire role is CONFIRMED (condition 2); it never proves that two independent primitives
are connected. *Implemented:* `arm_relations`, `independent_prims_at`, tests in
`test_arrangement.py` and the property tests.

## 4. Phase 3 re-solve
After ambiguity sites are resolved, the solver MUST re-run Phase 1–2 on the augmented edge set
and re-check constraints, partition and statuses until a fixpoint; a Phase 3 commit that
creates a violation is rolled back (site -> UNKNOWN/CONFLICT). Not in M1 (no solver yet);
scheduled for M3 with test TC-C9 (below).

| ID | Test (M3) |
|---|---|
| TC-C9 | Resolve a site so that it merges two nets with different confirmed names; solver must detect after re-run and roll the site back |
| TC-C10 | Result after Phase 3 equals result of running the solver from scratch with the resolved site fixed (idempotence) |
