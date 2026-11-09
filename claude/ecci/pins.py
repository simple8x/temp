"""Pin hypotheses from independent sources (M2).

S1  template: body cluster x library => predicted pin tips (lineage: lib token + body prims).
S2  measured stub: a WIRE-class segment spanning predicted base->tip (lineage: that prim only).
S3  terminal contact: WIRE-class segment ends touching a body outline that no pin explains.
S4  text: NOT implemented in M2 (it may only ever add pin-number metadata, never positions).

Position state per pin (pos_state):
  CONFIRMED  >= 2 pairwise-independent usable items agree on the position (S1 + S2 here),
  CANDIDATE  exactly one usable source,
  NONE       nothing usable (ambiguous or absent template),
and cluster-level CONFLICT flags for contacts that the matched symbol does not explain.
S1 and S2 are independent only if their lineages are disjoint: if the stub primitive is itself
part of the matched body cluster, S1 already used it, so the pair stays dependent (CANDIDATE).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .classify import LC_WIRE, PageClassification
from .evidence import EvidenceItem, decide_role
from .flatten import point_segment_distance
from .ir import PageIngest, SRC_VECTOR
from .symbols import Cluster, body_clusters, relaxations
from .template import MatchParams, Resolution, match_cluster, resolve, tolerance

POS_CONFIRMED = "CONFIRMED"
POS_CANDIDATE = "CANDIDATE"
POS_NONE = "NONE"


@dataclass(frozen=True)
class PinResult:
    cluster_id: int
    tip: tuple[float, float]
    base: tuple[float, float]
    numbers: tuple[str, ...]
    pos_state: str
    id_state: str
    evidence: tuple[str, ...]
    independent_items: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class UnresolvedTerminal:
    cluster_id: int
    contact: tuple[float, float]       # where the wire end touches the body outline
    wire_prim: int
    far_end: tuple[float, float]       # other end of the wire segment (NOT claimed to be a tip)
    evidence: str


@dataclass(frozen=True)
class PinConflict:
    cluster_id: int
    kind: str                           # "UNEXPECTED_CONTACT"
    point: tuple[float, float]
    wire_prim: int


@dataclass
class ClusterResult:
    cluster: Cluster
    resolution: Resolution
    pins: list[PinResult] = field(default_factory=list)
    unresolved: list[UnresolvedTerminal] = field(default_factory=list)
    conflicts: list[PinConflict] = field(default_factory=list)


@dataclass(frozen=True)
class PinParams:
    match: MatchParams = MatchParams()
    cluster_gap_frac: float = 0.1     # see symbols.body_clusters; 0.5 chained whole KiCad sheets (gap sweep in CI), 0.1 does not (ASSUMPTION)
    s2_rel: float = 0.1               # endpoint tolerance as a fraction of the pin length
    contact_abs: float = 0.05


def _add(reg, item: EvidenceItem) -> None:
    """Register evidence; re-registering an identical item (repeated detect_pins) is a no-op."""
    existing = reg.items.get(item.id)
    if existing is None:
        reg.add(item)
    elif existing != item:
        raise ValueError(f"conflicting evidence for id {item.id!r}")


class _SegIndex:
    """Uniform grid over wire segments so each cluster only sees nearby wires."""

    def __init__(self, segs, cell: float = 40.0) -> None:
        self.segs = segs
        self.cell = cell
        self.grid: dict[tuple[int, int], list[int]] = {}
        for k, (_pid, _si, a, b) in enumerate(segs):
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            for cx in range(math.floor(x0 / cell), math.floor(x1 / cell) + 1):
                for cy in range(math.floor(y0 / cell), math.floor(y1 / cell) + 1):
                    self.grid.setdefault((cx, cy), []).append(k)

    def query(self, bbox, margin: float):
        c = self.cell
        seen: set[int] = set()
        for cx in range(math.floor((bbox[0] - margin) / c), math.floor((bbox[2] + margin) / c) + 1):
            for cy in range(math.floor((bbox[1] - margin) / c), math.floor((bbox[3] + margin) / c) + 1):
                seen.update(self.grid.get((cx, cy), ()))
        return [self.segs[k] for k in sorted(seen)]


def _wire_segments(pi: PageIngest, cls: PageClassification, exclude: set[int]):
    out = []
    for p in pi.prims:
        if cls.records[p.id].cls == LC_WIRE and p.id not in exclude:
            for si, a, b in p.segments():
                out.append((p.id, si, a, b))
    return out


def detect_pins(pi: PageIngest, cls: PageClassification, symbols, params: PinParams = PinParams(),
                eps: float = 0.02, exclude: frozenset = frozenset()) -> list[ClusterResult]:
    reg = cls.registry
    results: list[ClusterResult] = []
    syms = list(symbols)
    index = _SegIndex(_wire_segments(pi, cls, set()))
    prim_by_id = {q.id: q for q in pi.prims}
    for cl in body_clusters(pi, cls, eps, params.cluster_gap_frac, exclude):
        hits = match_cluster(cl, syms, params.match) if syms else []
        stub_ids: tuple[int, ...] = ()
        if syms and not hits:        # KiCad draws pin lines in the body colour and adds names, marks and bubbles
            for cl2, stubs2 in relaxations(cl, prim_by_id, eps):
                hits2 = match_cluster(cl2, syms, params.match)
                if hits2:
                    cl, stub_ids, hits = cl2, stubs2, hits2
                    break
        res = resolve(cl, hits, params.match)
        cr = ClusterResult(cl, res)
        tau = tolerance(cl, params.match)
        body_ids = set(cl.prim_ids)
        diag = math.hypot(cl.bbox[2] - cl.bbox[0], cl.bbox[3] - cl.bbox[1])
        # pins are at most about one body diagonal long; contacts are within a stroke width
        wires = [w for w in index.query(cl.bbox, max(2.0 * diag, 5.0)) if w[0] not in body_ids]
        explained: list[tuple[tuple[float, float], float]] = []
        stroke = {q.id: q.stroke_w for q in pi.prims}

        if res.status == "UNIQUE" and res.pins:
            lib_tokens = {h.symbol.lib_token for h in res.hits}
            for k, rp in enumerate(res.pins):
                base_lineage = set(lib_tokens) | {f"geom:{i}" for i in cl.prim_ids}
                s1 = EvidenceItem(f"S1:{cl.id}:{k}", "TEMPLATE_PIN", SRC_VECTOR, frozenset(base_lineage))
                _add(reg, s1)
                items = [s1.id]
                plen = math.hypot(rp.tip[0] - rp.base[0], rp.tip[1] - rp.base[1])
                explained.append((rp.base, max(params.match.tau_abs, params.s2_rel * plen)))
                if plen > 0:
                    t2 = max(params.match.tau_abs, params.s2_rel * plen)
                    stub_segs = [(sid, 0, q.pts[0], q.pts[-1]) for sid in stub_ids for q in [prim_by_id[sid]]]
                    for pid, si, a, b in list(wires) + stub_segs:
                        fwd = math.hypot(a[0] - rp.base[0], a[1] - rp.base[1]) <= t2 and \
                            math.hypot(b[0] - rp.tip[0], b[1] - rp.tip[1]) <= t2
                        rev = math.hypot(b[0] - rp.base[0], b[1] - rp.base[1]) <= t2 and \
                            math.hypot(a[0] - rp.tip[0], a[1] - rp.tip[1]) <= t2
                        if fwd or rev:
                            s2 = EvidenceItem(f"S2:{cl.id}:{k}", "MEASURED_STUB", SRC_VECTOR,
                                              frozenset({f"geom:{pid}"}))
                            _add(reg, s2)
                            items.append(s2.id)
                            break
                dec = decide_role(items, reg)
                state = {"CONFIRMED": POS_CONFIRMED, "CANDIDATE": POS_CANDIDATE}.get(dec.status, POS_NONE)
                cr.pins.append(PinResult(cl.id, rp.tip, rp.base, rp.numbers, state, res.id_state,
                                         tuple(items), dec.independent_items, dec.excluded))

        # S3: contacts of WIRE-class segment ends with the body outline
        for pid, si, a, b in wires:
            for ei, (end, far) in enumerate(((a, b), (b, a))):
                ctol = params.contact_abs + 0.5 * stroke.get(pid, 0.0)
                d = min((point_segment_distance(end, s0, s1) for s0, s1 in cl.segs), default=math.inf)
                if d > ctol:
                    continue
                if any(math.hypot(end[0] - e[0], end[1] - e[1]) <= max(ctol, et) for e, et in explained):
                    continue
                if res.status == "UNIQUE" and res.pins:
                    cr.conflicts.append(PinConflict(cl.id, "UNEXPECTED_CONTACT", end, pid))
                else:
                    eid = f"S3:{cl.id}:{pid}:{si}:{ei}"
                    _add(reg, EvidenceItem(eid, "TERMINAL_CONTACT", SRC_VECTOR,
                                           frozenset({f"geom:{pid}"} | {f"geom:{i}" for i in cl.prim_ids})))
                    cr.unresolved.append(UnresolvedTerminal(cl.id, end, pid, far, eid))
        results.append(cr)
    return results
