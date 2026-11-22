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

from .pins import FORWARD, REVERSED, S2_REL_MAX, S2_TAU_ABS_MAX, s2_tolerance, stub_orientation
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
    "arm_geometry": {"node", "graph_edge", "other_node", "prim_ids", "chosen_prim", "direction", "vector", "prov",
                     "node_vertices", "node_through"},
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
def _stub_record(stub) -> dict | None:
    if stub i