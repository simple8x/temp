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
- PLACED only with identity CONFIRMED, transform UNIQUE/EQUIVALENT, resolution UNIQUE with pin ids CONFIRMED,
  library text present, scale = page scale (±0.5 %), KiCad orientation known, library pins = template pins,
  each KiCad pin end (integer IU arithmetic) on the detected tip (≤ 10 µm), no implicit KiCad connection
  (power symbol, hidden `power_in`), no pin end coinciding with a pin of another net.
- EQUIVALENT = several hits of ONE symbol putting every pin number on the same tip; the manifest records
  `SYNTHETIC_CANONICAL: candidate i of n`. Different symbols or different pin maps are never chosen between.
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
  counted `gt_pins_not_exported_UNKNOWN`. The `ground_truth` stage PASSes only with FP = 0; FN is reported.
- Gates (`m4_pipeline.gate_table`): a gate is PASS only if every page passed it; any BLOCKED → not PASS.

## Tests / mutations
`tests/unit/test_m4_export.py` (25, no KiCad): multiple candidates, identity AMBIGUOUS/UNKNOWN, reference
UNKNOWN + synthetic id, 6 transform tamperings, dropped/injected candidate, pin number UNKNOWN + forgery, crossing
site not exported, INFERRED T not exported, net evidence, forged edge / partition / manifest net / schematic
wire, evidence lineage + primitive ref + geometry tampering, wrong binding/library, hidden power pin, coincident
pins, library pin off the tip, determinism, orientation table, MERGED/MISSING/EXTRA classification, GT mismatch,
gate table. `tests/kicad/test_kicad_cli.py` (16, real CLI, CI job `kicad`): CLI present, 12 scenes all stages
PASS, mirror order both ways, unreadable schematic + stale netlist. Mutations M144–M164 (M156/M157 need the CLI,
so the mutation job installs KiCad and sets `ECCI_KICAD_TESTS=1`).

## Limits
- No wire geometry and no designators/values in the output; nonlocal connectivity (labels, power, sheets) is
  still unsolved upstream, so GT recall stays low; only FP is gated.
- Symbol choice is limited to what template matching supports: symmetric parts (R, C, …) stay NOT_PLACED.
- Library: the GT run embeds the source design's exact `lib_symbols` text (an ASSUMPTION shared with M4.0:
  the library is known). Derived (`extends`) symbols are not supported by `kicad_lib`.
- Not re-verifiable from files alone: PIN_LINES selection support (needs the page's pin lines).
- PR #12 (reader recomputes site resolution) is not on this branch; the export only uses VERIFIED groups, which
  do not depend on site links.
