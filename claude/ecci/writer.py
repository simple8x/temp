"""M4.1 writer: deterministic, auditable serialization of a solver result (`ecci.connectivity`, schema 1).

The writer invents nothing. Every status in the file is the one `classify_edge` / `solve` produced; the reader
re-derives every one of them from the premise records in the file and rejects the document if a status, a
connectivity group or a provenance block does not follow from what the file itself contains. So a document whose
INFERRED edge was edited to VERIFIED, whose provenance was dropped, or whose net was merged by hand fails closed
even if its content hash was recomputed.

Layout (all lists sorted, all keys sorted, no timestamps, no random ids):
  format / schema_version / status_vocabulary / coordinates / source
  primitives   PDF operators behind objects, edges and site arms (Prov, I8)
  evidence     the EvidenceItems that role / pin evidence ids name (closed over derived_from)
  objects      WIRE / PIN / DOT / LABEL / UNRESOLVED, their primitives, pin and dot details
  edges        every candidate edge (pipeline + solver TR6): premises, classify result, final status, provenance
  site_links   links committed by resolving an ambiguity site (INFERRED only), with arm provenance
  attachments  structural membership (UnresolvedTerminal -> wire). NOT electrical
  sites        arms (+ F2 arm provenance), finite domain, result
  nets, quarantined, connectivity (net / electrical group / verified group of every object), constraints
  unsupported / notes / warnings / unresolved_references
  solver_digest (SolveResult.digest()), content_sha256 (of everything else)

Status mapping internal -> file is the identity (STATUS_MAP); it is written into the file and checked on read.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from .solver import ConnGraph, SolveResult, _name_edges, audit_result
from .status import (CONFLICT, CONVENTIONAL, INFERRED, RULES, SOUND, UNKNOWN, VERIFIED, Edge, RoleRef,
                     audit_verified, classify_edge)

FORMAT = "ecci.connectivity"
SCHEMA_VERSION = 1
STATUS_MAP = {VERIFIED: "VERIFIED", INFERRED: "INFERRED", UNKNOWN: "UNKNOWN", CONFLICT: "CONFLICT"}
STATUS_UNMAP = {v: k for k, v in STATUS_MAP.items()}
COORDINATES = {
    "space": "normalized page space (ecci.transform)",
    "unit": "PDF point (1/72 in) x /UserUnit",
    "origin": "top-left corner of the visible page (CropBox intersect MediaBox, after /Rotate)",
    "x_axis": "right",
    "y_axis": "down",
}
ORIGIN_PIPELINE = "rules.build_conn_graph"
ORIGIN_TR6 = "solver.name_edges"
ORIGIN_SITE = "solver.site_resolution"
ORIGIN_ATTACH = "rules.build_conn_graph:attach"

TOP_KEYS = frozenset({
    "format", "schema_version", "status_vocabulary", "coordinates", "source", "primitives", "evidence",
    "objects", "edges", "site_links", "attachments", "sites", "nets", "quarantined", "connectivity",
    "constraints", "unsupported", "notes", "warnings", "unresolved_references", "solver_digest",
    "content_sha256"})
KEYS = {
    "primitive": {"id", "content_ref", "page", "source", "chain", "first_op", "bbox"},
    "evidence": {"id", "kind", "source", "lineage", "derived_from", "uses_connectivity"},
    "object": {"id", "kind", "attach", "prims", "pin", "dot"},
    "edge": {"id", "a", "b", "rule", "rule_kind", "origin", "roles", "coord", "n_prims", "negative", "prov",
             "classified", "status", "reason", "provenance"},
    "role": {"obj", "kind", "status", "evidence", "weak"},
    "provenance": {"complete", "missing", "refs"},
    "site_link": {"id", "site", "option", "a", "b", "status", "origin", "provenance"},
    "attachment": {"id", "object", "attach", "electrical", "origin"},
    "site": {"id", "kind", "arms", "options", "fixed", "result"},
    "arm": {"label", "obj", "geometry"},
    "arm_geometry": {"node", "graph_edge", "other_node", "prim_ids", "chosen_prim", "direction", "vector", "prov"},
    "net": {"id", "objects", "status", "closure", "open_reasons", "warnings"},
    "conn": {"object", "net", "electrical_group", "verified_group"},
}


class WriterError(ValueError):
    """The solver result cannot be serialized without dropping or inventing something."""


class DocumentError(ValueError):
    """A document failed validation. `problems` lists every reason."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:20]) + (f" (+{len(problems) - 20} more)" if len(problems) > 20 else ""))
        self.problems = problems


# ----------------------------------------------------------------------------------------------- helpers
def canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"


def content_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def _components(objs, edges) -> dict:
    """Same union rule as solver._components (representative = smallest id). Re-implemented on purpose."""
    parent = {o: o for o in objs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {o: find(o) for o in objs}


def _role_dict(r: RoleRef) -> dict:
    return {"obj": r.obj, "kind": r.kind, "status": r.status, "evidence": list(r.evidence), "weak": r.weak}


def _edge_from_record(rec: dict) -> Edge:
    roles = tuple(RoleRef(r["obj"], r["kind"], r["status"], tuple(r["evidence"]), r["weak"]) for r in rec["roles"])
    return Edge(rec["id"], rec["a"], rec["b"], rec["rule"], roles, rec["coord"], rec["n_prims"],
                tuple(rec["negative"]), tuple(rec["prov"]))


def _edge_provenance(prov, ev_ids, prim_refs: set, ev_known: set) -> dict:
    missing = []
    if not prov:
        missing.append("NO_PROV_RECORD")
    missing += [f"UNRESOLVED_PROV:{p}" for p in sorted(set(prov)) if p not in prim_refs]
    missing += [f"UNRESOLVED_EVIDENCE:{e}" for e in sorted(set(ev_ids)) if e not in ev_known]
    return {"complete": not missing, "missing": missing, "refs": sorted(set(prov))}


def _link_provenance(site: dict | None, a: str, b: str, prim_refs: set) -> dict:
    missing, refs = [], set()
    if site is None:
        return {"complete": False, "missing": ["UNKNOWN_SITE"], "refs": []}
    for o in (a, b):
        arms = [arm for arm in site["arms"] if arm["obj"] == o]
        geo = [arm["geometry"] for arm in arms if arm["geometry"] is not None]
        if not geo:
            missing.append(f"NO_ARM_PROVENANCE:{o}")
        for g in geo:
            refs |= set(g["prov"])
    missing += [f"UNRESOLVED_PROV:{p}" for p in sorted(refs) if p not in prim_refs]
    return {"complete": not missing, "missing": sorted(missing), "refs": sorted(refs)}


def _vgroups(objs, V, Q) -> dict:
    rep = _components(objs, V)
    mem = defaultdict(set)
    for o, r in rep.items():
        mem[r].add(o)
    return {o: r for r, m in mem.items() if len(m) > 1 and not (m & Q) for o in m}


# ----------------------------------------------------------------------------------------------- writer
def build_document(graph: ConnGraph, res: SolveResult, *, built=None, page=None, clusters=None, registry=None,
                   source: dict | None = None) -> dict:
    """Serialize one solved page. `built` (rules.BuildResult), `page` (ir.PageIngest), `clusters` and `registry`
    (classify registry) add provenance; without them the records are kept and their provenance is marked
    incomplete. Raises WriterError instead of dropping anything the result contains."""
    probs = audit_result(graph, res)
    if probs:
        raise WriterError("solver result fails audit_result: " + "; ".join(probs[:5]))
    edges = list(graph.edges) + _name_edges(graph)
    origin = {e.id: ORIGIN_PIPELINE for e in graph.edges}
    origin.update({e.id: ORIGIN_TR6 for e in _name_edges(graph)})
    if len({e.id for e in edges}) != len(edges):
        raise WriterError("duplicate edge id between pipeline and solver edges")
    if set(res.edge_status) != {e.id for e in edges}:
        raise WriterError("edge_status does not cover exactly the graph's edges: "
                          f"{sorted(set(res.edge_status) ^ {e.id for e in edges})[:5]}")
    for st, _ in res.edge_status.values():
        if st not in STATUS_MAP:
            raise WriterError(f"unknown edge status {st!r}")

    obj_prims = dict(getattr(built, "obj_prims", {}) or {})
    site_arms = dict(getattr(built, "site_arms", {}) or {})
    dots = dict(getattr(built, "dots", {}) or {})

    # ---- primitives (only those something refers to)
    prims_by_id = {p.id: p for p in (page.prims if page is not None else ())}
    by_ref = defaultdict(list)
    for p in prims_by_id.values():
        by_ref[p.prov.content_ref].append(p.id)
    want = set()
    for ps in obj_prims.values():
        want |= {int(i) for i in ps}
    for recs in site_arms.values():
        for r in recs:
            want |= set(r.prim_ids)
    for e in edges:
        for ref in e.prov:
            want |= set(by_ref.get(ref, ()))
    primitives = []
    for i in sorted(want):
        p = prims_by_id.get(i)
        if p is None:
            continue
        pv = p.prov
        primitives.append({"id": i, "content_ref": pv.content_ref, "page": pv.page, "source": pv.source,
                           "chain": [[a, b] for a, b in pv.chain], "first_op": pv.first_op,
                           "bbox": [float(x) for x in pv.bbox]})
    prim_refs = {p["content_ref"] for p in primitives}
    prim_ids_known = {p["id"] for p in primitives}

    # ---- evidence closure
    items = getattr(registry, "items", {}) or {}
    pins_of: dict[str, object] = {}
    for cr in clusters or ():
        for k, pin in enumerate(cr.pins):
            pins_of[f"p:{cr.cluster.id}:{k}"] = pin
    seeds = {x for e in edges for r in e.roles for x in r.evidence}
    for pin in pins_of.values():
        seeds |= set(pin.evidence) | set(pin.independent_items)
    seen, stack = set(), sorted(seeds)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        it = items.get(cur)
        if it is not None:
            stack.extend(sorted(it.derived_from))
    evidence = [{"id": i, "kind": items[i].kind, "source": items[i].source, "lineage": sorted(items[i].lineage),
                 "derived_from": sorted(items[i].derived_from), "uses_connectivity": items[i].uses_connectivity}
                for i in sorted(seen) if i in items]
    ev_known = {x["id"] for x in evidence}

    # ---- objects
    objects = []
    for o in sorted(graph.objects, key=lambda o: o.id):
        pin = pins_of.get(o.id)
        d = dots.get(o.id)
        objects.append({
            "id": o.id, "kind": o.kind, "attach": o.attach,
            "prims": sorted(int(i) for i in obj_prims[o.id]) if o.id in obj_prims else None,
            "pin": None if pin is None else {
                "cluster": pin.cluster_id, "tip": [float(v) for v in pin.tip], "base": [float(v) for v in pin.base],
                "numbers": list(pin.numbers), "pos_state": pin.pos_state, "id_state": pin.id_state,
                "evidence": sorted(pin.evidence), "independent_items": sorted(pin.independent_items),
                "excluded": sorted([list(x) for x in pin.excluded])},
            "dot": None if d is None else {"centre": [float(v) for v in d[0]], "radius": float(d[1]), "node": d[2]},
        })

    # ---- edges
    erecs = []
    for e in sorted(edges, key=lambda e: e.id):
        cst, crs = classify_edge(e)
        st, rs = res.edge_status[e.id]
        erecs.append({
            "id": e.id, "a": e.a, "b": e.b, "rule": e.rule,
            "rule_kind": RULES[e.rule][0] if e.rule in RULES else None, "origin": origin[e.id],
            "roles": [_role_dict(r) for r in e.roles], "coord": e.coord, "n_prims": e.n_prims,
            "negative": list(e.negative), "prov": list(e.prov),
            "classified": [STATUS_MAP[cst], crs], "status": STATUS_MAP[st], "reason": rs,
            "provenance": _edge_provenance(e.prov, [x for r in e.roles for x in r.evidence], prim_refs, ev_known)})

    # ---- sites (F2 arm geometry)
    srecs = []
    for s in sorted(graph.sites, key=lambda s: s.id):
        recs = {(r.label, r.obj): r for r in site_arms.get(s.id, ())}
        arms = []
        for label, obj in s.arms:
            r = recs.get((label, obj))
            arms.append({"label": label, "obj": obj, "geometry": None if r is None else {
                "node": r.node, "graph_edge": r.graph_edge, "other_node": r.other_node,
                "prim_ids": list(r.prim_ids), "chosen_prim": r.chosen_prim, "direction": r.direction,
                "vector": [float(v) for v in r.vector], "prov": list(r.prov)}})
        sr = res.sites.get(s.id)
        if sr is None:
            raise WriterError(f"site {s.id} has no result")
        srecs.append({"id": s.id, "kind": s.kind, "arms": arms,
                      "options": [{"name": op.name, "links": sorted([list(l) for l in op.links])}
                                  for op in sorted(s.options, key=lambda op: op.name)],
                      "fixed": s.fixed,
                      "result": {"status": STATUS_MAP[sr.status], "option": sr.option, "reason": sr.reason}})
    if set(res.sites) != {s.id for s in graph.sites}:
        raise WriterError("site results do not match the graph's sites")
    site_by_id = {s["id"]: s for s in srecs}

    # ---- site links and attachments from the solver's final edge list
    known_e = {e.id for e in edges}
    links, attaches = [], []
    for eid, a, b in sorted(res.final_edges):
        if eid in known_e:
            continue
        if eid.startswith("site:"):
            # object ids contain ':', so the site is found by prefix, never by splitting the link id
            cands = [x for x in res.sites if eid.startswith(f"site:{x}:")]
            sid = max(cands, key=len) if cands else None
            sr = res.sites.get(sid)
            if sr is None or sr.status != INFERRED:
                raise WriterError(f"committed link {eid} has no INFERRED site")
            links.append({"id": eid, "site": sid, "option": sr.option, "a": a, "b": b,
                          "status": STATUS_MAP[INFERRED], "origin": ORIGIN_SITE,
                          "provenance": _link_provenance(site_by_id.get(sid), a, b, prim_refs)})
        elif eid.startswith("attach:"):
            attaches.append({"id": eid, "object": a, "attach": b, "electrical": False, "origin": ORIGIN_ATTACH})
        else:
            raise WriterError(f"final edge {eid} is of no known kind")

    # ---- nets, quarantine, connectivity
    nets = [{"id": n.id, "objects": sorted(n.objects), "status": n.status, "closure": n.closure,
             "open_reasons": sorted(n.open_reasons), "warnings": sorted(n.warnings)}
            for n in sorted(res.nets, key=lambda n: n.id)]
    quarantined = sorted(([sorted(o), sorted([[v.kind, sorted([[str(x) for x in p] for p in v.pairs]), v.detail]
                                              for v in vs])] for o, vs in res.quarantined), key=lambda t: t[0])
    conn = [{"object": o.id, "net": res._net_of[o.id], "electrical_group": res._elec_of.get(o.id, o.id),
             "verified_group": res._vgroup.get(o.id)} for o in sorted(graph.objects, key=lambda o: o.id)]
    cons = graph.constraints
    constraints = {
        "cannot_link": sorted([[a, b, why] for a, b, why in cons.cannot_link]),
        "names": sorted([{"object": o, "text": n.text, "scope": n.scope, "confirmed": n.confirmed}
                         for o, n in cons.names.items()], key=lambda d: d["object"]),
        "no_connect": sorted(cons.no_connect)}

    ev_refs = {x for e in erecs for r in e["roles"] for x in r["evidence"]}
    for ob in objects:
        if ob["pin"]:
            ev_refs |= set(ob["pin"]["evidence"]) | set(ob["pin"]["independent_items"])
    prov_refs = {x for e in erecs for x in e["prov"]}
    for s in srecs:
        for arm in s["arms"]:
            if arm["geometry"]:
                prov_refs |= set(arm["geometry"]["prov"])
    unresolved = {
        "evidence": sorted(ev_refs - ev_known),
        "provenance": sorted(prov_refs - prim_refs),
        "primitives": sorted({i for ob in objects for i in (ob["prims"] or ())} - prim_ids_known),
        "objects_without_primitives": [ob["id"] for ob in objects if ob["prims"] is None],
        "sites_without_arm_geometry": [s["id"] for s in srecs if any(a["geometry"] is None for a in s["arms"])],
    }
    geom = getattr(page, "geom", None)
    doc = {
        "format": FORMAT, "schema_version": SCHEMA_VERSION, "status_vocabulary": dict(STATUS_MAP),
        "coordinates": dict(COORDINATES),
        "source": {"document": dict(source) if source else None,
                   "page_index": getattr(geom, "page_index", None),
                   "page_size": [float(geom.width), float(geom.height)] if geom is not None else None},
        "primitives": primitives, "evidence": evidence, "objects": objects, "edges": erecs,
        "site_links": links, "attachments": attaches, "sites": srecs, "nets": nets, "quarantined": quarantined,
        "connectivity": conn, "constraints": constraints,
        "unsupported": sorted(getattr(built, "unsupported", []) or []),
        "notes": sorted(getattr(built, "notes", []) or []), "warnings": sorted(res.warnings),
        "unresolved_references": unresolved, "solver_digest": res.digest(),
    }
    doc["content_sha256"] = content_hash(doc)
    problems = validate(doc)
    if problems:                    # never hand out a document the reader would reject
        raise WriterError("writer produced an invalid document: " + "; ".join(problems[:5]))
    return doc


def dumps(graph: ConnGraph, res: SolveResult, **kw) -> str:
    return canonical(build_document(graph, res, **kw))


# ----------------------------------------------------------------------------------------------- reader
def _no_dup_keys(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise DocumentError([f"duplicate JSON key {k!r}"])
        d[k] = v
    return d


def _bad_constant(x):
    raise DocumentError([f"non-finite number {x}"])


def loads(text: str) -> dict:
    """Parse and validate. Raises DocumentError (fail closed) on any problem."""
    if not isinstance(text, str):
        raise DocumentError(["document is not text"])
    try:
        doc = json.loads(text, object_pairs_hook=_no_dup_keys, parse_constant=_bad_constant)
    except DocumentError:
        raise
    except ValueError as x:
        raise DocumentError([f"not JSON: {x}"]) from None
    problems = validate(doc)
    if problems:
        raise DocumentError(problems)
    if canonical(doc) != text:
        raise DocumentError(["document is not in canonical form (re-serialization differs)"])
    return doc


def pair_status(doc: dict, p: str, q: str) -> str:
    """SolveResult.pair_status, answered from a document alone."""
    c = {r["object"]: r for r in doc["connectivity"]}
    if p == q or p not in c or q not in c or c[p]["net"] != c[q]["net"]:
        return UNKNOWN
    qn = {c[o]["net"] for objs, _ in doc["quarantined"] for o in objs}
    if c[p]["net"] in qn:
        return CONFLICT
    if c[p]["electrical_group"] != c[q]["electrical_group"]:
        return UNKNOWN
    vp, vq = c[p]["verified_group"], c[q]["verified_group"]
    return VERIFIED if vp is not None and vp == vq else INFERRED


# ----------------------------------------------------------------------------------------------- validator
def _is_str(x) -> bool:
    return type(x) is str and x != ""


def _str_list(x) -> bool:
    return type(x) is list and all(type(v) is str for v in x)


def _num(x) -> bool:
    return type(x) in (int, float) and math.isfinite(x)


def _keys(rec, kind: str, where: str, out: list) -> bool:
    if type(rec) is not dict:
        out.append(f"{where}: not an object")
        return False
    want = KEYS[kind]
    if set(rec) != want:
        out.append(f"{where}: keys {sorted(set(rec) ^ want)} missing or unexpected")
        return False
    return True


def _unique(recs, key, what, out) -> list:
    ids = [r[key] for r in recs]
    if len(set(map(repr, ids))) != len(ids):
        out.append(f"duplicate {what} id")
    if ids != sorted(ids, key=lambda v: (type(v).__name__, v)):
        out.append(f"{what} records are not in canonical order")
    return ids


def validate(doc) -> list[str]:
    """Every problem with a document; [] means valid. Total: never raises."""
    try:
        return _validate(doc)
    except Exception as x:          # malformed structure the explicit checks did not foresee: fail closed
        return [f"document unreadable: {type(x).__name__}: {x}"]


def _validate(doc) -> list[str]:
    P: list[str] = []
    if type(doc) is not dict:
        return ["document is not a JSON object"]
    if set(doc) != TOP_KEYS:
        return [f"top-level keys {sorted(set(doc) ^ TOP_KEYS)} missing or unexpected"]
    if doc["format"] != FORMAT:
        P.append(f"format {doc['format']!r} != {FORMAT!r}")
    if doc["schema_version"] != SCHEMA_VERSION or type(doc["schema_version"]) is not int:
        P.append(f"unsupported schema_version {doc['schema_version']!r}")
    if doc["status_vocabulary"] != STATUS_MAP:
        P.append("status_vocabulary differs from this reader's mapping")
    if doc["coordinates"] != COORDINATES:
        P.append("coordinate convention differs from this reader's")
    if P:
        return P
    if doc["content_sha256"] != content_hash(doc):
        P.append("content_sha256 does not match the content (tampered or truncated)")
    for k in ("primitives", "evidence", "objects", "edges", "site_links", "attachments", "sites", "nets",
              "quarantined", "connectivity", "unsupported", "notes", "warnings"):
        if type(doc[k]) is not list:
            P.append(f"{k} is not a list")
    if P:
        return P

    # ---- primitives / evidence
    for i, p in enumerate(doc["primitives"]):
        if _keys(p, "primitive", f"primitive[{i}]", P):
            if type(p["id"]) is not int or not _is_str(p["content_ref"]):
                P.append(f"primitive[{i}]: bad id/content_ref")
    for i, e in enumerate(doc["evidence"]):
        if _keys(e, "evidence", f"evidence[{i}]", P):
            if not _is_str(e["id"]) or not _str_list(e["lineage"]) or not e["lineage"] \
                    or not _str_list(e["derived_from"]) or type(e["uses_connectivity"]) is not bool:
                P.append(f"evidence[{i}]: malformed")
    if P:
        return P
    _unique(doc["primitives"], "id", "primitive", P)
    _unique(doc["evidence"], "id", "evidence", P)
    prim_refs = {p["content_ref"] for p in doc["primitives"]}
    prim_ids = {p["id"] for p in doc["primitives"]}
    ev_known = {e["id"] for e in doc["evidence"]}
    unres = doc["unresolved_references"]
    if type(unres) is not dict or set(unres) != {"evidence", "provenance", "primitives",
                                                  "objects_without_primitives", "sites_without_arm_geometry"}:
        return P + ["unresolved_references malformed"]

    # ---- objects
    for i, o in enumerate(doc["objects"]):
        if _keys(o, "object", f"object[{i}]", P):
            if not _is_str(o["id"]) or o["kind"] not in ("WIRE", "PIN", "DOT", "LABEL", "UNRESOLVED"):
                P.append(f"object[{i}]: bad id/kind")
            if o["prims"] is not None and not (type(o["prims"]) is list and all(type(v) is int for v in o["prims"])):
                P.append(f"object[{i}]: bad prims")
    if P:
        return P
    oids = _unique(doc["objects"], "id", "object", P)
    known = set(oids)
    for o in doc["objects"]:
        if o["attach"] is not None and o["attach"] not in known:
            P.append(f"object {o['id']}: dangling attach {o['attach']!r}")
        if (o["kind"] == "UNRESOLVED") != (o["attach"] is not None):
            P.append(f"object {o['id']}: attach present iff UNRESOLVED")
        if o["pin"] is not None and o["kind"] != "PIN" or o["dot"] is not None and o["kind"] != "DOT":
            P.append(f"object {o['id']}: pin/dot detail on a {o['kind']}")
        for i in o["prims"] or ():
            if i not in prim_ids and i not in unres["primitives"]:
                P.append(f"object {o['id']}: dangling primitive {i}")
        if o["pin"] is not None:
            for x in o["pin"]["evidence"] + o["pin"]["independent_items"]:
                if x not in ev_known and x not in unres["evidence"]:
                    P.append(f"object {o['id']}: dangling evidence {x!r}")
    if sorted(o["id"] for o in doc["objects"] if o["prims"] is None) != unres["objects_without_primitives"]:
        P.append("objects_without_primitives does not match the objects")

    # ---- edges: premises re-classified, status transitions checked, provenance recomputed
    Q = set()
    for t in doc["quarantined"]:
        if type(t) is not list or len(t) != 2 or not _str_list(t[0]):
            P.append("quarantined record malformed")
            continue
        for o in t[0]:
            if o not in known:
                P.append(f"quarantined: dangling object {o!r}")
        Q |= set(t[0])
    V, I = [], []
    for i, e in enumerate(doc["edges"]):
        where = f"edge[{i}]"
        if not _keys(e, "edge", where, P):
            continue
        if not (type(e["roles"]) is list and len(e["roles"]) == 2):
            P.append(f"{where}: needs two roles")
            continue
        if not all(_keys(r, "role", f"{where}.role", P) for r in e["roles"]):
            continue
        if not all(_str_list(r["evidence"]) and type(r["weak"]) is bool for r in e["roles"]) \
                or not _str_list(e["negative"]) or not _str_list(e["prov"]) or type(e["n_prims"]) is not int:
            P.append(f"{where}: malformed premise fields")
            continue
        eid = e["id"]
        for end in (e["a"], e["b"]):
            if end not in known:
                P.append(f"edge {eid}: dangling endpoint {end!r}")
        if e["origin"] not in (ORIGIN_PIPELINE, ORIGIN_TR6):
            P.append(f"edge {eid}: unknown origin {e['origin']!r}")
        if e["rule_kind"] != (RULES[e["rule"]][0] if e["rule"] in RULES else None):
            P.append(f"edge {eid}: rule_kind does not match rule")
        if e["status"] not in STATUS_UNMAP:
            P.append(f"edge {eid}: invalid status {e['status']!r}")
        cst, crs = classify_edge(_edge_from_record(e))
        if e["classified"] != [STATUS_MAP[cst], crs]:
            P.append(f"edge {eid}: classified {e['classified']} but premises give {[cst, crs]}")
        st = STATUS_UNMAP.get(e["status"])
        touches = e["a"] in Q or e["b"] in Q
        if cst == VERIFIED:
            ok = (st, e["reason"]) == ((CONFLICT, "QUARANTINED") if e["a"] in Q else (VERIFIED, crs))
            if st == VERIFIED and audit_verified(_edge_from_record(e)):
                P.append(f"edge {eid}: VERIFIED but fails the independent audit")
            V.append((e["a"], e["b"]))
        elif cst == INFERRED:
            ok = (st, e["reason"]) in ({(UNKNOWN, "TOUCHES_CONFLICT_COMPONENT")} if touches else
                                       {(INFERRED, crs), (UNKNOWN, "REJECTED_BY_CONSTRAINT")})
            if st == INFERRED:
                I.append((e["a"], e["b"]))
        else:
            ok = (st, e["reason"]) == (cst, crs)
        if not ok:
            P.append(f"edge {eid}: status {e['status']}/{e['reason']} does not follow from "
                     f"classified {cst}/{crs} (promotion or tampering)")
        evs = [x for r in e["roles"] for x in r["evidence"]]
        exp_prov = _edge_provenance(e["prov"], evs, prim_refs, ev_known)
        if e["provenance"] != exp_prov:
            P.append(f"edge {eid}: provenance block does not match its references")
        for x in e["prov"]:
            if x not in prim_refs and x not in unres["provenance"]:
                P.append(f"edge {eid}: dangling provenance {x!r}")
        for x in evs:
            if x not in ev_known and x not in unres["evidence"]:
                P.append(f"edge {eid}: dangling evidence {x!r}")
    if P:
        return P
    _unique(doc["edges"], "id", "edge", P)

    # ---- sites (+ F2 arm geometry checks, independent of rules.audit_site_arms)
    from .rules import _dir_label
    site_by_id = {}
    for i, s in enumerate(doc["sites"]):
        if not _keys(s, "site", f"site[{i}]", P):
            continue
        sid = s["id"]
        site_by_id[sid] = s
        labels, aobjs, gedges = [], set(), []
        for a in s["arms"]:
            if not _keys(a, "arm", f"site {sid} arm", P):
                continue
            labels.append(a["label"])
            aobjs.add(a["obj"])
            if a["obj"] not in known:
                P.append(f"site {sid}: dangling arm object {a['obj']!r}")
            g = a["geometry"]
            if g is None:
                continue
            if not _keys(g, "arm_geometry", f"site {sid} arm {a['label']} geometry", P):
                continue
            gedges.append(g["graph_edge"])
            if sid != f"site:{g['node']}":
                P.append(f"site {sid}/{a['label']}: arm does not start at the site node")
            if g["chosen_prim"] not in g["prim_ids"] or a["obj"] != f"w:{g['chosen_prim']}":
                P.append(f"site {sid}/{a['label']}: arm object is not one of its edge's primitives")
            if not (type(g["vector"]) is list and len(g["vector"]) == 2 and all(map(_num, g["vector"]))) \
                    or g["direction"] != _dir_label(*g["vector"]):
                P.append(f"site {sid}/{a['label']}: direction does not fit the arm vector")
            if s["kind"] == "FOURWAY" and a["label"] != g["direction"]:
                P.append(f"site {sid}/{a['label']}: FOURWAY label is not the arm direction")
            if len(g["prov"]) != len(g["prim_ids"]):
                P.append(f"site {sid}/{a['label']}: arm provenance incomplete")
            for x in g["prov"]:
                if x not in prim_refs and x not in unres["provenance"]:
                    P.append(f"site {sid}/{a['label']}: dangling provenance {x!r}")
        if len(set(labels)) != len(labels):
            P.append(f"site {sid}: duplicate arm label")
        if len(set(gedges)) != len(gedges):
            P.append(f"site {sid}: duplicate arm evidence (one graph edge on two arms)")
        names = [op["name"] for op in s["options"]]
        if not names or len(set(names)) != len(names):
            P.append(f"site {sid}: bad option list")
        for op in s["options"]:
            for l in op["links"]:
                if not (type(l) is list and len(l) == 2 and l[0] != l[1] and l[0] in aobjs and l[1] in aobjs):
                    P.append(f"site {sid}: option link {l!r} is not between distinct arms")
        r = s["result"]
        if type(r) is not dict or set(r) != {"status", "option", "reason"} or r["status"] not in STATUS_UNMAP:
            P.append(f"site {sid}: malformed result")
            continue
        if r["status"] == VERIFIED:
            P.append(f"site {sid}: a site is never VERIFIED")
        if (r["status"] == INFERRED) != (r["option"] is not None) or r["option"] is not None and r["option"] not in names:
            P.append(f"site {sid}: option present iff INFERRED and must be in the domain")
        if s["fixed"] is not None and s["fixed"] not in names:
            P.append(f"site {sid}: fixed option not in domain")
    _unique(doc["sites"], "id", "site", P)
    if sorted(s["id"] for s in doc["sites"] if any(a.get("geometry") is None for a in s["arms"])) \
            != unres["sites_without_arm_geometry"]:
        P.append("sites_without_arm_geometry does not match the sites")

    # ---- site links: exactly the links of every INFERRED site's option
    want_links = {}
    for sid, s in site_by_id.items():
        r = s["result"]
        if type(r) is dict and r.get("status") == INFERRED:
            op = next((o for o in s["options"] if o["name"] == r["option"]), None)
            for a, b in (op or {"links": []})["links"]:
                want_links[f"site:{sid}:{a}|{b}"] = (sid, r["option"], a, b)
    got = {}
    for i, l in enumerate(doc["site_links"]):
        if not _keys(l, "site_link", f"site_link[{i}]", P):
            continue
        got[l["id"]] = (l["site"], l["option"], l["a"], l["b"])
        if l["status"] != INFERRED or l["origin"] != ORIGIN_SITE:
            P.append(f"site link {l['id']}: must be INFERRED from {ORIGIN_SITE}")
        if l["provenance"] != _link_provenance(site_by_id.get(l["site"]), l["a"], l["b"], prim_refs):
            P.append(f"site link {l['id']}: provenance block does not match its site arms")
    if got != want_links:
        P.append("site_links are not exactly the links of the resolved sites' options")
    _unique(doc["site_links"], "id", "site link", P)
    A = []
    for i, a in enumerate(doc["attachments"]):
        if not _keys(a, "attachment", f"attachment[{i}]", P):
            continue
        if a["electrical"] is not False or a["origin"] != ORIGIN_ATTACH:
            P.append(f"attachment {a['id']}: must be non-electrical")
        A.append((a["object"], a["attach"]))
    want_A = sorted((o["id"], o["attach"]) for o in doc["objects"] if o["attach"] is not None)
    if sorted(A) != want_A or [a["id"] for a in doc["attachments"]] != [f"attach:{o}" for o, _ in want_A]:
        P.append("attachments do not match the objects' attach fields")
    if P:
        return P

    # ---- connectivity recomputed from the edges in the file (no connection may appear that they do not give)
    L = [(a, b) for _, _, a, b in want_links.values()]
    elec = _components(oids, V + I + L)
    net = _components(oids, V + I + L + A)
    vg = _vgroups(oids, V, Q)
    rows = doc["connectivity"]
    if [r.get("object") if type(r) is dict else None for r in rows] != oids:
        P.append("connectivity does not list every object exactly once, in order")
        return P
    for r in rows:
        if not _keys(r, "conn", "connectivity", P):
            continue
        o = r["object"]
        if (r["net"], r["electrical_group"], r["verified_group"]) != (net[o], elec[o], vg.get(o)):
            P.append(f"connectivity {o}: net/electrical/verified group does not follow from the edges")
    nets = doc["nets"]
    members = defaultdict(list)
    for o in oids:
        members[net[o]].append(o)
    if [n.get("id") if type(n) is dict else None for n in nets] != sorted(members):
        P.append("nets do not match the partition the edges give")
    else:
        for n in nets:
            if not _keys(n, "net", f"net {n['id']}", P):
                continue
            if n["objects"] != members[n["id"]]:
                P.append(f"net {n['id']}: members differ from the partition")
            if n["status"] != (CONFLICT if set(n["objects"]) & Q else "OK") or n["closure"] not in ("OPEN", "CLOSED") \
                    or (n["closure"] == "OPEN") != bool(n["open_reasons"]):
                P.append(f"net {n['id']}: status/closure inconsistent")
        # every UNKNOWN edge / open site keeps its nets OPEN (so dropping a record is visible), and no reason
        # names a record that is not there
        want_r = defaultdict(set)
        for e in doc["edges"]:
            if e["status"] == UNKNOWN:
                for o in (e["a"], e["b"]):
                    want_r[net[o]].add(f"UNKNOWN_CANDIDATE:{e['id']}")
        for sid, s in site_by_id.items():
            if s["result"]["status"] != INFERRED:
                for a in s["arms"]:
                    want_r[net[a["obj"]]].add(f"OPEN_SITE:{sid}")
        for o in doc["objects"]:
            if o["kind"] == "UNRESOLVED":
                want_r[net[o["id"]]].add(f"UNRESOLVED_TERMINAL:{o['id']}")
        for n in nets:
            if type(n) is dict and set(n.get("open_reasons", ())) != want_r.get(n["id"], set()):
                P.append(f"net {n['id']}: open_reasons do not match the UNKNOWN edges / open sites / terminals")
    cons = doc["constraints"]
    if type(cons) is not dict or set(cons) != {"cannot_link", "names", "no_connect"}:
        P.append("constraints malformed")
    else:
        for a, b, _ in cons["cannot_link"]:
            if a not in known or b not in known:
                P.append(f"cannot_link: dangling object {a!r}/{b!r}")
    for k in ("evidence", "provenance"):
        if unres[k] != sorted(set(unres[k])):
            P.append(f"unresolved_references.{k} not canonical")
    if set(unres["evidence"]) & ev_known or set(unres["provenance"]) & prim_refs:
        P.append("unresolved_references lists something that is resolved")
    return P


def reserialize(doc: dict) -> str:
    """write(read(x)): the document is its own canonical form."""
    return canonical(doc)
