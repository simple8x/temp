"""Provenance-preserving planar graph prototype.

This module is deliberately parallel to the legacy topology engine. It does not
assign electrical nets or infer symbol bodies. Ambiguous endpoint-on-interior
contacts remain UNKNOWN unless an explicit junction mark supports connection.
Coordinates are PDF points in the page coordinate system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
import math
from typing import Iterable, Optional


@dataclass(frozen=True)
class Primitive:
    primitive_id: str
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = "wire"

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Node:
    node_id: int
    x: float
    y: float
    provenance: set[str] = field(default_factory=set)
    decisions: list[str] = field(default_factory=list)


@dataclass
class Edge:
    edge_id: int
    start: int
    end: int
    primitive_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Contact:
    x: float
    y: float
    primitive_a: str
    primitive_b: str
    kind: str
    decision: str  # CONNECTED | DISCONNECTED | UNKNOWN
    reason: str


@dataclass
class PlanarGraph:
    nodes: list[Node]
    edges: list[Edge]
    contacts: list[Contact]
    tolerance: float

    def connected_components(self) -> list[set[int]]:
        """Components over explicitly connected graph nodes; UNKNOWN is not joined."""
        parent = list(range(len(self.nodes)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            parent[find(a)] = find(b)

        for edge in self.edges:
            union(edge.start, edge.end)
        groups: dict[int, set[int]] = {}
        for node in self.nodes:
            groups.setdefault(find(node.node_id), set()).add(node.node_id)
        return list(groups.values())


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _point_on_segment(px: float, py: float, s: Primitive, tol: float):
    dx, dy = s.x1 - s.x0, s.y1 - s.y0
    ll = dx * dx + dy * dy
    if ll == 0:
        return None
    t = ((px - s.x0) * dx + (py - s.y0) * dy) / ll
    if t < -tol / max(s.length, tol) or t > 1 + tol / max(s.length, tol):
        return None
    qx, qy = s.x0 + t * dx, s.y0 + t * dy
    if math.hypot(px - qx, py - qy) <= tol:
        return max(0.0, min(1.0, t))
    return None


def _intersection(a: Primitive, b: Primitive, tol: float):
    """Return (kind, point, ta, tb); parallel collinear overlaps are separate."""
    ax, ay = a.x1 - a.x0, a.y1 - a.y0
    bx, by = b.x1 - b.x0, b.y1 - b.y0
    den = _cross(ax, ay, bx, by)
    qx, qy = b.x0 - a.x0, b.y0 - a.y0
    scale = max(a.length * b.length, 1.0)
    if abs(den) > 1e-10 * scale:
        ta = _cross(qx, qy, bx, by) / den
        tb = _cross(qx, qy, ax, ay) / den
        eps_a = tol / max(a.length, tol)
        eps_b = tol / max(b.length, tol)
        if -eps_a <= ta <= 1 + eps_a and -eps_b <= tb <= 1 + eps_b:
            ta, tb = max(0.0, min(1.0, ta)), max(0.0, min(1.0, tb))
            pa = (a.x0 + ta * ax, a.y0 + ta * ay)
            pb = (b.x0 + tb * bx, b.y0 + tb * by)
            p = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
            enda = ta <= eps_a or ta >= 1 - eps_a
            endb = tb <= eps_b or tb >= 1 - eps_b
            kind = "endpoint_endpoint" if enda and endb else (
                "endpoint_on_segment" if enda or endb else "proper_crossing")
            return kind, p, ta, tb
        return None

    # Parallel: reject non-collinear; report overlap endpoints for collinear geometry.
    if abs(_cross(qx, qy, ax, ay)) > tol * max(a.length, 1.0):
        return None
    ll = ax * ax + ay * ay
    if ll == 0:
        return None
    t0 = (qx * ax + qy * ay) / ll
    t1 = t0 + (bx * ax + by * ay) / ll
    lo, hi = max(0.0, min(t0, t1)), min(1.0, max(t0, t1))
    if hi < lo - tol / max(a.length, tol):
        return None
    points = []
    for t in (lo, hi):
        p = (a.x0 + t * ax, a.y0 + t * ay)
        tb = _point_on_segment(p[0], p[1], b, tol)
        if tb is not None:
            points.append((p, t, tb))
    if not points:
        return None
    return ("collinear_overlap", points)


def build_planar_graph(
    primitives: Iterable[Primitive],
    junction_dots: Iterable[tuple[float, float]] = (),
    tolerance: float = 0.05,
    dot_radius: float = 1.0,
) -> PlanarGraph:
    """Split linework at geometric contacts while preserving uncertain semantics.

    Rules:
    * endpoint/endpoint contact: CONNECTED within tolerance;
    * endpoint/interior T contact: UNKNOWN without a dot, CONNECTED with a dot;
    * proper X crossing: DISCONNECTED without a dot, CONNECTED with a dot;
    * collinear overlap: split and CONNECTED, retaining every source primitive ID;
    * no proximity-only joining of distinct contacts.
    """
    segs = list(primitives)
    dots = list(junction_dots)
    cuts = [{0.0, 1.0} for _ in segs]
    raw_contacts: list[Contact] = []

    def has_dot(x: float, y: float) -> bool:
        return any(math.hypot(x - dx, y - dy) <= dot_radius for dx, dy in dots)

    for i, j in combinations(range(len(segs)), 2):
        a, b = segs[i], segs[j]
        hit = _intersection(a, b, tolerance)
        if hit is None:
            continue
        if hit[0] == "collinear_overlap":
            for (p, ta, tb) in hit[1]:
                cuts[i].add(ta)
                cuts[j].add(tb)
                raw_contacts.append(Contact(p[0], p[1], a.primitive_id, b.primitive_id,
                                            "collinear_overlap", "CONNECTED",
                                            "shared_collinear_geometry"))
            continue
        kind, p, ta, tb = hit
        cuts[i].add(ta)
        cuts[j].add(tb)
        dot = has_dot(*p)
        if kind == "endpoint_endpoint":
            decision, reason = "CONNECTED", "endpoint_contact"
        elif kind == "endpoint_on_segment":
            decision, reason = (("CONNECTED", "explicit_junction_dot") if dot else
                                ("UNKNOWN", "endpoint_on_segment_without_dot"))
        else:
            decision, reason = (("CONNECTED", "explicit_junction_dot") if dot else
                                ("DISCONNECTED", "proper_crossing_without_dot"))
        raw_contacts.append(Contact(p[0], p[1], a.primitive_id, b.primitive_id,
                                    kind, decision, reason))

    # Explicit dots split every segment they geometrically touch. A dot alone is
    # not enough to infer a connection unless at least two primitives meet it.
    for dx, dy in dots:
        hits = []
        for i, seg in enumerate(segs):
            t = _point_on_segment(dx, dy, seg, dot_radius)
            if t is not None:
                cuts[i].add(t)
                hits.append((i, t))
        if len(hits) >= 2:
            ids = sorted({segs[i].primitive_id for i, _ in hits})
            for aid, bid in combinations(ids, 2):
                raw_contacts.append(Contact(dx, dy, aid, bid, "junction_dot",
                                            "CONNECTED", "explicit_junction_dot"))

    # Node identity is exact after tolerance-based coordinate normalization.
    # We avoid broad radius clustering: only points created by the same
    # geometric contact are canonicalized together.
    node_map: dict[tuple[int, int], int] = {}
    nodes: list[Node] = []
    edges: list[Edge] = []

    def node_for(x: float, y: float, pid: str) -> int:
        key = (round(x / tolerance), round(y / tolerance))
        if key not in node_map:
            node_id = len(nodes)
            node_map[key] = node_id
            nodes.append(Node(node_id, x, y))
        n = nodes[node_map[key]]
        n.provenance.add(pid)
        return n.node_id

    for i, seg in enumerate(segs):
        ts = sorted(cuts[i])
        for t0, t1 in zip(ts, ts[1:]):
            if (t1 - t0) * seg.length <= 1e-9:
                continue
            x0 = seg.x0 + t0 * (seg.x1 - seg.x0)
            y0 = seg.y0 + t0 * (seg.y1 - seg.y0)
            x1 = seg.x0 + t1 * (seg.x1 - seg.x0)
            y1 = seg.y0 + t1 * (seg.y1 - seg.y0)
            u = node_for(x0, y0, seg.primitive_id)
            v = node_for(x1, y1, seg.primitive_id)
            edges.append(Edge(len(edges), u, v, {seg.primitive_id}))

    # Attach contact decisions to involved nodes for auditability.
    for c in raw_contacts:
        key = (round(c.x / tolerance), round(c.y / tolerance))
        nid = node_map.get(key)
        if nid is not None:
            nodes[nid].decisions.append(
                f"{c.decision}:{c.kind}:{c.primitive_a}:{c.primitive_b}:{c.reason}"
            )
    return PlanarGraph(nodes, edges, raw_contacts, tolerance)
