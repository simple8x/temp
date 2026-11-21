# QI9 PDF-only multi-workstream audit v1

Source SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`

## Executive decision

**BLOCKED — no electrical connectivity is verified.** This report is a read-only forensic reconciliation of the existing PDF, vector, OCR, symbol-pin and event-port artifacts. It does not write a netlist or promote evidence.

## BC1 — PDF text/vector forensics

- PDF pages: 14; schematic pages audited: 5–11.
- Searchable text spans on schematic pages: 168.
- Text spans fully inside the schematic-body region `[35.0, 38.0, 810.0, 505.0]`: 0.
- Decoded PDF text-show operators: 154 TJ/Tj calls across the 7 pages.
- Vector drawing records: 68400; clipping operators observed: 90.
- Finding: extracted searchable text is in page header/title-frame regions; no text spans land inside the defined schematic-body region. This supports that diagram labels are not available through the ordinary text layer. It does not prove every diagram glyph is a vector outline; that requires operator-level glyph/path classification.
- Consequence: OCR and vector geometry must be reconciled; do not assume a text extraction pass can recover the diagram labels.

## BC2 — Vector event/port geometry

- Candidate source segments: 1762.
- Geometric events: 2256.
- Incidence ports: 3701; stroke fragments: 1939.
- Pairwise geometric intersection/overlap records: 2263.
- Every event remains geometric-only; electrical adjacency = 0 and verified connectivity = 0 across all pages.
- Existing v2 representation correctly keeps per-segment incidence ports separate at geometric coincidence. A downstream consumer must not union ports merely because they share an event coordinate.

## BC3 — OCR labels and wire proximity

- OCR observations: 437 (128 possible net-label tokens; 309 reference-like tokens).
- NET_LABEL_CANDIDATE: 128 observations; proximity bands `{'NO_CLOSE_WIRE_CANDIDATE': 124, 'NEAR_WIRE_GEOMETRY_CANDIDATE': 4}`; <2 pt=1; <5 pt=20; all UNKNOWN=True.
- REFERENCE_CANDIDATE: 309 observations; proximity bands `{'NO_CLOSE_WIRE_CANDIDATE': 309}`; <2 pt=0; <5 pt=7; all UNKNOWN=True.
- OCR PSM6/11 are same-engine correlated observations. Regex token class and nearest-wire distance are not semantic label recognition or attachment evidence.

## BC4 — Symbol/reference/pin identity

- Symbol-body clusters matched: 93.
- Pin positions: {'CONFIRMED': 197, 'CANDIDATE': 17}; pin-number identity: {'AMBIGUOUS': 190, 'CONFIRMED': 24}.
- Candidate pin-to-wire edges: 197 (not promoted).
- Ambiguity sites: 118; unresolved terminal-contact records: 1337; candidate dots: 202 (193 geometrically attached).
- Per-page ambiguity / unresolved-contact counts: p5=29/202; p6=3/156; p7=3/110; p8=4/94; p9=1/202; p10=52/337; p11=26/236 (ambiguity / unresolved-contact records).
- Reference mapping summary: `{'references_mapped': 174, 'nearest_to_symbol_matched_cluster': 85, 'distinct_symbol_matched_clusters_with_reference_candidates': 81, 'reference_candidates_per_matched_cluster_gt1': 4}`.
- Reference collisions: `[{'page': 6, 'cluster_id': 14, 'references': ['C13', 'C15'], 'status': 'CONFLICT'}, {'page': 6, 'cluster_id': 37, 'references': ['C16', 'R18'], 'status': 'CONFLICT'}, {'page': 5, 'cluster_id': 4, 'references': ['C3', 'U3'], 'status': 'CONFLICT'}, {'page': 11, 'cluster_id': 57, 'references': ['Q3', 'Q5'], 'status': 'CONFLICT'}]`.
- A confirmed pin position is not a confirmed pin number, reference identity, or electrical connection. External KiCad 7.0.11 symbols remain templates only. The saved artifact supplies aggregate counts, not a complete stable-ID adjudication queue for all 118 sites / 1,337 contact records.

## BC5 — Page 11 reference audit

- Candidate mappings: `{'Q2': 27, 'Q3': 57, 'Q5': 58, 'Q6': 59, 'Q7': 60, 'Q8': 24}`.
- Conflicts: `[{'label': 'Q3/Q5', 'cluster_id': 57, 'reason': 'prior OCR pass reads Q3 at this label position; another pass reads Q5 at the same position'}, {'label': 'Q5', 'cluster_ids': [57, 58], 'reason': 'Q5 is observed at both the Q3 candidate position and a second position near cluster 58'}]`.
- Missing labels: `['Q4', 'Q9']`.
- These are OCR-centroid-to-body-cluster candidates, not accepted reference bindings.

## Release gate

- Status: **BLOCKED**.
- VERIFIED_CONNECTED: 0; VERIFIED_SEPARATE: 0; verified net memberships: 0.
- No solver, net writer, or promotion was executed in this audit.
- Blockers:
  - `PDF_DIAGRAM_TEXT_NOT_EXPOSED_AS_SEARCHABLE_TEXT`
  - `SYMBOL_REFERENCE_PIN_IDENTITY_INCOMPLETE`
  - `GEOMETRIC_INTERSECTIONS_NOT_ELECTRICAL_JUNCTIONS`
  - `LABEL_ATTACHMENT_NOT_PROVEN`
  - `CROSS_SHEET_LINKS_NOT_RESOLVED`
  - `NO_INDEPENDENT_MANUALLY_ADJUDICATED_GROUND_TRUTH`

## Next executable work

1. Build PDF operator/path lineage for all glyph-like vector groups, preserving clipping transforms and exact source xrefs.
2. Create a source-evidence object model that links OCR tokens, vector glyph candidates, wire segments, symbol bodies and terminals without joining them.
3. Produce a human-review queue for all ambiguity sites, OCR/reference collisions, and unresolved terminal contacts; include cropped source evidence and deterministic IDs.
4. Add an independent adjudicated corpus before any connectivity resolver or KiCad net writer is enabled.

**Authority:** all connectivity remains UNKNOWN or CONFLICT. CI, parser success, geometric coincidence, OCR confidence and symbol-template matching are not electrical proof.