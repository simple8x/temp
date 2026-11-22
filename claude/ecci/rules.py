"""Builds the abstract `ConnGraph` from M1/M2 results (design v2 s1.3 rule table).

This module only turns geometry facts + roles into CANDIDATE edges with premise records and
ambiguity sites. It never decides a status: `status.classify_edge` and `solver.solve` do.

Rules produced:
  TR1  two independent wire primitives end-to-end (exactly 2 arms, 2 prims)          SOUND
  TR2  wire arms at a junction dot where >= 3 arms meet                               SOUND
  TR3  pin tip <-> its own measured stub, or the single wire ending at the tip       SOUND
  TR4  3 arms without dot (T-touch), several wires at a pin tip, wire through a tip   CONVENTIONAL
  TR5  collinear gap <= g_max between two dangling wire ends                          CONVENTIONAL
  TR7  4 (or more) arms without a dot -> ambiguity SITE, no edge
Not produced here (no inputs exist yet): TR6 / C1 / C5 (net names, no-connect markers).
Constraint C4 (wire crossing / touching a symbol body away from its pins) is produced.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .arrangement import EPS_DEFAULT, build_arrangement, independent_prims_at
from .classify import LC_BODY, LC_WIRE, PageClassification, classify_page
from .constraints import Constraints
from .evidence import ROLE_CANDIDATE, ROLE_CONFIRMED, ROLE_UNKNOWN, WEAK_SOURCES, EvidenceItem, decide_role
from .flatten import point_segment_distance
from .ir import SRC_VECTOR, PageIngest
from .pins import POS_CANDIDATE, POS_CONFIRMED, ClusterResult, PinParams, detect_pins
from .solver import ConnGraph, Obj, Option, Site, _pair, fourway_site
from .status import Edge, RoleRef


@dataclass(frozen=True)
class RulesParams:
    eps: float = EPS_DEFAULT
    dot_max_diag_frac: float = 0.01     # dot diameter <= this x page diagonal          (ASSUMPTION)
    dot_min_vertices: int = 8           # a flattened disc has many vertices            (ASSUMPTION)
    dot_roundness: float = 0.12         # (rmax - rmin) / rmean over the outline        (ASSUMPTION)
    dot_aspect: float = 0.85            # short / long side of the bounding box         (ASSUMPTION)
    dot_node_tol_rel: float = 0.75      # node within this x radius of the dot centre   (ASSUMPTION)
    dot_node_tol_abs: float = 0.05
    g_max: float = 3.0                  # maximum collinear gap bridged by TR5 (points) (ASSUMPTION)
    tip_tol_abs: float = 0.05
    tip_tol_rel: float = 0.1            # fraction of the pin length


@dataclass
class BuildResult:
    graph: ConnGraph
    notes: list = field(default_factory=list)
    unsupported: list = field(default_factory=list)
    dots: dict = field(default_factory=dict)          # dot object id -> (centre, radius, attached node or None)
    obj_prims: dict = field(default_factory=dict)     # object id -> tuple of prim ids (traceability, I8)
    pin_tips: dict = field(default_factory=dict)      # pin object id -> tip (normalized page space)
    # F2: site id -> (ArmRecord, ...) in the order of Site.arms. Side table so Site / solver / digest stay unchanged.
    site_arms: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ArmRecord:
    """Independent geometric provenance of one site arm (F2): which arrangement node and edge the arm is, which
    primitives cover that edge, the direction it leaves the node in, and the PDF operators behind it."""
    label: str                      # = the label in Site.arms
    obj: str                        # = the object in Site.arms ("w:<chosen_prim>")
    node: int                       # arrangement node the site sits on
    graph_edge: int                 # arrangement edge this arm is
    other_node: int                 # far end of that edge
    prim_ids: tuple                 # every primitive covering the edge (sorted); > 1 = overlapping wires
    chosen_prim: int                # the primitive the arm object was named after (min of prim_ids)
    direction: str | None           # _dir_label of the arm vector, None if not axis-aligned
    vector: tuple                   # (dx, dy) node -> other node, normalized page space (y down)
    prov: tuple                     # content_ref of every primitive in prim_ids
    node_vertices: tuple = ()       # (prim_id, vertex_idx) endpoint/polyline-vertex facts at this node
    node_through: tuple = ()        # (prim_id, seg_idx) interior-through facts at this node


def audit_site_arms(graph: ConnGraph, site_arms: dict, obj_prims: dict | None = None) -> list[str]:
    """F2 checks: every site has one arm record per arm, naming the same label/object, a real wire primitive, a
    distinct arrangement edge, and a direction that fits its vector. Arms sharing one wire object are legal (a wire
    passing straight through a crossing occupies two arms) as long as they are distinct graph edges. Total."""
    problems: list[str] = []
    try:
        sids = {s.id for s in graph.sites}
        for extra in sorted(set(site_arms) - sids):
            problems.append(f"F2 {extra}: arm records for a site that is not in the graph")
        for s in sorted(graph.sites, key=lambda s: s.id):
            recs = site_arms.get(s.id)
            if not recs:
                problems.append(f"F2 {s.id}: missing arm provenance")
                continue
            if tuple((r.label, r.obj) for r in recs) != tuple(s.arms):
                problems.append(f"F2 {s.id}: arm records do not match the site arms")
            nodes = {r.node for r in recs}
            if len(nodes) != 1 or s.id != f"site:{next(iter(nodes))}":
                problems.append(f"F2 {s.id}: arms do not all start at the site node")
            gedges = [r.graph_edge for r in recs]
            if len(set(gedges)) != len(gedges):
                problems.append(f"F2 {s.id}: duplicate arm evidence (one graph edge on two arms)")
            dirs = [r.direction for r in recs if r.direction is not None]
            if s.kind == "FOURWAY" and len(set(dirs)) != len(recs):
                problems.append(f"F2 {s.id}: contradictory arm directions")
            for r in recs:
                if not r.prim_ids or r.chosen_prim not in r.prim_ids or r.obj != f"w:{r.chosen_prim}":
                    problems.append(f"F2 {s.id}/{r.label}: arm object is not one of the edge's primitives")
                if obj_prims is not None and r.chosen_prim not in obj_prims.get(r.obj, ()):
                    problems.append(f"F2 {s.id}/{r.label}: arm refers to an unrelated graph object")
                if r.direction != _dir_label(*r.vector):
                    problems.append(f"F2 {s.id}/{r.label}: direction {r.direction!r} does not fit vector {r.vector}")
                if s.kind == "FOURWAY" and r.label != r.direction:
                    problems.append(f"F2 {s.id}/{r.label}: FOURWAY label is not the arm direction")
                if len(r.prov) != len(r.prim_ids) or not all(isinstance(x, str) and x for x in r.prov):
                    problems.append(f"F2 {s.id}/{r.label}: missing arm provenance")
    except Exception as x:          # malformed record: a violation, never a crash
        problems.append(f"F2: arm records unreadable ({type(x).__name__})")
    return problems


def _role(obj: str, kind: str, status: str, items, weak: bool) -> RoleRef:
    return RoleRef(obj, kind, status, tuple(items), weak)


def _wire_role(cls: PageClassification, pid: int) -> RoleRef:
    rec = cls.records[pid]
    dec = decide_role(list(rec.evidence), cls.reg