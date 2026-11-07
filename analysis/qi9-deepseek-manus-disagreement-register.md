# QI9 PDF→KiCad — DeepSeek vs Manus disagreement register

- Repository: `simple8x/temp`
- DeepSeek run: https://github.com/simple8x/temp/actions/runs/42358860044
- Manus run: https://github.com/simple8x/temp/actions/runs/42358853104
- PDF SHA-256 (both artifacts): `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`
- Scope: PDF pages 5–11.
- Source: branch-specific `graph_netlist_candidate.csv` and `reference_connectivity_review.csv` from the two workflow artifacts.

Rows in register: **27**.

## Interpretation / limits

- This is a disagreement register, not a ground-truth verdict.
- Net labels are diagnostic only; neither branch is promoted as correct.
- `source_wire_primitive_ids` are attached only where an exact page/ref/net/coordinate match was found.
- Every case remains `UNVERIFIED_SOURCE_PDF_AND_SYMBOL_LIBRARY_REQUIRED`; verify against the original PDF crop and the correct symbol/pin definition.
- KiCad read-back confirms serialization/read-back only, not electrical correctness.
- No changes are made to either algorithm branch. This register is isolated on its own analysis branch.
