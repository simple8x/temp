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
import re
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


def _part_stem(name: str) -> str:
    """'SIM4X32_2', 'SIM4X32#u2' and 'video_schlib:SIM4X32' are units / per-sheet copies of the part 'SIM4X32'.
    The library nickname before ':' is not part of the part name (KiCad embeds the same part under both forms)."""
    return re.sub(r"(#u\d+|_\d+)$", "", name.rsplit(":", 1)[-1])


def _line_index(pi: PageIngest, cls: PageClassification, cell: float = 20.0):
    """Grid over the end points of every open 2-point non-text line (pin lines are plotted as plain lines)."""
    grid: dict[tuple[int, int], list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    for q in pi.prims:
        if q.kind != "polyline" or q.line_closed or len(q.pts) != 2 or cls.records[q.id].cls == "TEXT":
            continue
        a, b = q.pts[0], q.pts[-1]
        for end, far in ((a, b), (b, a)):
            grid.setdefault((math.floor(end[0] / cell), math.floor(end[1] / cell)), []).append((end, far))
    return grid, cell


def stub_support(hit, lines, params: "PinParams") -> list[tuple[tuple[float, float], str]]:
    """Pins of `hit` WITHOUT a drawn pin line: (tip, reason). A pin line has one end at the tip, is collinear with
    the pin axis and has a length of 0.5 to 1.3 pin lengths (a bubble may shorten the line). Uses only measured
    lines, no library data other than the predicted pin positions. Empty list = every pin is drawn."""
    grid, cell = lines
    pins = [(hit.place(*p.tip), hit.place(*p.base)) for p in hit.symbol.pins]
    if not pins:
        return [((0.0, 0.0), "no pins")]
    missing = []
    for tip, base in pins:
        plen = math.hypot(tip[0] - base[0], tip[1] - base[1])
        if plen <= 0:
            missing.append((tip, "zero length"))
            continue
        t2 = max(params.match.tau_abs, params.s2_rel * plen)
        ux, uy = (base[0] - tip[0]) / plen, (base[1] - tip[1]) / plen
        ok, why = False, "no line end at tip"
        gx, gy = math.floor(tip[0] / cell), math.floor(tip[1] / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for end, far in grid.get((gx + dx, gy + dy), ()):
                    if math.hypot(end[0] - tip[0], end[1] - tip[1]) > t2:
                        continue
                    vx, vy = far[0] - end[0], far[1] - end[1]
                    ln = math.hypot(vx, vy)
                    why = f"line length {ln:.2f} vs pin {plen:.2f}"
                    if not (0.5 * plen <= ln <= 1.3 * plen):
                        continue
                    if abs(vx * uy - vy * ux) <= t2 and vx * ux + vy * uy > 0:
                        ok = True
                    else:
                        why = "line not along the pin axis"
        if not ok:
            missing.append((tip, why))
    return missing


def _stub_supported(hit, lines, params: "PinParams") -> bool:
    """True when EVERY pin of `hit` has a drawn pin line (see stub_support)."""
    return not stub_support(hit, lines, params)


def detect_pins(pi: PageIngest, cls: PageClassification, symbols, params: PinParams = PinParams(),
                eps: float = 0.02, exclude: frozenset = frozenset()) -> list[ClusterResult]:
    reg = cls.registry
    results: list[ClusterResult] = []
    syms = list(symbols)
    index = _SegIndex(_wire_segments(pi, cls, set()))
    prim_by_id = {q.id: q for q in pi.prims}
    lines = None
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
        stub_selected = False
        if res.status == "DIFFERENT_TIP_SETS" and len({_part_stem(h.symbol.name) for h in res.hits}) == 1:
            # units of ONE library part share a body and differ in their pins: the drawn pin lines pick the unit.
            # The part is the same either way; the lines were used for the choice, so S2 is not independent.
            if lines is None:
                lines = _line_index(pi, cls)
            sel = [h for h in res.hits if _stub_supported(h, lines, params)]
            if sel and len(sel) < len(res.hits):
                res2 = resolve(cl, sel, params.match)
                if res2.status == "UNIQUE":
                    res, hits, stub_selected = res2, sel, True
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
                if plen > 0 and not stub_selected:
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
