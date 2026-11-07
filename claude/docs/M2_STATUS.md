# M2 status: line classes, role evidence, body clusters, pin hypotheses

Labels: FACT = covered by a test or measured; ASSUMPTION = not validated on real data.

## What M2 contains (FACT: code + tests)
- `ecci/sexpr.py`, `ecci/kicad_lib.py`: KiCad `.kicad_sym` reader (body graphics, pins, library SHA-256 in lineage).
- `ecci/classify.py`: classes BORDER / WIRE / BODY / OTHER with lineage-carrying evidence; role status via
  `decide_role` (>= 2 pairwise-independent items). No evidence uses connectivity.
- `ecci/symbols.py`: BODY clusters (touching, or overlapping/containing bounding boxes).
- `ecci/template.py`: S1. All 8 square symmetries x uniform scale; accepted only if vertex, corner and
  outline-coverage residuals are within tolerance; ALL accepted hits kept, ambiguity reported, never guessed.
- `ecci/pins.py`: S1 (template tip), S2 (measured WIRE-class segment spanning predicted base->tip),
  S3 (WIRE ends touching a body outline: `UnresolvedTerminal` or `UNEXPECTED_CONTACT` conflict).
  `pos_state` CONFIRMED needs S1+S2 with disjoint lineage; one source = CANDIDATE.

## Test cases (design v2 -> code)
| Design ID | Test | Result |
|---|---|---|
| TC-H1 border is not a wire | `test_classify::test_border_frame_is_border_and_never_wire` | PASS |
| TC-H4 dashed/graphic line is not a wire (class level) | `test_dashed_and_differently_styled_lines_are_not_wires` | PASS |
| TC-P1 R with stubs -> CONFIRMED | `test_p1_...` | PASS |
| TC-P2 no stub -> CANDIDATE | `test_p2_...` | PASS |
| TC-P3 unknown symbol -> unresolved terminals | `test_p3_...` | PASS |
| TC-P4 symmetric symbol -> numbers AMBIGUOUS | `test_p4_...`, property test | PASS |
| TC-P5 holdout symbol | `test_p5_...` | PASS |
| TC-P6 near-identical templates | `test_p6_...`, `test_p6b_...` | PASS |
| TC-P7 wrong orientation / distorted body / extra segment | `test_p7_*`, `test_p7b_*` (8 orientations), `test_p7c_*` | PASS |
| TC-P8 unexpected contact -> conflict | `test_p8_...` | PASS |
| lineage dependency recorded | `test_lineage_tokens_record_the_dependency_structure` | PASS |
| TC-H2, TC-H3, TC-H5..H7 | not M2 (need edges/solver/raster) | NOT RUN (M3+) |

### Revision of TC-H3 (consequence of approved condition 2, stated explicitly)
Design v2 said "floating wire, style matches, no terminals => role CANDIDATE". Condition 2 forbids using
terminal contact (a connectivity-like fact) to confirm a wire role, so the role can no longer depend on
terminals. New rule: a floating wire with independent shape + style evidence has role CONFIRMED; whether
any *edge* through it is VERIFIED is decided in M3 from pins/topology, never from the role alone.
No assertion that existed in code was changed; TC-H3 existed only in the design text.

## Real-PDF observations (repository PDF, no ground truth; NOT a correctness claim)
Pages 4 and 10 (about 11k to 13k primitives), fixture library (5 symbols, hand-written):
- Wire style found with 0.99 modal share (0.48 pt black).
- About 7.3k primitives per page are open non-axis-aligned strokes of the SAME width as wires: this PDF
  draws text as stroked paths. They land in BODY, forming 1353 / 1718 "clusters". M2 cannot tell text from
  symbol graphics, so most clusters are text and `unresolved_terminals` (4590 / 5669) is dominated by noise
  where wires touch text strokes. These must not be read as terminals. (needs a text/glyph filter)
- 4 clusters on page 4 (8 on page 10) matched the fixture symbol `D` with pins CONFIRMED. Rendering one
  of them shows an LED (triangle + bar + emission arrows). The template matched the triangle/bar part
  because the arrows do not touch the body and form separate clusters. Pin positions look plausible but
  the symbol identity (D vs LED) is wrong or at least unproven. No pin was checked against a ground truth.
- Runtime after adding a wire spatial index: about 9 s per page for clustering + matching (36 to 50 s before).

## Limitations (FACT unless marked)
- S4 (text) not implemented. Without it, pin NUMBERS are AMBIGUOUS for every symmetric symbol (R, U4...) and
  positions can reach CONFIRMED only when stubs are drawn as separate segments of the page's wire style.
- Clusters only merge touching or bbox-overlapping BODY primitives. Symbols with separated parts (capacitor
  plates, LED arrows) split into several clusters; a sub-part can match the wrong template.
- Text/glyph strokes are not recognised (see above).
- The library fixture is hand-written, NOT the official KiCad library (no network access to it here). The
  pin coordinates for R were written from memory of the official symbol: ASSUMPTION. Synthetic test PDFs are
  generated from the same fixture the matcher reads, which is optimistic (circularity noted in v2); the
  oracle for pin positions is the placement transform, not ecci output.
- Scale is estimated from the bounding box of the body cluster only; a cluster missing a symbol part gives a
  wrong scale and is then rejected, not corrected.
- Wire evidence assumes style (width/colour/dash) separates wires from graphics; when a producer uses one
  style for everything, only shape evidence remains and roles stay CANDIDATE (reported in `notes`).
- Multi-unit symbols: only units 0 and 1 are read (warning emitted).
- No junction dots, no edges, no nets (M3).
