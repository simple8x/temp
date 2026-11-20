# IngestLab absorption audit (2026-11-20)

## Decision

**ABSORB WITH ADAPTER; do not vendor or trust circuit output as electrical truth.**

Audited upstream: https://github.com/ingestlab-dev/ingestlab
Upstream revision observed: `73f530c7415b815a8b6297ca24aad65a1351ca11` (release v1.1.3).
Local baseline: branch `claude`, commit `b975692acdd2b002f44f80dc906df2943b0c1bfe`.

This is a source review, not an independent execution or reproducibility claim. No upstream package was installed, no upstream tests/benchmarks were run, and no QI9 result was generated in this audit.

## Findings

### Reusable ideas

- PDF.js-based document ingestion and layout extraction can complement ECCI's vector-first pipeline.
- Structured page/layout/text/table outputs can provide candidate labels, references, values and page anchors.
- The upstream circuit module demonstrates a bounded, heuristic op-amp/resistor/ground recognizer and a wire-component grouping pass.
- Its LM358 regression assertions are useful as candidate fixtures, not as a general connectivity oracle.

### Material limitations found in source

The reviewed upstream `src/circuits.js`:
- filters to axis-aligned strokes for its wire graph;
- uses geometric proximity and endpoint/junction tests to union wire segments;
- recognizes a narrow set of symbol shapes (op-amp, resistor, ground);
- associates nearby words with symbols/nets using distance thresholds;
- emits partial circuit reconstructions and generated net names.

These are useful extraction heuristics, not a general PDF schematic topology solver. The source does not establish full support for arbitrary symbols, rotated/curved wires, buses, hierarchical sheets, cross-sheet labels, power symbols, or all junction conventions.

The upstream corpus test asserts exact outputs for two LM358 datasheets. That is a useful regression, but does not establish general precision/recall. A test that skips because an external fixture is absent must remain UNKNOWN, not PASS.

## Fit with current ECCI

The current ECCI pipeline is already vector-first: `pikepdf` content-stream interpretation, normalized page geometry, transformed path primitives and operator provenance. It explicitly reports unsupported PDF features (including text-show operators and clipping not applied). The optional PyMuPDF cross-check is an independent extractor, but its own tests document that agreement does not prove universal correctness.

Therefore, IngestLab should be an **optional text/layout sidecar**, not a replacement for ECCI vector ingestion or arrangement/topology logic.

## Integration boundary

1. Preserve original PDF bytes and SHA-256 before any parsing.
2. Run IngestLab in an isolated, pinned, optional adapter process; no network fetches during parsing.
3. Convert outputs into a neutral evidence sidecar. Every item must retain source page, bounding box, extractor/version, source digest and raw payload digest.
4. Text-derived reference/value/net-label candidates may help locate evidence, but may not create or merge electrical graph edges.
5. ECCI remains authoritative for vector geometry, candidate electrical edges and classification.
6. Any disagreement between text/layout and vector evidence is recorded as CONFLICT/UNKNOWN and blocks promotion.
7. No adapter output may transition UNKNOWN/INFERRED to VERIFIED. VERIFIED still requires the existing independent evidence rules and writer re-audit.

## Security and supply-chain gates

Before enabling the adapter in CI or production:
- pin exact upstream release and resolved transitive dependency lock;
- inspect license and all transitive packages;
- disable runtime network access and remote model/API calls;
- enforce input byte, page, object, time, memory and output-size limits;
- run untrusted PDF parsing in a sandbox with no credentials or writable repository mount;
- reject malformed, non-finite, out-of-page or unbounded coordinates;
- record parser errors/unsupported features; partial extraction cannot be reported as complete;
- verify deterministic output or explicitly record nondeterministic fields.

No dependency or runtime integration is added by this audit.

## Required validation before absorption

- Unit tests for page index, bbox normalization, coordinate convention, source digest binding and malformed outputs.
- Golden text/layout fixtures from the actual QI9 source with manually reviewed expected spans.
- Adversarial PDF corpus: malformed xref, object streams, huge page count, deeply nested forms, embedded files, encrypted PDFs, clipping, rotated/cropped pages, duplicate/overlapping text.
- Differential comparison against ECCI/PyMuPDF where semantics overlap; disagreement remains visible.
- Mutation tests proving that dropping page, bbox, source hash or extractor identity fails closed.
- Explicit skip accounting: missing fixture/dependency => UNKNOWN and CI gate failure for required suites.

## Disposition

| Capability | Decision | Reason |
|---|---|---|
| Text/layout/table extraction | ADAPT | Useful candidate evidence; retain page/bbox provenance |
| OCR | DEFER | Need a separate accuracy/security benchmark |
| RAG chunking | REJECT as authority | Retrieval chunks are not electrical evidence |
| Circuit recognizer | REFERENCE ONLY | Narrow heuristic; not a topology authority |
| PDF vector ingestion | KEEP ECCI | Existing provenance-aware vector pipeline |
| Upstream package vendoring | REJECT for now | No dependency/security/reproducibility gate completed |

## Honest status

- Source review: COMPLETE for the files listed in this audit.
- Independent upstream test execution: NOT RUN.
- Dependency/security audit: NOT RUN.
- QI9 integration: NOT IMPLEMENTED.
- QI9 electrical connectivity promoted by this work: NONE.
