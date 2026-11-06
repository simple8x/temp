# PDF2KiCad workflow

## 1. Source inspection
- Hash the source PDF and enumerate pages.
- Record page size, rotation, metadata, fonts, vector operators, image regions, and extracted text.
- Classify each page/region as vector, raster, or mixed.
- For native vector PDFs, parse vector paths and text operators first. OCR is reserved for raster-only or justified regions.
- Record original coordinate frame and every transform before normalization.

## 2. Evidence-preserving extraction
For each primitive retain source hash, page, object/operator ID, type, raw coordinates, bounding box, stroke/fill, line width, font/glyph/text, transform, units, extractor/version, confidence, and ambiguity.
Never discard raw geometry after deriving higher-level objects.

## 3. Symbol and pin recovery
- Detect symbol body, reference, value, pin number/name/type, and pin endpoint independently.
- Match against verified symbol libraries using body geometry, text, pin count/positions, orientation, and library ID/version.
- Apply rotation/mirroring/offset transforms explicitly.
- Preserve alternate glyph readings (0/O, 1/I, 5/S, 8/B); do not resolve without evidence.
- Require endpoint/topology evidence for pin-wire connection; proximity alone is insufficient.

## 4. Connectivity reconstruction
- Separate electrical wires from graphics, symbol outlines, buses, page frames, and no-connect markers.
- Detect endpoints, T-junctions, multi-way junctions, endpoint-on-segment, crossings, overlaps, gaps, and near misses.
- A crossing connects only with explicit junction or equivalent source-semantic evidence.
- Derive tolerance from source resolution/grid and validate against known anchors. Do not widen it to force a connection.
- Apply local/global/hierarchical label scope and power-symbol semantics.
- Preserve unnamed nets, duplicate labels, and no-connect evidence.
- Keep a graph of pin-wire, wire-junction, wire-label, and wire-wire edges.
- If multiple topologies remain plausible, retain candidates and mark affected nets UNKNOWN.

## 5. KiCad generation
- Check installed KiCad version and supported format.
- Generate only requested outputs. A schematic/netlist task must not emit a PCB board.
- Preserve stable IDs when editing an existing project.
- Keep raw source, intermediate IR, generated files, and reports separate.
- Parse generated files with the actual KiCad toolchain; file existence is not validation.
- Run ERC and netlist export where available. Preserve all warnings/errors.

## 6. Independent verification / G3-G4
Compare source-derived expectations to KiCad output:
- component/reference coverage and values;
- symbol pin count, numbers, and endpoints;
- wire segment and junction topology;
- crossings and labels/power scope;
- no-connect markers;
- pin-to-net membership;
- unintended shorts and opens;
- ERC results and independently parsed netlist.

Report each discrepancy with ID, page/object evidence, expected value, reconstructed value, independent check, status, and confidence.

Status:
- PASS only if every required electrical criterion is evidenced and no critical discrepancy remains.
- FAIL if a defect is demonstrated.
- BLOCKED if required tools/input/permission are unavailable.
- UNKNOWN if source evidence cannot decide.

Do not equate successful parsing, project opening, netlist export, or unit tests with electrical correctness.
