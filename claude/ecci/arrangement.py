"""Planar arrangement of the extracted primitives (M1: geometry facts only).

Every primitive segment is split wherever it meets another segment, and nearby points are
merged into nodes. The result records GEOMETRIC FACTS about each node:
  * `vertices`: polyline vertices (prim, vertex_idx) located at the node;
  * `through`: segments that pass through the node without ending there;
  * incident edges, each carrying the source segments it covers.
Nothing here states that anything is electrically connected. A crossing, a T-touch and a
polyline vertex are all just nodes; telling them apart is what `arm_relations` is for.

Tolerance semantics (FACT): `eps` is a *touch tolerance*, not a metric. Two points closer
than eps are merged; clusters are transitive (chaining), which is reported in `stats`.
Complexity: uniform-grid broad phase, expected O(n + k) for n segments and k intersections
with bounded local density; worst case O(n^2) (guarded by `max_pairs`).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .ir import ArrangementGraph, Edge, Node, Prim, SegRef

EPS_DEFAULT = 0.02

REL_SAME_PATH_VERTEX = "SAME_PATH_VERTEX"      # consecutive segments of ONE path meeting at its vertex
REL_SAME_SEGMENT = "SAME_SEGMENT_THROUGH"      # the two arms are halves of ONE straight segment passing through
REL_SAME_PATH_OTHER = "SAME_PATH_OTHER"        # same path, but self-touch / self-cross / non-consecutive
REL_INDEPENDENT = "INDEPENDENT_PRIMS"          # arms belong to different primitives


class ArrangementError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class _Seg:
    id: int
    prim_id: int
    idx: int
    p: tuple[float, float]
    q: tuple[float, float]
    rp: int            # record id of the p endpoint (a prim vertex record)
    rq: int
    length: float


class _UF:
    def __init__(self, n: int = 0) -> None:
        self.p = list(range(n))

    def add(self) -> int:
        self.p.append(len(self.p))
        return len(self.p) - 1

    def find(self, a: int) -> int:
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.p[rb] = ra
            else:
                self.p[ra] = rb


def _pt_seg(px: float, py: float, s: _Seg) -> tuple[float, float]:
    ax, ay = s.p
    dx, dy = s.q[0] - ax, s.q[1] - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (s.length * s.length)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _pair_events(a: _Seg, b: _Seg, eps: float):
    """Split events for a pair. Returns (ev_a, ev_b, links).

    ev_x: list of (t, x, y) interior split points on that segment.
    links: list of (record, record) endpoint pairs that must share a node.
    """
    ev_a: list[tuple[float, float, float]] = []
    ev_b: list[tuple[float, float, float]] = []
    links: list[tuple[int, int]] = []
    touched = False

    def handle(pt: tuple[float, float], rec: int, host: _Seg, ev: list) -> None:
        nonlocal touched
        d, t = _pt_seg(pt[0], pt[1], host)
        if d > eps:
            return
        touched = True
        along = t * host.length
        if along <= eps:
            links.append((rec, host.rp))
        elif host.length - along <= eps:
            links.append((rec, host.rq))
        else:
            ev.append((t, pt[0], pt[1]))

    handle(b.p, b.rp, a, ev_a)
    handle(b.q, b.rq, a, ev_a)
    handle(a.p, a.rp, b, ev_b)
    handle(a.q, a.rq, b, ev_b)
    if touched:
        return ev_a, ev_b, links

    # Proper crossing: endpoints strictly on opposite sides, and no touching within eps.
    # The point is computed with the two segments in a CANONICAL order (by coordinates, not
    # by input order) so the result is bit-identical regardless of how primitives are listed.
    key_a = (min(a.p, a.q), max(a.p, a.q))
    key_b = (min(b.p, b.q), max(b.p, b.q))
    swap = key_b < key_a
    u, v = (b, a) if swap else (a, b)
    dux, duy = u.q[0] - u.p[0], u.q[1] - u.p[1]
    dvx, dvy = v.q[0] - v.p[0], v.q[1] - v.p[1]
    h0 = _cross(dux, duy, v.p[0] - u.p[0], v.p[1] - u.p[1]) / u.length
    h1 = _cross(dux, duy, v.q[0] - u.p[0], v.q[1] - u.p[1]) / u.length
    g0 = _cross(dvx, dvy, u.p[0] - v.p[0], u.p[1] - v.p[1]) / v.length
    g1 = _cross(dvx, dvy, u.q[0] - v.p[0], u.q[1] - v.p[1]) / v.length
    if h0 * h1 < 0.0 and g0 * g1 < 0.0:
        sv = h0 / (h0 - h1)                      # parameter along v
        x = v.p[0] + sv * dvx
        y = v.p[1] + sv * dvy
        tu = ((x - u.p[0]) * dux + (y - u.p[1]) * duy) / (u.length * u.length)
        tu = min(1.0, max(0.0, tu))
        t_a, t_b = (sv, tu) if swap else (tu, sv)
        ev_a.append((t_a, x, y))
        ev_b.append((t_b, x, y))
    return ev_a, ev_b, links


def build_arrangement(prims: list[Prim], eps: float = EPS_DEFAULT, *,
                      max_pairs: int = 5_000_000, max_cells_per_seg: int = 2500) -> ArrangementGraph:
    if not (eps > 0 and math.isfinite(eps)):
        raise ArrangementError("BAD_PARAMETER", "eps must be positive and finite")

    # ---- records: one per polyline vertex (+ later one per split event)
    rx: list[float] = []
    ry: list[float] = []
    rvert: list[tuple[int, int] | None] = []
    uf = _UF()

    def new_rec(x: float, y: float, vert: tuple[int, int] | None) -> int:
        rx.append(x)
        ry.append(y)
        rvert.append(vert)
        return uf.add()

    segs: list[_Seg] = []
    points: list[tuple[int, tuple[float, float]]] = []
    degenerate = 0
    for prim in prims:
        if prim.kind == "point":
            points.append((prim.id, prim.pts[0]))
            continue
        vrec = [new_rec(x, y, (prim.id, i)) for i, (x, y) in enumerate(prim.pts)]
        n = len(prim.pts)
        for i, p, q in prim.segments():
            j = (i + 1) % n
            length = math.hypot(q[0] - p[0], q[1] - p[1])
            if length <= eps:
                degenerate += 1
                continue
            segs.append(_Seg(len(segs), prim.id, i, p, q, vrec[i], vrec[j], length))

    stats: dict[str, float] = {
        "prims": len(prims), "segments": len(segs), "degenerate_segments": degenerate,
    }

    # ---- broad phase: uniform grid
    events: list[list[tuple[float, int]]] = [[] for _ in segs]   # per seg: (t, record)
    pairs_tested = 0
    n_events = 0
    cell = 1.0
    long_ids: list[int] = []
    if segs:
        lens = sorted(s.length for s in segs)
        cell = max(8.0 * eps, lens[len(lens) // 2])
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for s in segs:
            x0, x1 = min(s.p[0], s.q[0]) - eps, max(s.p[0], s.q[0]) + eps
            y0, y1 = min(s.p[1], s.q[1]) - eps, max(s.p[1], s.q[1]) + eps
            cx0, cx1 = math.floor(x0 / cell), math.floor(x1 / cell)
            cy0, cy1 = math.floor(y0 / cell), math.floor(y1 / cell)
            if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > max_cells_per_seg:
                long_ids.append(s.id)
                continue
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    grid[(cx, cy)].append(s.id)
        stats["long_segments"] = len(long_ids)

        candidates: set[tuple[int, int]] = set()
        for ids in grid.values():
            m = len(ids)
            for ii in range(m):
                for jj in range(ii + 1, m):
                    a_, b_ = ids[ii], ids[jj]
                    candidates.add((a_, b_) if a_ < b_ else (b_, a_))
            if len(candidates) > max_pairs:
                raise ArrangementError("PAIR_LIMIT", f"more than {max_pairs} candidate pairs")
        long_set = set(long_ids)
        for lid in long_ids:
            for s in segs:
                if s.id != lid and (s.id not in long_set or s.id > lid):
                    candidates.add((lid, s.id) if lid < s.id else (s.id, lid))
            if len(candidates) > max_pairs:
                raise ArrangementError("PAIR_LIMIT", f"more than {max_pairs} candidate pairs")

        def bbox(s: _Seg) -> tuple[float, float, float, float]:
            return (min(s.p[0], s.q[0]), min(s.p[1], s.q[1]), max(s.p[0], s.q[0]), max(s.p[1], s.q[1]))

        for ia, ib in sorted(candidates):
            a, b = segs[ia], segs[ib]
            ba, bb = bbox(a), bbox(b)
            if ba[2] + eps < bb[0] or bb[2] + eps < ba[0] or ba[3] + eps < bb[1] or bb[3] + eps < ba[1]:
                continue
            pairs_tested += 1
            ev_a, ev_b, links = _pair_events(a, b, eps)
            for ra, rb in links:
                uf.union(ra, rb)
            for t, x, y in ev_a:
                events[ia].append((t, new_rec(x, y, None)))
                n_events += 1
            for t, x, y in ev_b:
                events[ib].append((t, new_rec(x, y, None)))
                n_events += 1

    stats["pairs_tested"] = pairs_tested
    stats["events"] = n_events
    stats["cell_size"] = cell

    # ---- cluster records into nodes (transitive within eps)
    gx: dict[tuple[int, int], list[int]] = defaultdict(list)
    for r in range(len(rx)):
        gx[(math.floor(rx[r] / eps), math.floor(ry[r] / eps))].append(r)
    for r in range(len(rx)):
        cx, cy = math.floor(rx[r] / eps), math.floor(ry[r] / eps)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for r2 in gx.get((cx + dx, cy + dy), ()):
                    if r2 > r and math.hypot(rx[r] - rx[r2], ry[r] - ry[r2]) <= eps:
                        uf.union(r, r2)

    members: dict[int, list[int]] = defaultdict(list)
    for r in range(len(rx)):
        members[uf.find(r)].append(r)

    def representative(ms: list[int]) -> tuple[float, float]:
        verts = [r for r in ms if rvert[r] is not None]
        pool = verts if verts else ms
        r = min(pool, key=lambda k: (rx[k], ry[k]))
        return rx[r], ry[r]

    reps = {root: representative(ms) for root, ms in members.items()}
    order = sorted(reps, key=lambda root: (reps[root][0], reps[root][1], root))
    node_of_root = {root: i for i, root in enumerate(order)}

    def node_of(r: int) -> int:
        return node_of_root[uf.find(r)]

    # ---- per-segment node sequences -> edges and facts
    edge_refs: dict[tuple[int, int], list[SegRef]] = defaultdict(list)
    through: dict[int, set[tuple[int, int]]] = defaultdict(set)
    non_monotone = 0
    for s in segs:
        seq = [(0.0, s.rp)] + sorted(events[s.id]) + [(1.0, s.rq)]
        runs: list[list[tuple[float, int]]] = []   # consecutive records sharing a node
        for t, r in seq:
            nid = node_of(r)
            if runs and node_of(runs[-1][-1][1]) == nid:
                runs[-1].append((t, r))
            else:
                runs.append([(t, r)])
        node_seq = [node_of(run[0][1]) for run in runs]
        if len(set(node_seq)) != len(node_seq):
            non_monotone += 1
        for k in range(len(runs) - 1):
            n0, n1 = node_seq[k], node_seq[k + 1]
            ref = SegRef(s.prim_id, s.idx, runs[k][-1][0], runs[k + 1][0][0])
            key = (n0, n1) if n0 < n1 else (n1, n0)
            edge_refs[key].append(ref)
        for nid in node_seq[1:-1]:
            if nid != node_seq[0] and nid != node_seq[-1]:
                through[nid].add((s.prim_id, s.idx))

    edges: list[Edge] = []
    incident: dict[int, list[int]] = defaultdict(list)
    for eid, key in enumerate(sorted(edge_refs)):
        refs = tuple(sorted(edge_refs[key], key=lambda r: (r.prim_id, r.seg_idx, r.t0, r.t1)))
        edges.append(Edge(eid, key[0], key[1], refs))
        incident[key[0]].append(eid)
        incident[key[1]].append(eid)

    nodes: list[Node] = []
    for root in order:
        nid = node_of_root[root]
        verts = tuple(sorted({rvert[r] for r in members[root] if rvert[r] is not None}))  # type: ignore[type-var]
        nodes.append(Node(nid, reps[root], verts, tuple(sorted(through.get(nid, ()))),
                          tuple(incident.get(nid, ()))))

    stats["records"] = len(rx)
    stats["nodes"] = len(nodes)
    stats["edges"] = len(edges)
    stats["non_monotone_segments"] = non_monotone
    prim_info = {p.id: (len(p.pts), p.line_closed) for p in prims if p.kind == "polyline"}
    return ArrangementGraph(nodes, edges, points, eps, stats, prim_info)


# ------------------------------------------------------------------ continuity facts

def arm_relations(graph: ArrangementGraph, node_id: int) -> dict[tuple[int, int], str]:
    """Relation between every pair of arms (edges) at a node.

    This is what lets later stages distinguish *continuity of one PDF path* from
    *endpoint coincidence between independent primitives* (v2.1 condition 3).

    SAME_PATH_VERTEX is reported only when some source segment of one arm and some source
    segment of the other arm belong to the same primitive, are consecutive segments of it
    (seg_idx differs by 1, or wrap-around of a closed path), and the shared polyline
    vertex is located at this node. SAME_SEGMENT_THROUGH: both arms are pieces of the same
    straight segment (it merely passes through the node). Any other pairing within one
    primitive is SAME_PATH_OTHER. A pair with no shared primitive is INDEPENDENT_PRIMS.
    """
    node = graph.nodes[node_id]
    edges = {e.id: e for e in graph.edges}
    vert_set = set(node.vertices)
    out: dict[tuple[int, int], str] = {}
    arms = list(node.edges)
    for i in range(len(arms)):
        for j in range(i + 1, len(arms)):
            e1, e2 = edges[arms[i]], edges[arms[j]]
            rel = REL_INDEPENDENT
            for r1 in e1.refs:
                for r2 in e2.refs:
                    if r1.prim_id != r2.prim_id:
                        continue
                    if rel == REL_INDEPENDENT:
                        rel = REL_SAME_PATH_OTHER
                    if r1.seg_idx == r2.seg_idx and rel != REL_SAME_PATH_VERTEX:
                        rel = REL_SAME_SEGMENT
                    n, closed = graph.prim_info.get(r1.prim_id, (0, False))
                    sv = _shared_vertex(r1.seg_idx, r2.seg_idx, n, closed)
                    if sv is not None and (r1.prim_id, sv) in vert_set:
                        rel = REL_SAME_PATH_VERTEX
            out[(arms[i], arms[j])] = rel
    return out


def _shared_vertex(i: int, j: int, n: int, closed: bool) -> int | None:
    """Vertex shared by segments i and j of one path with n vertices, else None.

    Segment k joins vertex k to vertex (k+1) % n, so consecutive segments lo, lo+1 share
    vertex lo+1; the closing segment n-1 and segment 0 share vertex 0 (closed paths only).
    """
    lo, hi = (i, j) if i <= j else (j, i)
    if hi - lo == 1:
        return hi
    if closed and n >= 3 and lo == 0 and hi == n - 1:
        return 0
    return None


def independent_prims_at(graph: ArrangementGraph, node_id: int) -> frozenset[int]:
    """Distinct primitive ids contributing arms at a node (count THESE, not arms)."""
    edges = {e.id: e for e in graph.edges}
    return frozenset(r.prim_id for eid in graph.nodes[node_id].edges for r in edges[eid].refs)
