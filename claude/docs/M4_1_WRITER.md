# M4.1 — connectivity writer (`ecci.connectivity`, schema 1) and F2 arm provenance

## What it is
`ecci/writer.py` serializes one solved page (`ConnGraph` + `SolveResult`, plus optionally the `BuildResult`,
`PageIngest`, pin clusters and evidence registry of `rules.analyze_page`) into canonical JSON, and reads it back.

- `build_document(graph, res, built=, page=, clusters=, registry=, source=) -> dict`
- `dumps(...) -> str` (canonical JSON: sorted keys, compact separators, ASCII, no NaN, trailing newline)
- `loads(text) -> dict` (fail closed: `DocumentError(problems)`), `validate(doc) -> [problems]`,
  `pair_status(doc, p, q)` (= `SolveResult.pair_status` from the file alone), `reserialize(doc)`.

Not in this batch: the `.kicad_sch` / KiCad netlist writer and the kicad-cli V-W round trip from HANDOFF "Next 1".
This writer is the auditable intermediate those exporters should consume.

## Schema (top level)
| key | content |
|---|---|
| `format`, `schema_version` | `"ecci.connectivity"`, `1` |
| `status_vocabulary` | internal -> file mapping; identity `VERIFIED/INFERRED/UNKNOWN/CONFLICT`, checked on read |
| `coordinates` | normalized page space, PDF pt x UserUnit, origin top-left of visible page, x right, y down |
| `source` | `document` (caller-supplied identity, e.g. name/sha256, or null), `page_index`, `page_size` |
| `primitives` | id, content_ref, page, source, chain, first_op, bbox of every referenced PDF primitive |
| `evidence` | EvidenceItems named by roles / pins, closed over `derived_from` |
| `objects` | id, kind, attach, prims, pin detail (tip, base, numbers, states, evidence), dot detail |
| `edges` | every candidate edge (pipeline + solver TR6): rule, rule_kind, origin, roles (obj, kind, status, evidence, weak), coord, n_prims, negative, prov, `classified` (classify_edge), final `status`/`reason`, `provenance {complete, missing, refs}` |
| `site_links` | links committed by site resolution: always INFERRED, origin `solver.site_resolution`, arm provenance |
| `attachments` | UnresolvedTerminal -> wire, `electrical: false` |
| `sites` | kind, arms (label, obj, F2 `geometry`), options, fixed, result |
| `nets`, `quarantined`, `connectivity` | solver nets; quarantine records; per object `net` / `electrical_group` / `verified_group` |
| `constraints` | cannot_link, names, no_connect |
| `unsupported`, `notes`, `warnings` | from the builder / solver, verbatim |
| `unresolved_references` | evidence ids, prov refs, primitive ids, objects without primitives, sites without arm geometry |
| `solver_digest`, `content_sha256` | opaque informational `SolveResult.digest()` token; `content_sha256` hashes the canonical document without itself, including `solver_digest` |

Example (crossing of two wires, trimmed): a FOURWAY site whose arms are `E w:3 / N w:4 / S w:4 / W w:3`, each
with its own `graph_edge` (6, 4, 5, 3), `vector`, `direction` and `prov`; domain collapses to `ALL4 {(w:3,w:4)}` /
`NONE`; result `UNKNOWN AMBIGUOUS_2_CONSISTENT_ASSIGNMENTS`; no site link; `pair_status(w:3, w:4) = UNKNOWN`.

## Why a forged file cannot pass (independent of the hash)
The reader reconstructs `ConnGraph` from serialized objects, pipeline edge premises, site domains and constraints,
then calls the production solver again. It compares every site result, every edge status/reason and the exact
committed site-link set against that fresh solve; it does not derive expected links from the document's claimed
`site.result`. It separately re-runs `classify_edge` / `audit_verified`, recomputes provenance, and recomputes
the net / electrical / verified partitions from edges, site links and attachments. Constraints are schema-checked
before they become solver inputs. Duplicate JSON keys, unknown or missing keys, non-canonical text and NaN are
rejected.

A serialized `fixed` site choice is an assumption, not something a reader can authenticate from the same
self-contained document. Therefore the writer and reader reject non-null `fixed` values. To accept such a choice
in a future version it must be authorized by a separately trusted, externally verified record (not by rehashing
this JSON). The finite option domain remains an explicit serialized premise; this recomputation proves the result
follows from that domain and the other serialized premises, not that the domain itself is authentic PDF evidence.

Missing provenance never promotes: the record stays, its `provenance.complete` is false with the reasons listed,
and its status is what the solver gave (e.g. a SOUND edge without prov is INFERRED/MISSING_PROVENANCE).

## F2 — independent geometric provenance of site arms
Finding (baseline c5f7cdf): `Site.arms` was `(label, obj)` only. The arrangement node, graph edge, direction
vector and PDF operators of each arm were discarded in `rules.build_conn_graph`; with overlapping wire primitives on
one arm edge only `min(prim_id)` survived, silently. A site link (an electrical assertion) therefore could not be
traced to its geometry, so this was a writer-integrity dependency and was fixed here.

Fix (backward compatible, solver and digest unchanged): `BuildResult.site_arms[site_id] = (ArmRecord, ...)` in the
order of `Site.arms` with `node, graph_edge, other_node, prim_ids (all covering prims), chosen_prim, direction,
vector, prov`. `rules.audit_site_arms` and the reader check: missing records, label/object mismatch, arms not at the
site node, one graph edge on two arms, contradictory FOURWAY directions, direction not fitting the vector, an arm
object that is not one of its edge's primitives / unrelated to the object's primitives, missing arm prov. Two arms
of one wire (straight through a crossing) are legal as long as they are distinct graph edges.

## Tests / mutations
`tests/unit/test_writer.py` (21 tests): status preservation x4 with promotion attempts, missing provenance,
dangling refs, duplicate ids, determinism under shuffled input, write->read->write identity + all-pairs semantic
equality, two-wire crossing arms, F2 audit, committed site links, nothing dropped, writer refusal, 15 malformed /
tampered inputs, end to end on `lib_min.kicad_sym` + generated PDFs (TR1, TR2+dot, TR3 pins, crossing, T-junction).
Mutations M137-M143 (reader promotion / connectivity / hash / site-link / provenance checks, arm prov, arm audit).


## Independent adversarial regressions added after review
- A multi-arm object with one valid geometry and one missing geometry must produce `provenance.complete=false`.
- Writer serialization rejects duplicate `(label, object)` records and incomplete/mismatched arm inventories before any dictionary indexing.
- Reader rejects a site-link record that still claims complete provenance after one participating arm geometry is removed.
- Reader rebuilds the graph and reruns `solve()`; forged site status/option, `fixed` assumptions and extra site links are rejected even after the content hash is recomputed. Mutations M144-M146 disable each of these checks.
- Non-null `fixed` is rejected at the writer/reader boundary until an external authorization mechanism exists.
- `solver_digest` is intentionally opaque/informational; only accidental edits are caught by the content hash. It is not independently authenticated.
