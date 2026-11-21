# M4.2 — native KiCad export with provenance, real kicad-cli round trip, GT comparison

Pipeline: PDF page → `ecci.connectivity` (M4.1, `writer.loads`) → `ecci.symbols` (`symrec.loads`) →
`.kicad_sch` + manifest (`kicad_export`) → **fresh** `kicad-cli sch export netlist` → V-W (`compare_roundtrip`) →
V-G (`compare_ground_truth`). One-page driver: `ecci/m4_pipeline.run_page`; tools `tools/m4_selftest.py`
(generated scenes) and `tools/m4_gt.py` (KiCad ground-truth corpus).

## `ecci.symbols` schema 1 (`ecci/symrec.py`)
Bound to one connectivity document (`connectivity_sha256`) and one library (`library.sha256`). Per cluster:
primitive ids + PDF content refs, bbox / verts / segs, **every** template hit of the whole library (symbol,
lib token, D4 orientation + matrix, scale, tx, ty, residual, predicted pins, `selected`), resolution
(status, id_state, note, selection `ALL` | `PIN_LINES`), identity (`CONFIRMED` only for one symbol name),
transform (`UNIQUE` | `EQUIVALENT` | `AMBIGUOUS` | `UNKNOWN`), the pin objects of the connectivity document,
`reference` / `value` = `UNKNOWN`/null (no designator reader exists; anything else is rejected on read).

Reader re-derives: candidates = `match_cluster` over the whole library on the stored geometry (catches forged,
dropped or altered transforms), selection, `resolve`, identity, transform state, pins vs the connectivity doc,
primitive refs vs the connectivity doc, canonical text, hash. Not re-derivable from the file: that the geometry
is the page's and that lineages name real geometry → `audit_against_page` and `audit_evidence` (run in the
pipeline's `provenance` stage).

## Export policy (`ecci/kicad_export.py`, fail-closed, every decision + reason in the manifest)
- PLACED only with identity CONFIRMED and either (a) transform UNIQUE/EQUIVALENT with resolution UNIQUE and
  pin ids CONFIRMED, or (b) resolution UNIQUE with pin id state EQUIVALENT: every ambiguous pin-number
  permutation maps only pins with identical library electrical signatures (name, electrical type, shape,
  hidden flag, length). Case (b) uses candidate 0 as an explicitly synthetic canonical pin numbering; the source
  pin-number identity remains EQUIVALENT, never CONFIRMED. Library text, scale = page scale (±0.5 %), KiCad
  orientation, library pin geometry, exact pin tips (≤ 10 µm), implicit connections and coincident pins are
  still checked.
- Transform EQUIVALENT = several hits of ONE symbol putting every pin number on the same tip. Pin-id EQUIVALENT
  is distinct: symmetric electrically identical terminals (e.g. a passive resistor's two `~` pins) may be
  canonically numbered for export because swapping them preserves pin electrical semantics. Manifest pin records
  retain `source_number_candidates` and `number_assignment=EQUIVALENT_CANONICAL`; different electrical
  signatures or different symbol identities remain NOT_PLACED.
- Nets = `verified_group` of the connectivity doc only (quarantined groups excluded). INFERRED / UNKNOWN /
  CONFLICT edges, site links, open sites and partially exported groups are listed in `not_exported`.
- A net is drawn as local labels on pin ends; **no wire geometry** (not in the connectivity doc).
- Designators: `ECCI<cluster>`, `kind: SYNTHETIC`; `source_reference` stays UNKNOWN. Value property "UNKNOWN".
- Mirror/rotate order: only the two diagonal reflections depend on it. Measured: kicad-cli 7.0.11 uses `RM`
  (`MR` splits nets); `tests/kicad/test_kicad_cli.py::test_mirror_order_is_the_one_kicad_uses` re-measures it on
  the CI build and requires the other order to be DETECTED by the round trip.
- `verify_manifest` re-plans from the inputs; a manifest or schematic that differs is rejected.

## Round trip / GT (`ecci/kicad_roundtrip.py`)
- The netlist file is deleted before each call and must be newer than the call: a stale or cached netlist can
  never be read as a fresh export. Non-zero exit → `kicad_read` FAIL, later stages BLOCKED.
- V-W: schematic sha256, symbols, labels, wires vs manifest; netlist components + lib ids; every exported pin
  present; each manifest net classified OK / MISSING / SPLIT / MERGED; a pin the manifest leaves alone that KiCad
  connects is EXTRA.
- V-G: exported pin ends matched 1:1 to GT pins (≤ 1 pt); pairs TP / FP / FN / TN; GT pins not exported are
  counted `gt_pins_not_exported_UNKNOWN` and listed (`unmatched_gt_pins`, state UNKNOWN); exported pins that
  could not be mapped are listed with a reason (`unmatched_exported_pins`, state UNKNOWN). `mapping_coverage` =
  mapped exported pins / GT scoreable pins. The `ground_truth` stage is `ground_truth_gate` (see Hardening, B3).
- Gates (`m4_pipeline.gate_table`): a gate is PASS only if every page passed it; any FAIL → FAIL; anything else
  (BLOCKED, INCONCLUSIVE, unknown) → BLOCKED.

## Tests / mutations
`tests/unit/test_m4_export.py` (25, no KiCad): multiple candidates, identity AMBIGUOUS/UNKNOWN, reference
UNKNOWN + synthetic id, 6 transform tamperings, dropped/injected candidate, electrically equivalent pin-number
permutation + canonical mapping, non-equivalent pin-number ambiguity remains blocked, crossing
site not exported, INFERRED T not exported, net evidence, forged edge / partition / manifest net / schematic
wire, evidence lineage + primitive ref + geometry tampering, wrong binding/library, hidden power pin, coincident
pins, library pin off the tip, determinism, orientation table, MERGED/MISSING/EXTRA classification, GT mismatch,
gate table. `tests/kicad/test_kicad_cli.py` (16, real CLI, CI job `kicad`): CLI present, 12 scenes all stages
PASS, mirror order both ways, unreadable schematic + stale netlist. Mutations M144–M164 (M156/M157 need the CLI,
so the mutation job installs KiCad and sets `ECCI_KICAD_TESTS=1`).

## Limits
- No wire geometry and no designators/values in the output; nonlocal connectivity (labels, power, sheets) is
  still unsolved upstream, so GT recall stays low; FP and mapping coverage are gated (B3), FN is reported.
- Symbol choice is limited to what template matching supports: symmetric parts with non-equivalent pin
  signatures stay NOT_PLACED; electrically identical terminals may use only a declared synthetic canonical map.
- Library: the GT run embeds the source design's exact `lib_symbols` text (an ASSUMPTION shared with M4.0:
  the library is known). Derived (`extends`) symbols are not supported by `kicad_lib`.
- Not re-verifiable from files alone: PIN_LINES selection support (needs the page's pin lines) — the file records
  the lines and `audit_pin_lines` checks them against the page; without the page the result is BLOCKED.
- PR #12 (reader recomputes site resolution) is not on this branch; the export only uses VERIFIED groups, which
  do not depend on site links.

## Hardening (M4.2 blockers B1–B3)

### B1 — S2 (`MEASURED_STUB`) lineage is geometric
- `detect_pins` records the measured segment behind every S2 on the pin (`PinResult.stub`); the connectivity
  document's pin record carries `stub` = `{prim, segment, a, b, orientation, tolerance}` (null without S2) and
  the stub primitive is always a cited primitive. Stripped body stubs contribute their real segments (no chord).
- `writer.s2_problems` (run by `writer.loads`, and by `symrec.loads` on the bound connectivity document): a pin
  cites S2 iff it has a stub, only as its own `S2:<cluster>:<k>`; the S2 record is an underived VECTOR
  `MEASURED_STUB` whose lineage is exactly `[geom:<stub.prim>]`; no S2 record is uncited; the segment is
  finite and non-degenerate; the recorded orientation holds (`FORWARD`: a≈base, b≈tip; `REVERSED`: b≈base,
  a≈tip) within the recorded tolerance; the tolerance is ≤ the documented policy
  `max(S2_TAU_ABS_MAX = 0.05, S2_REL_MAX = 0.1 × pin length)` (the `PinParams` / `MatchParams` defaults;
  a run with wider parameters is rejected, not trusted); both ends lie in the primitive's recorded bbox ± tol.
- `symrec.audit_s2_against_page` (part of `audit_evidence`, pipeline `provenance` stage): the stub primitive is
  on the page with the cited content_ref, its segment `segment` is exactly a→b, and it is not part of the
  matched body (S1 lineage). No page → `BLOCKED`, never a pass.
- Re-hashing a forged document does not help: every check is semantic. Nothing is promoted: the checks only
  reject; `pos_state` is still what `decide_role` gave.

### B2 — PIN_LINES selections are audited against the source page
- `resolution.pin_lines` (symbols schema 1, new key): `[]` for `ALL`; for `PIN_LINES`, per selected candidate,
  per pin `{number, tip, base, line: {prim, content_ref, end, far, tolerance}}` — the drawn line `detect_pins`
  used (`pins.pin_line_support`, same rule as before: end at the tip, along the axis toward the base,
  0.5–1.3 pin lengths, tolerance as in B1).
- File level (`symrec.loads`): the record exists, covers every pin of every selected candidate with the
  predicted tip/base, each line satisfies the rule, tolerance ≤ policy.
- Page level (`symrec.audit_pin_lines(doc, page, cls=None)` → `VERIFIED_AGAINST_PAGE | FAIL | BLOCKED |
  NOT_APPLICABLE` + reasons): page_index matches (else FAIL "wrong page"); each line primitive exists, is an
  open 2-point non-text line, has the recorded content_ref; recorded end points equal the page line's (else
  "displaced"); the page line satisfies the rule for its pin (else "does not support the pin"); every selected
  candidate is fully drawn and every unselected one is not. No page / empty page / unknown page identity →
  BLOCKED. Folded into the `provenance` stage (FAIL → FAIL, BLOCKED → BLOCKED).
- Evidence in tests is the vector ingest of PDFs generated by the test helpers, not a real source PDF.

### B3 — ground-truth gate (`kicad_roundtrip.ground_truth_gate`)
- Order: FP > 0 → FAIL; no / malformed policy → BLOCKED; < 2 mapped pins or no scored pair → INCONCLUSIVE;
  `mapping_coverage` < `min_mapping_coverage` → FAIL; else PASS. A zero-mapped result can never PASS.
- Policy: `{"min_mapping_coverage": x}`, 0 < x ≤ 1. The project documents no M4.2 coverage threshold (the M4.0
  `GATE_MIN_PIN_RECALL` in `gt_gate.txt` is a different quantity), so **none is shipped**: `tools/m4_gt.py` reads
  `$M4_GT_POLICY` or `tools/m4_gt_policy.json`; absent → every page's `ground_truth` is BLOCKED and the
  `m4-kicad-export` job does not pass until the owner sets the threshold. The generated self-test scenes carry a
  test-only policy (`Scene.gt_min_coverage`: 1.0; `ambiguous_r` now reaches 1.0 because its resistor pins are
  electrically equivalent and are mapped by physical position, not source pin-number identity).
- UNKNOWN accounting: a GT pin is *claimed* when exactly one exported pin matches it geometrically (first claim
  wins; a second exported pin on the same GT pin is unmatched, "GT pin already matched") and *scored* only once
  that exported pin is on exactly one KiCad net. `gt_pins_scored` = scored = `exported_pins_mapped`;
  `gt_pins_not_exported_UNKNOWN` = scoreable − scored (since this fix: "not scored", previously "not claimed",
  which hid claimed-but-unscored pins); `gt_pins_claimed_not_scored_UNKNOWN` counts the latter;
  `unmatched_gt_pins[*].reason` says which. `mapping_coverage` = scored / scoreable.
- Reported per page (`gt_report.json`, `rec["ground_truth"]`) and in the CI step summary of `claude-gt`:
  GT scoreable / mapped / unmatched pins, coverage, TP / FP / FN / TN, precision, recall, gate state + reason.

### Tests / mutations
`tests/unit/test_m42_blockers.py` (57): B1 valid / unrelated primitive / foreign lineage / wrong endpoint /
widened tolerance / reversed / malformed / degenerate / missing lineage (4 forms) / symbol reader / page audit
without page / primitive missing from page; B2 valid / no promotion / wrong page / nonexistent / unrelated /
non-line primitive / shifted endpoint / foreign content_ref / line removed from page / no geometry / record
required / provenance wiring; B3 zero mapped / below threshold / sufficient / FP / missing & malformed policy /
policy file / unmatched UNKNOWN / gate table / run_page wiring (kicad-cli SIMULATED there; the real one is
`tests/kicad`). Mutations M165–M187; M88 and M161 re-targeted to the refactored code (same bug class).
[exit status 0]