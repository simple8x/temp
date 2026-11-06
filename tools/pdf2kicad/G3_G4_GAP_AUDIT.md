# QI9-2604-A01 G3/G4 gap audit

Audit target: `tools/pdf2kicad/`, branch `fix/pdf2kicad-g3-g4-hardening`.

Evidence used:
- Authoritative PDF hash asserted by the regression fixture:
  `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`.
- GitHub Actions run: [42180879933](https://github.com/simple8x/temp/actions/runs/42180879933).
- Combined evidence artifact: [qi9-g3-g4-parser-evidence](https://github.com/simple8x/temp/actions/runs/42180879933/artifacts/13485491212).
- The run completed successfully: tests, compile, junction extraction, real-PDF CLI extraction for pages 5-11, and artifact upload all succeeded.

## Six-gate gap table

| Gate | Existing code/evidence | Gap | Status |
|---|---|---|---|
| G3-1 Source integrity | Workflow checks out the authoritative PDF; regression asserts its SHA-256; page-level extraction is limited to pages 5-11 | The source branch is still a mutable Git ref; promote the source SHA into an explicit workflow input/manifest and retain the hash in every page artifact | PARTIAL |
| G3-2 Component inventory | `assign()`, `values()`, `parts.csv`; README records 209/209 references and 205/209 values from hand verification | No checked-in machine-readable reference inventory is compared on every run; the README's aggregate claim is not a current per-component reconciliation report | PARTIAL |
| G3-3 Pin recovery | `pin_tokens()`, conservative coordinate fallback, `pin_status.csv`, new full-terminal inventory | No raster/template recovery; no independently checked pin map; source-PDF run reports 69 open terminals and 0 explicit NC terminals across pages 5-11; 24-token USB-C test is synthetic | BLOCKED |
| G4-1 Connectivity | `Topology`, `NL.nets()`, `netlist.csv`; new `connectivity_evidence.json` traces each parser-derived membership to wire primitive indices and coordinates | Parser decisions are not independently verified; three page-7 junction marks remain unresolved; some attached topology terminals are not yet established as identified component pins | PARTIAL |
| G4-2 Netlist reconciliation | Parser-derived netlist and pin identity outputs exist | No independently derived reference netlist, no exact `(ref,pin)->net` reconciliation, and no measured connectivity precision/recall | NOT IMPLEMENTED |
| G4-3 KiCad validation | Project-generation tooling exists | No verified KiCad CLI/ERC run or complete visual electrical sign-off for this source PDF | NOT IMPLEMENTED |

## Real-PDF parser inventory

Counts below are summed from the seven generated `connectivity_evidence.json` page reports. They describe parser output, not electrical truth.

| Page | Parser-derived pin/net memberships | Page-net instances | Topology terminals | Attached | Open unresolved | Explicit NC |
|---:|---:|---:|---:|---:|---:|---:|
| 5 | 122 | 41 | 241 | 216 | 25 | 0 |
| 6 | 93 | 37 | 169 | 166 | 3 | 0 |
| 7 | 82 | 28 | 165 | 154 | 11 | 0 |
| 8 | 62 | 27 | 152 | 152 | 0 | 0 |
| 9 | 59 | 44 | 187 | 183 | 4 | 0 |
| 10 | 183 | 53 | 436 | 422 | 14 | 0 |
| 11 | 167 | 56 | 348 | 336 | 12 | 0 |
| **Total** | **768** | **286** | **1,698** | **1,629** | **69** | **0** |

Important interpretation:
- 768 means parser-produced component-pin/net rows with primitive lineage. It does **not** mean 768 independently verified electrical memberships.
- 286 is the sum of page-local net counts; the same logical net may occur on multiple pages, so it is not a project-wide unique-net count.
- The 69 open terminals remain unresolved. Do not promote them to NC.
- Zero detected explicit NC marks is a detection result, not proof that the source has no NC marks.
- Independently verified memberships remain **0**.
- Junction evidence remains 191 confirmed out of 194 candidates; 3 candidates on page 7 remain unresolved.

## Next implementation order

1. Fix and validate explicit no-connect mark detection against source-PDF crops; preserve all 69 unresolved endpoints until evidence assigns them.
2. Establish a reviewed reference inventory for all component pins and net memberships. Each reference row must cite PDF page, coordinates/crop, and reviewer decision.
3. Compare parser output against that reference at exact `(reference, pin identity, net)` granularity. Report false positives, false negatives, unknown identities, and conflicts separately.
4. Add page-to-page label and hierarchical-port reconciliation before merging page netlists.
5. Generate the KiCad project only from reconciled data; run the supported KiCad CLI ERC and preserve its report.
6. Keep G3/G4 BLOCKED until all material unknowns and mismatches are resolved or explicitly dispositioned.

## Status rule

A successful CI run proves that the tested code and extraction commands completed. It does not prove electrical correctness. This audit therefore records G3/G4 as **BLOCKED** until independent netlist reconciliation and KiCad validation are implemented.


## NC stub reconciliation update (2026-11-06)

The source-PDF vector pass now detects 28 explicit X marks across pages 5, 6, 8, and 9. Each mark is within 0.085 pt of a wire terminal in the generated evidence. The NC terminals are associated to referenced components by unique directional geometry (28/28 currently assigned in the parser report). For the page-5 USB-C connector, the associated reference is J2.

The NC marker and the opposite symbol-body endpoint can be two geometric endpoints of the same pin stub. A regression now collapses a one-NC/one-body-end stub to one physical NC pin and suppresses the duplicate body-end terminal from net membership. Branched or multiple-mark cases are recorded as NC_STUB_TOPOLOGY_CONFLICT and fail closed.

**Pin-number recovery is still not verified.** The source-PDF page-5 test currently fails its requirement that all 10 J2 NC marks map to printed pin tokens. The vector words A1-A12 and B1-B12 are decoded, but the existing mutual-nearest pin-token matcher has not yet assigned them to those NC terminals. Do not infer the pin number from proximity alone.

The current six-gate status remains G3/G4 BLOCKED. Parser-derived net memberships are not independent verification, and KiCad ERC/reference-netlist reconciliation are still outstanding.
