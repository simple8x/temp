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


def _role(obj: str, kind: str, status: str, items, weak: bool) -> RoleRef:
    return RoleRef(obj, kind, status, tuple(items), weak)


def _wire_role(cls: PageClassification, pid: int) -> RoleRef:
    rec = cls.records[pid]
    dec = decide_role(list(rec.evidence), cls.registry)
    weak = any(cls.registry.items[i].source in WEAK_SOURCES for i in rec.evidence if i in cls.registry.items)
    return _role(f"w:{pid}", "WIRE", dec.status, dec.independent_items, weak)


def _dir_label(dx: float, dy: float) -> str | None:
    """Axis label in page space (y down): N is up. None if the arm is not axis-aligned enough."""
    ax, ay = abs(dx), abs(dy)
    if max(ax, ay) <= 0 or min(ax, ay) > 0.2 * max(ax, ay):
        return None
    if ax >= ay:
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


def _is_disc(p, W: float, H: float, prm: RulesParams):
    if not (p.fill and p.line_closed and p.kind == "polyline" and len(p.pts) >= prm.dot_min_vertices):
        return None
    xs = [q[0] for q in p.pts]
    ys = [q[1] for q in p.pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if max(w, h) <= 0 or math.hypot(w, h) > prm.dot_max_diag_frac * math.hypot(W, H):
        return None
    if min(w, h) / max(w, h) < prm.dot_aspect:
        return None
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    rs = [math.hypot(x - cx, y - cy) for x, y in p.pts]
    rm = sum(rs) / len(rs)
    if rm <= 0 or (max(rs) - min(rs)) / rm > prm.dot_roundness:
        return None
    return (cx, cy), rm


def _proper_cross(a, b, c, d, eps: float) -> tuple[float, float] | None:
    """Intersection of segments ab and cd if they cross at points interior to both (> eps from all ends)."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if not (0 < t < 1 and 0 < u < 1):
        return None
    x, y = a[0] + t * rx, a[1] + t * ry
    for e in (a, b, c, d):
        if math.hypot(x - e[0], y - e[1]) <= eps:
            return None
    return x, y


def find_dots(pi: PageIngest, cls: PageClassification, prm: RulesParams = RulesParams()) -> dict:
    """Filled, round, small BODY primitives that touch no other BODY primitive (so they are not
    part of a symbol: TC-X3). Returns dot object id -> (centre, radius, prim id)."""
    W, H = pi.geom.width, pi.geom.height
    body = [p for p in pi.prims if cls.records[p.id].cls == LC_BODY and p.kind == "polyline"]
    cand = {p.id: _is_disc(p, W, H, prm) for p in body}
    cand = {i: d for i, d in cand.items() if d is not None}
    if not cand:
        return {}
    g = build_arrangement(body, prm.eps)
    touched: set[int] = set()
    for n in g.nodes:
        ids = independent_prims_at(g, n.id)
        if len(ids) > 1:
            touched |= set(ids)
    return {f"d:{i}": (d[0], d[1], i) for i, d in sorted(cand.items()) if i not in touched}


def build_conn_graph(pi: PageIngest, cls: PageClassification, clusters: list[ClusterResult],
                     prm: RulesParams = RulesParams(), dots: dict | None = None) -> BuildResult:
    W, H = pi.geom.width, pi.geom.height
    by_id = {p.id: p for p in pi.prims}
    reg = cls.registry
    notes: list[str] = []
    objects: list[Obj] = []
    edges: list[Edge] = []
    sites: list[Site] = []
    cannot: list[tuple[str, str, str]] = []
    obj_prims: dict[str, tuple] = {}
    pin_tips: dict[str, tuple] = {}

    def prov_of(pids) -> tuple[str, ...]:
        return tuple(by_id[i].prov.content_ref for i in sorted(set(pids)) if i in by_id)

    # ---- wire objects
    wire_ids = sorted(i for i, r in cls.records.items() if r.cls == LC_WIRE)
    wrole = {i: _wire_role(cls, i) for i in wire_ids}
    for i in wire_ids:
        objects.append(Obj(f"w:{i}", "WIRE"))
        obj_prims[f"w:{i}"] = (i,)
    wprims = [by_id[i] for i in wire_ids]
    g = build_arrangement(wprims, prm.eps) if wprims else None

    # ---- junction dots (found by find_dots: filled discs touching no other BODY primitive)
    if dots is None:
        dots = find_dots(pi, cls, prm)
    dot_clusters: set[int] = set()

    node_wires: dict[int, list[int]] = {}
    node_dot: dict[int, str] = {}
    dot_info: dict[str, tuple] = {}
    if g is not None:
        for n in g.nodes:
            node_wires[n.id] = sorted(independent_prims_at(g, n.id))
        for did, (ctr, rad, pid) in sorted(dots.items()):
            tol = max(prm.dot_node_tol_abs, prm.dot_node_tol_rel * rad)
            best = None
            for n in g.nodes:
                if not n.edges:
                    continue
                d = math.hypot(n.xy[0] - ctr[0], n.xy[1] - ctr[1])
                if d <= tol and (best is None or d < best[0]):
                    best = (d, n.id)
            dot_info[did] = (ctr, rad, best[1] if best else None)
            if best and best[1] not in node_dot:
                node_dot[best[1]] = did
    for did, (ctr, rad, pid) in sorted(dots.items()):
        objects.append(Obj(did, "DOT"))
        obj_prims[did] = (pid,)
        dot_info.setdefault(did, (ctr, rad, None))
    node_of_dot = {d: n for n, d in node_dot.items()}

    def dot_role(did: str, arm_prims: list[int], arms: int) -> RoleRef:
        pid = dots[did][2]
        items = []
        d1 = EvidenceItem(f"DOT_DISC:{pid}", "DOT_DISC", SRC_VECTOR, frozenset({f"geom:{pid}", f"style:{pid}"}))
        if d1.id not in reg.items:
            reg.add(d1)
        items.append(d1.id)
        if arms >= 3 and all(wrole[i].status == ROLE_CONFIRMED for i in arm_prims):
            d2 = EvidenceItem(f"DOT_ARMS:{pid}", "JUNCTION_ARMS", SRC_VECTOR,
                              frozenset({f"geom:{i}" for i in arm_prims}))
            if d2.id not in reg.items:
                reg.add(d2)
            items.append(d2.id)
        dec = decide_role(items, reg)
        return _role(did, "DOT", dec.status, dec.independent_items, False)

    # ---- node rules over the wire arrangement
    dangling: list[tuple[int, int]] = []      # (node id, prim id) wire ends with a single arm
    if g is not None:
        eby = {e.id: e for e in g.edges}
        for n in g.nodes:
            arms = len(n.edges)
            prims = node_wires[n.id]
            xy = n.xy
            coord = f"node:{n.id}@{xy[0]:.3f},{xy[1]:.3f}"
            if arms == 1 and len(prims) == 1:
                dangling.append((n.id, prims[0]))
                continue
            if len(prims) < 2:
                continue
            did = node_dot.get(n.id)
            if did is not None and arms >= 3:
                drole = dot_role(did, prims, arms)
                for pid in prims:
                    edges.append(Edge(f"tr2:{n.id}:{pid}", f"w:{pid}", did, "TR2", (wrole[pid], drole),
                                      coord, len(prims), (), prov_of(list(prims) + [dots[did][2]])))
                continue
            vset = {v[0] for v in n.vertices}
            if arms == 2 and len(prims) == 2 and all(p in vset for p in prims):
                a, b = prims
                edges.append(Edge(f"tr1:{n.id}:{a}|{b}", f"w:{a}", f"w:{b}", "TR1", (wrole[a], wrole[b]),
                                  coord, 2, (), prov_of(prims)))
            elif arms == 3:
                for a, b in zip(prims, prims[1:]):
                    edges.append(Edge(f"tr4:{n.id}:{a}|{b}", f"w:{a}", f"w:{b}", "TR4", (wrole[a], wrole[b]),
                                      coord, len(prims), (), prov_of(prims)))
            elif arms >= 4:
                lab: dict[str, str] = {}
                arm_objs: list[str] = []
                ok = arms == 4
                for eid in n.edges:
                    e = eby[eid]
                    other = e.n1 if e.n0 == n.id else e.n0
                    ox, oy = g.nodes[other].xy
                    pid = min(r.prim_id for r in e.refs)
                    arm_objs.append(f"w:{pid}")
                    L = _dir_label(ox - xy[0], oy - xy[1])
                    if L is None or L in lab:
                        ok = False
                    else:
                        lab[L] = f"w:{pid}"
                sid = f"site:{n.id}"
                if ok and set(lab) == {"N", "E", "S", "W"}:
                    sites.append(fourway_site(sid, lab))
                else:
                    objs_ = sorted(set(arm_objs))
                    allp = frozenset(_pair(x, y) for i, x in enumerate(objs_) for y in objs_[i + 1:])
                    sites.append(Site(sid, "MULTI", tuple((f"a{i}", o) for i, o in enumerate(arm_objs)),
                                      (Option("ALL", allp), Option("NONE", frozenset()))))
                notes.append(f"site {sid}: {arms} arms, {len(prims)} prims, no dot at {coord}")

    # ---- pins
    pin_objs_of: dict[int, list[str]] = defaultdict(list)
    ut_objs_of: dict[int, list[str]] = defaultdict(list)
    wire_segs = [(p.id, a, b) for p in wprims for _, a, b in p.segments()]
    for cr in clusters:
        cid = cr.cluster.id
        if cid in dot_clusters:
            continue
        for k, pin in enumerate(cr.pins):
            pobj = f"p:{cid}:{k}"
            objects.append(Obj(pobj, "PIN"))
            obj_prims[pobj] = tuple(cr.cluster.prim_ids)
            pin_objs_of[cid].append(pobj)
            pin_tips[pobj] = (pin.tip[0], pin.tip[1])
            st = {POS_CONFIRMED: ROLE_CONFIRMED, POS_CANDIDATE: ROLE_CANDIDATE}.get(pin.pos_state, ROLE_UNKNOWN)
            weak = any(reg.items[i].source in WEAK_SOURCES for i in pin.evidence if i in reg.items)
            prole = _role(pobj, "PIN", st, pin.independent_items, weak)
            coord = f"pin:{cid}:{k}@{pin.tip[0]:.3f},{pin.tip[1]:.3f}"
            stub = None
            for ev in pin.evidence:
                if ev.startswith("S2:") and ev in reg.items:
                    for tok in sorted(reg.items[ev].lineage):
                        if tok.startswith("geom:") and int(tok[5:]) in wrole:
                            stub = int(tok[5:])
            if stub is not None:
                edges.append(Edge(f"tr3:{pobj}:{stub}", pobj, f"w:{stub}", "TR3", (prole, wrole[stub]),
                                  coord, 1, (), prov_of([stub] + list(cr.cluster.prim_ids))))
                continue
            plen = math.hypot(pin.tip[0] - pin.base[0], pin.tip[1] - pin.base[1])
            tol = max(prm.tip_tol_abs, prm.tip_tol_rel * plen)
            near: list[int] = []
            for p in wprims:
                if any(point_segment_distance(pin.tip, a, b) <= tol for _, a, b in p.segments()):
                    near.append(p.id)
            if not near:
                continue
            rule, n = ("TR3", 1) if len(near) == 1 and _is_end(by_id[near[0]], pin.tip, tol) else ("TR4", len(near))
            for pid in near:
                edges.append(Edge(f"{rule.lower()}:{pobj}:{pid}", pobj, f"w:{pid}", rule, (prole, wrole[pid]),
                                  coord, n, (), prov_of([pid] + list(cr.cluster.prim_ids))))

        for u in cr.unresolved:
            uobj = f"u:{u.evidence}"
            objects.append(Obj(uobj, "UNRESOLVED", attach=f"w:{u.wire_prim}"))
            obj_prims[uobj] = (u.wire_prim,) + tuple(cr.cluster.prim_ids)
            ut_objs_of[cid].append(uobj)

    # ---- C4: wires crossing or touching a symbol body away from its pins
    for cr in clusters:
        cid = cr.cluster.id
        # C4 only against pins of a template-resolved symbol. An unresolved cluster has no known
        # pin positions, so 'not connected to the symbol' cannot be stated (it would also make a
        # wire cannot-link with its own attached terminal).
        related = pin_objs_of.get(cid, [])
        if cid in dot_clusters or not related:
            continue
        own = set()
        for pin in cr.pins:
            for ev in pin.evidence:
                if ev.startswith("S2:") and ev in reg.items:
                    own |= {int(t[5:]) for t in reg.items[ev].lineage if t.startswith("geom:")}
        bx0, by0, bx1, by1 = cr.cluster.bbox
        offenders: dict[int, str] = {}
        for pid, a, b in wire_segs:
            if pid in own:
                continue
            if max(a[0], b[0]) < bx0 - prm.eps or min(a[0], b[0]) > bx1 + prm.eps or \
                    max(a[1], b[1]) < by0 - prm.eps or min(a[1], b[1]) > by1 + prm.eps:
                continue
            for c, d in cr.cluster.segs:
                if _proper_cross(a, b, c, d, prm.eps) is not None:
                    offenders[pid] = "wire crosses the symbol body"
                    break
        for c in cr.conflicts:
            offenders.setdefault(c.wire_prim, "wire touches the symbol body away from any pin")
        for pid, why in sorted(offenders.items()):
            for r in related:
                cannot.append((f"w:{pid}", r, f"C4 cluster {cid}: {why}"))

    # ---- TR5 gaps between dangling ends
    tip_pts = [pin.tip for cr in clusters for pin in cr.pins]
    ends = []
    for nid, pid in dangling:
        n = g.nodes[nid]
        if any(math.hypot(n.xy[0] - t[0], n.xy[1] - t[1]) <= max(prm.tip_tol_abs, 0.5) for t in tip_pts):
            continue
        if nid in node_dot:
            continue
        e = g.edges[n.edges[0]]
        other = e.n1 if e.n0 == nid else e.n0
        ox, oy = g.nodes[other].xy
        L = _dir_label(n.xy[0] - ox, n.xy[1] - oy)           # direction the end points to
        if L is not None:
            ends.append((nid, pid, n.xy, L))
    opp = {"N": "S", "S": "N", "E": "W", "W": "E"}
    for i, (n1, p1, x1, L1) in enumerate(ends):
        for n2, p2, x2, L2 in ends[i + 1:]:
            if p1 == p2 or L2 != opp[L1]:
                continue
            horiz = L1 in "EW"
            perp = abs(x1[1] - x2[1]) if horiz else abs(x1[0] - x2[0])
            gap = (x2[0] - x1[0]) if horiz else (x2[1] - x1[1])
            gap = gap if L1 in "ES" else -gap
            if perp > prm.eps or not (prm.eps < gap <= prm.g_max):
                continue
            if any(q not in (p1, p2) and _proper_cross(x1, x2, a, b, 0.0) is not None
                   for q, a, b in wire_segs):
                continue
            a, b = sorted((p1, p2))
            edges.append(Edge(f"tr5:{a}|{b}:{n1}|{n2}", f"w:{a}", f"w:{b}", "TR5", (wrole[a], wrole[b]),
                              f"gap:{x1[0]:.3f},{x1[1]:.3f}->{x2[0]:.3f},{x2[1]:.3f}", 0, (), prov_of([a, b])))

    unsupported = ["net names / labels (no text content extracted): TR6, C1, C2 inactive on PDFs",
                   "no-connect markers not detected: C5 inactive on PDFs",
                   "sheet-scope, power symbols and hierarchical labels not modelled"]
    return BuildResult(ConnGraph(objects, edges, sites, Constraints(cannot_link=tuple(cannot))),
                       notes, unsupported, dot_info, obj_prims, pin_tips)


def _is_end(p, pt, tol: float) -> bool:
    if p.line_closed:
        return False
    return min(math.hypot(pt[0] - p.pts[0][0], pt[1] - p.pts[0][1]),
               math.hypot(pt[0] - p.pts[-1][0], pt[1] - p.pts[-1][1])) <= tol


def analyze_page(pi: PageIngest, symbols, prm: RulesParams = RulesParams(),
                 pin_params: PinParams = PinParams()):
    """Convenience pipeline for one page: classify -> pins -> candidate graph -> solve."""
    from .solver import solve
    cls = classify_page(pi)
    dots = find_dots(pi, cls, prm)
    clusters = detect_pins(pi, cls, list(symbols), pin_params, prm.eps,
                           frozenset(v[2] for v in dots.values()))
    built = build_conn_graph(pi, cls, clusters, prm, dots)
    return cls, clusters, built, solve(built.graph)
