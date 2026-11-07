# QI9 PDF→KiCad — DeepSeek vs Manus disagreement register

- Repository: `simple8x/temp`
- Analysis branch: `analysis/qi9-deepseek-manus-disagreement-register`
- DeepSeek run: https://github.com/simple8x/temp/actions/runs/42358860044
- Manus run: https://github.com/simple8x/temp/actions/runs/42358853104
- Visual crop workflow: https://github.com/simple8x/temp/actions/runs/42360843604
- PDF SHA-256 (both algorithm artifacts and source-PDF crop workflow): `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`
- Scope: PDF pages 5–11.
- Register rows: **27**.
- Crop artifact: `qi9-disagreement-crop-pack` — 54 PNG crops (two Y-origin interpretations), four per-page contact sheets, manifest, and README.

## Interpretation / limits

- This is a disagreement register with visual evidence, not a complete ground-truth verdict.
- The crop workflow verifies the source PDF hash and shows that direct top-left coordinates align with the target terminals.
- PDF-visible pin labels support S1 pins 1/6/3/4 and Q2–Q9 base pin 1. Capacitor polarity marks and resistor value text are not treated as pin numbers.
- The R54/R55/R58/R59/R62/R63/R66/R67 cases expose multiple symbol terminals within the same graph component; one terminal must not suppress the other.
- No authoritative source `.SchLib` / `.IntLib` pin map or source-wire primitive IDs were found in the scanned repository tree. Those mappings remain unresolved.
- Electrical membership is **NOT_ADJUDICATED**; KiCad read-back confirms serialization/read-back only, not electrical correctness.
- Neither algorithm branch is changed or promoted as overall winner. All analysis files remain isolated on this branch.

See [visual adjudication report](qi9-deepseek-manus-visual-adjudication.md) and [CSV register](qi9-deepseek-manus-disagreement-register.csv).
