# Test result

- Source PDF SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`
- G0: 14/14 pages extracted; 1,052 words; 756 text spans; 68,760 drawing paths; 71,148 line primitives; 6 image references.
- G1: pages 5–11 all extracted; 71,935 vector drawing items serialized.
- G2: OCR executed on 7/7 sheets at 200 DPI; 876 word candidates serialized with pixel bounding boxes and raw confidence values.
- Determinism check: two separate extraction runs produced identical pipeline manifests (`true`).
- Unit tests: 7 passed / 7 run. Tests cover endpoint tolerance, orthogonal crossing detection, conservative crossing-vs-junction behavior, explicit junction, shared endpoint candidate, and diagonal rejection.
- Visual spot-check: page 6 (RS232) raster inspected; symbols and text are legible in the rendered sheet. This is a spot-check, not full-sheet semantic verification.
- KiCad CLI: not installed/available in this environment; no KiCad parse/ERC test performed.

## Gate decision
G0 PASS; G1 PASS; G2 PASS for candidate extraction only; G3/G4/G5 BLOCKED. Overall release remains `BLOCKED_PENDING_G3_G4_G5`.
