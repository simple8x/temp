# QI9 DeepSeek vs Manus — visual adjudication of 27 disagreements

## Scope and provenance

- Repository: `simple8x/temp`
- Analysis branch: `analysis/qi9-deepseek-manus-disagreement-register`
- DeepSeek run: [42358860044](https://github.com/simple8x/temp/actions/runs/42358860044)
- Manus run: [42358853104](https://github.com/simple8x/temp/actions/runs/42358853104)
- Crop generation run: [42360843604](https://github.com/simple8x/temp/actions/runs/42360843604)
- Crop artifact: `qi9-disagreement-crop-pack` (54 crops, per-page contact sheets, manifest)
- Source PDF SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`
- PDF source path: `QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/-QI9-2604-A01- Module IO.pdf`
- Register: `analysis/qi9-deepseek-manus-disagreement-register.csv`

The crop workflow re-computes and asserts the PDF SHA-256 before rendering. It emitted both Y-origin interpretations; direct top-left coordinates align with the target schematic terminals on pages 5, 7, 8, and 11. The flipped interpretation is retained in the artifact as a coordinate sanity check, not used for adjudication.

## Visual findings

| Cases | Observation from source-PDF crop | Assessment |
|---|---|---|
| DIS-003–006 | S1 terminal numerals 1, 6, 3, and 4 are visible at the four corresponding crosshairs. | DeepSeek pin labels have direct visual support. Manus UNKNOWN is less informative here. |
| DIS-001, 002, 007, 008 | Crosshair lands on the polarized capacitor positive plate / polarity mark. No numeric pin number is visible in the crop. | Manus `+` is a polarity label, not yet proven to be a library pin identifier. Keep pin identity unresolved. |
| DIS-009, 010 | R24/R25 show printed value `0` adjacent to the resistor body. The crop does not show a pin numeral `0`. | Manus pin `0` is unsupported by this visual evidence; `0` is the component value in the drawing. Keep terminal pin identity unresolved rather than promoting DeepSeek UNKNOWN to a verified pin. |
| DIS-011 | The coordinate aligns with a wire terminal beside a visible `8`; the reference designator is outside the crop. | Pin label 8 has visual support, but component reference remains unknown. |
| DIS-012, 013, 016, 017, 020, 021, 024, 025 | Q2/Q3/Q4/Q5/Q6/Q7/Q8/Q9 transistor base terminal is visibly labelled pin 1. | DeepSeek Qx.1 assignment has direct visual support. |
| DIS-014, 015, 018, 019, 022, 023, 026, 027 | Corresponding R54/R55/R58/R59/R62/R63/R66/R67 top terminals are visible on the same graph components as the paired Qx.1 base terminals. The resistor pin number is not printed in the PDF crop. | This is a multi-terminal-per-connected-component case. The graph component can contain both the transistor base pin and resistor terminal; selecting one candidate must not suppress the other. Resistor pin numbering still requires symbol-library evidence. |

## What is and is not established

**VERIFIED**
- The two algorithm runs used the same source PDF hash and page scope, per the prior run manifests.
- The crop workflow succeeded and independently enforced the source PDF SHA-256.
- 27 register rows produced 54 crops (both Y-origin interpretations).
- Direct top-left coordinate crops visually align to the expected schematic regions.
- The S1 and Qx transistor pin numerals listed above are visible in the PDF.
- R24/R25's printed `0` is the resistor value text, not a pin numeral shown in the crop.

**NOT VERIFIED / BLOCKED**
- No source Altium `.SchLib` / `.IntLib` or equivalent authoritative symbol-library pin map is present in the scanned repository tree. Therefore PDF visual evidence cannot prove the library pin numbers for capacitors or resistors.
- No source-wire primitive IDs have been attached to the 27 rows.
- No electrical net membership is adjudicated by this visual review.
- Neither algorithm is promoted as the overall winner from these 27 cases.

## Required implementation consequence

1. Preserve multiple symbol-terminal candidates inside one connected graph component. Do not use one terminal identity as an exclusive representative for the entire component.
2. Treat polarity text (`+`) and component value text (`0`, `510`) as distinct evidence classes from pin-number labels.
3. Keep identity UNKNOWN until matched to an authoritative symbol pin map or an explicit PDF pin numeral.
4. Keep electrical verification BLOCKED until source-wire provenance and independent net-membership checks are available.

The updated CSV records per-case source hash, coordinate convention, visual evidence, visual adjudication, and the explicit `NOT_ADJUDICATED` electrical status. This is a visual evidence pass, not electrical sign-off.
