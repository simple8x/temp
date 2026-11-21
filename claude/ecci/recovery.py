"""Isolated grouped-recovery candidates (P0).

The baseline (classify -> detect_pins -> conn graph -> solve) is READ-ONLY input here. Recovery never mutates a
baseline cluster, pin, evidence registry entry, edge or net, never registers evidence and never feeds the
connectivity graph. Its only output is a RecoveryReport of candidates, each with full lineage and an explicit state.

Pipeline per page
  1. lineage check   recompute the baseline body clusters; every baseline NONE cluster must be reproduced exactly,
                     every resolved cluster must be a subset of its recomputed cluster. Otherwise BLOCKED, no candidates.
  2. propose         only primitives of baseline clusters that matched NO template (status NONE) are used:
                       SPLIT_TOUCH  touching components of a NONE cluster that has more than one
                       MERGE_PAIR   two NONE units (clusters or split components) within the merge reach
                     a group that matches a library template (directly or after a baseline relaxation) is PROPOSED.
  3. evidence gate   per pin, a DISTINCT measured 2-point vector line (not in the template-fit primitive set) whose
                     two ends agree with the predicted base AND tip within s2_tolerance (both ends, not only the tip).
  4. conflicts       a terminal / line already claimed by the baseline -> REJECTED; candidates that share a
                     primitive, a pin line or a terminal -> every one of them UNKNOWN (no 'best wins').
  5. audit           every EVIDENCE_SUPPORTED candidate is re-derived from the page alone (geometry hash, template
                     re-match, predicted pins, measured lines). Pass -> PROMOTABLE, any mismatch -> REJECTED.

Symbol identity is library-qualified: (lib_token, name). Two library symbols that share a name are different
identities; more than one identity among the accepted alternatives keeps the candidate UNKNOWN. Every retained
(symbol, transform) alternative is validated, not only the first one, and the hit order never decides anything.

Evidence roles (primitive ids, recorded on every candidate, re-derived by the audit):
  discovery     every primitive of the baseline NONE clusters the proposal was built from (candidate discovery)
  proposal      source_prim_ids: the grouped primitives (subset of discovery)
  template_fit  matched_prim_ids: the primitives the template was fitted to
  stripped      stripped_prim_ids: stubs removed by the baseline relaxation (stripped-source interpretation)
  excluded      excluded_prim_ids: the rest of the proposal that the relaxation left out (other style group,
                round decor). template_fit, stripped and excluded partition the proposal EXACTLY; stripped or
                excluded prims without a +RELAX operation, overlaps, duplicates or a gap fail closed.
  pin_line      one measured line per pin. It must be disjoint from discovery (and so from every other role): a
                primitive that helped propose, group, fit or interpret the candidate is never its own physical
                pin evidence. There is no reuse exception (none is documented as independent).

States: PROPOSED, EVIDENCE_SUPPORTED, PROMOTABLE, REJECTED, UNKNOWN. There is NO path to VERIFIED here: PROMOTABLE
is terminal in P0 (no existing authority policy permits candidate -> VERIFIED; see status.py).
"""

from __future__ import annotations

import hashlib
import math
import pickle
import time
from collections import Counter
from dataclasses import dataclass, field

from .classify import LC_BODY, PageClassification
from .flatten import point_segment_distance
from .ir import PageIngest
from .pins import S2_REL_MAX, S2_TAU_ABS_MAX, PinParams, _line_index, s2_tolerance, stub_orientation
from .symbols import EPS_DEFAULT, Cluster, _rebuild, relaxations
from .template import D4, MatchParams, hit_sort_key, match_cluster, resolve_qualified, symbol_identity, tolerance

PROPOSED = "PROPOSED"
EVIDENCE_SUPPORTED = "EVIDENCE_SUPPORTED"
PROMOTABLE = "PROMOTABLE"
REJECTED = "REJECTED"
UNKNOWN = "UNKNOWN"
STATES = (PROPOSED, EVIDENCE_SUPPORTED, PROMOTABLE, REJECTED, UNKNOWN)
ALLOWED = {
    PROPOSED: frozenset({EVIDENCE_SUPPORTED, UNKNOWN, REJECTED}),
    EVIDENCE_SUPPORTED: frozenset({PROMOTABLE, UNKNOWN, REJECTED}),
    PROMOTABLE: frozenset({UNKNOWN, REJECTED}),        # demotion only
    UNKNOWN: frozenset({REJECTED}),
    REJECTED: frozenset(),
}
FRAME = "ingest-normalized page space (pt, y down)"
OP_SPLIT = "SPLIT_TOUCH"
OP_MERGE = "MERGE_PAIR"
ID_OK = frozenset({"CONFIRMED", "PIN_EQUIVALENT_CANONICAL"})

STATUS_OK = "OK"
STATUS_BLOCKED = "BLOCKED"


class IllegalTransition(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryParams:
    touch_eps: float = EPS_DEFAULT          # SPLIT_TOUCH: primitives closer than this are one component
    merge_gap_frac: float = 0.5             # MERGE_PAIR reach: gap <= frac x larger dimension of the smaller unit
    min_part_frac: float = 0.003            # = symbols.body_clusters min_gap_part_frac: smaller units are glyph-like
    max_split_prims: int = 2000             # larger NONE clusters are not split (counted as budget skips)
    max_neighbours: int = 4                 # MERGE_PAIR partners per unit (nearest first)
    max_proposals: int = 4000               # per page; the rest are counted, never silently dropped
    relax: bool = True                      # also try the baseline relaxations (stub / decor stripping)


@dataclass(frozen=True)
class PinEvidence:
    index: int
    numbers: tuple[str, ...]
    predicted_tip: tuple[float, float]
    predicted_base: tuple[float, float]
    tol: float
    line_prim: int | None
    line_pts: tuple | None                  # measured (p0, p1) of line_prim as ingested
    orientation: str | None
    residual: float | None                  # max endpoint distance measured vs predicted
    why: str | None                         # None = supported


@dataclass
class RecoveryCandidate:
    id: str
    operation: str
    source_clusters: tuple[int, ...]
    source_prim_ids: tuple[int, ...]
    matched_prim_ids: tuple[int, ...]
    stripped_prim_ids: tuple[int, ...]
    geometry: tuple                         # ((prim id, ((x, y), ...)), ...) of every source prim, as ingested
    geometry_sha256: str
    frame: str
    tolerances: dict
    hits: tuple[dict, ...]                  # every accepted (symbol, transform) alternative, canonical order
    identity: dict
    pins: tuple[PinEvidence, ...] = ()
    discovery_prim_ids: tuple[int, ...] = ()   # empty = missing lineage -> fails closed
    excluded_prim_ids: tuple[int, ...] = ()
    state: str = PROPOSED
    history: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def to(self, new: str, reason: str) -> None:
        if new not in ALLOWED.get(self.state, ()):
            raise IllegalTransition(f"{self.id}: {self.state} -> {new} ({reason})")
        self.history.append((self.state, new, reason))
        self.state = new
        self.reasons.append(reason)


@dataclass
class RecoveryReport:
    status: str
    candidates: list
    stats: dict
    problems: list

    def by_state(self) -> dict:
        c = Counter(x.state for x in self.candidates)
        return {s: c.get(s, 0) for s in STATES}

    def reason_counts(self) -> dict:
        c = Counter()
        for x in self.candidates:
            if x.state in (REJECTED, UNKNOWN):
                for r in x.reasons:
                    c[r.split(":", 1)[0]] += 1
        return dict(sorted(c.items()))

    def promotable_pins(self) -> list:
        return [(c.id, p) for c in self.candidates if c.state == PROMOTABLE for p in c.pins]


# ----------------------------------------------------------------------------------------------- fingerprints

def baseline_fingerprint(*objs) -> str:
    """Byte-level fingerprint of baseline objects (page, classification incl. evidence registry, cluster results,
    graph, solve result). Equal before and after recovery <=> recovery changed nothing observable."""
    h = hashlib.sha256()
    for o in objs:
        try:
            b = pickle.dumps(o, protocol=4)
        except Exception:                  # unpicklable member: fall back to the full repr (still deterministic)
            b = repr(o).encode()
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return h.hexdigest()


def geometry_of(pi: PageIngest, ids) -> tuple:
    by_id = {q.id: q for q in pi.prims}
    return tuple((i, tuple((float(x), float(y)) for x, y in by_id[i].pts)) for i in sorted(ids))


def geometry_hash(geom: tuple) -> str:
    return hashlib.sha256(repr(geom).encode()).hexdigest()


# ----------------------------------------------------------------------------------------------- geometry helpers

def _bbox_ids(ids, by_id):
    xs = [p[0] for i in ids for p in by_id[i].pts]
    ys = [p[1] for i in ids for p in by_id[i].pts]
    return min(xs), min(ys), max(xs), max(ys)


def _maxdim(b) -> float:
    return max(b[2] - b[0], b[3] - b[1])


def _gap(a, b) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def seg_seg_distance(p, q, r, s) -> float:
    d1, d2, d3, d4 = _orient(p, q, r), _orient(p, q, s), _orient(r, s, p), _orient(r, s, q)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return 0.0
    return min(point_segment_distance(p, r, s), point_segment_distance(q, r, s),
               point_segment_distance(r, p, q), point_segment_distance(s, p, q))


def _segs(prim):
    out = [(a, b) for _si, a, b in prim.segments()]
    if not out and prim.pts:                  # a single point: zero-length segment
        out = [(prim.pts[0], prim.pts[0])]
    return out


def touch_components(ids, by_id, eps: float) -> list[tuple[int, ...]]:
    """Connected components of `ids` where two primitives touch if any of their segments are within eps."""
    ids = sorted(ids)
    parent = {i: i for i in ids}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    boxes = {i: _bbox_ids([i], by_id) for i in ids}
    segs = {i: _segs(by_id[i]) for i in ids}
    order = sorted(ids, key=lambda i: boxes[i][0])
    for k, a in enumerate(order):
        ba = boxes[a]
        for b in order[k + 1:]:
            bb = boxes[b]
            if bb[0] > ba[2] + eps:
                break
            if _gap(ba, bb) > eps or find(a) == find(b):
                continue
            if any(seg_seg_distance(p, q, r, s) <= eps for p, q in segs[a] for r, s in segs[b]):
                ra, rb = find(a), find(b)
                parent[max(ra, rb)] = min(ra, rb)
    comps: dict[int, list[int]] = {}
    for i in ids:
        comps.setdefault(find(i), []).append(i)
    return sorted(tuple(v) for v in comps.values())


def _hit_dict(h) -> dict:
    """pins: (number, placed tip, placed base) of EVERY library pin of this alternative, library order."""
    return {"symbol": h.symbol.name, "lib_token": h.symbol.lib_token, "orient": h.orient, "scale": h.scale,
            "tx": h.tx, "ty": h.ty, "residual": h.residual,
            "pins": tuple((p.number, h.place(*p.tip), h.place(*p.base)) for p in h.symbol.pins)}


def _hits_sorted(hits) -> list:
    return sorted(hits, key=hit_sort_key)


# Explicit numeric tolerance for re-derived pin GEOMETRY only (page points). The re-derivation runs the same code on
# the same ingested page, so any honest record agrees to ~1e-12; 1e-9 absorbs float formatting, nothing else.
# Identity, numbers and assignment state are compared EXACTLY (no tolerance).
IDENTITY_GEOM_TOL = 1e-9
IDENTITY_EXACT_KEYS = ("status", "id_state", "symbols", "identities", "library_collision", "note")


def identity_record(qres, collide) -> dict:
    res = qres.resolution
    return {"status": qres.status, "id_state": qres.id_state, "symbols": list(res.symbols),
            "identities": [list(x) for x in qres.identities],
            "library_collision": sorted(list(x) for x in set(qres.identities) & collide),
            "note": qres.note, "pins": [(p.tip, p.base, p.numbers) for p in res.pins]}


def _norm_pin(p) -> tuple | None:
    try:
        tip, base, nums = p
        tip = (float(tip[0]), float(tip[1]))
        base = (float(base[0]), float(base[1]))
        if len(tuple(tip)) != 2 or len(tuple(base)) != 2 or not all(map(math.isfinite, tip + base)):
            return None
        return tip, base, tuple(sorted(str(n) for n in nums))
    except (TypeError, ValueError):
        return None


def identity_record_problems(stored: dict, derived: dict) -> list[str]:
    """Stored candidate identity vs the identity re-derived from the page: every field that decides the outcome.
    Exact: status, id_state (assignment state), symbols, identities, collisions, note, the pin COUNT and every
    pin's possible numbers. Within IDENTITY_GEOM_TOL: terminal (tip) and base (direction) positions, matched
    one-to-one after sorting by position. A malformed or missing field is a mismatch (fail closed)."""
    probs = []
    if not isinstance(stored, dict):
        return ["identity record missing"]
    for k in IDENTITY_EXACT_KEYS:
        if k not in stored:
            probs.append(f"identity field {k} missing")
        elif stored[k] != derived[k]:
            probs.append(f"identity re-derivation differs: {k}")
    sp = stored.get("pins")
    if not isinstance(sp, (list, tuple)):
        return probs + ["identity pins missing"]
    s_n = [_norm_pin(p) for p in sp]
    if any(x is None for x in s_n):
        return probs + ["identity pins malformed"]
    d_n = [_norm_pin(p) for p in derived["pins"]]
    if len(s_n) != len(d_n):
        return probs + [f"pin resolution differs: {len(s_n)} stored vs {len(d_n)} derived pins"]
    left = list(d_n)
    for k, (tip, base, nums) in enumerate(sorted(s_n)):
        m = [j for j, d in enumerate(left) if math.dist(d[0], tip) <= IDENTITY_GEOM_TOL]
        if len(m) != 1:
            probs.append(f"pin resolution differs: stored terminal {k} at {tip} not re-derived")
            continue
        d = left.pop(m[0])
        if math.dist(d[1], base) > IDENTITY_GEOM_TOL:
            probs.append(f"pin resolution differs: base/direction of terminal {k}")
        if d[2] != nums:
            probs.append(f"pin resolution differs: numbers of terminal {k} {nums} vs {d[2]}")
    return probs


def pin_evidence_identity_problems(c) -> list[str]:
    """The per-pin evidence records must be exactly the identity's pins (same order, index, numbers, positions)."""
    ip = (c.identity or {}).get("pins") if isinstance(c.identity, dict) else None
    if not isinstance(ip, (list, tuple)) or len(ip) != len(c.pins):
        return ["pin evidence count differs from the identity pins"]
    probs = []
    for k, (p, q) in enumerate(zip(c.pins, ip)):
        nq = _norm_pin(q)
        if nq is None:
            probs.append(f"pin evidence {k}: identity pin malformed")
            continue
        if p.index != k:
            probs.append(f"pin evidence {k}: index {p.index}")
        if tuple(sorted(map(str, p.numbers))) != nq[2]:
            probs.append(f"pin evidence {k}: numbers {p.numbers} differ from the identity {nq[2]}")
        if (math.dist(p.predicted_tip, nq[0]) > IDENTITY_GEOM_TOL
                or math.dist(p.predicted_base, nq[1]) > IDENTITY_GEOM_TOL):
            probs.append(f"pin evidence {k}: predicted terminal differs from the identity")
    return probs


def _identity_of(d: dict) -> tuple[str, str]:
    return (d["lib_token"], d["symbol"])


def library_index(symbols) -> tuple[dict, set]:
    """(lib_token, name) -> SymbolDef, plus the identities that map to more than one DIFFERENT definition."""
    idx: dict = {}
    collide: set = set()
    for s in symbols:
        k = symbol_identity(s)
        if k in idx and idx[k] != s:
            collide.add(k)
        idx.setdefault(k, s)
    return idx, collide


def alternative_problems(c: "RecoveryCandidate") -> list[str]:
    """Every retained (symbol, transform) alternative must predict the SAME terminals (tip and base within the
    template tolerance, one-to-one) with a pin number the identity records for that terminal; with a CONFIRMED
    identity each alternative must give exactly the recorded number. Nothing is taken from hits[0] alone."""
    probs = []
    idn = c.identity
    tau = c.tolerances.get("template_tau")
    rec = idn.get("pins") or []
    if not c.hits:
        return ["no retained template alternative"]
    if tau is None or not (tau > 0):
        return ["template tolerance missing"]
    ids = sorted({_identity_of(h) for h in c.hits})
    if [list(x) for x in ids] != [list(x) for x in idn.get("identities", [])]:
        probs.append("alternatives' identities differ from the recorded identity")
    for k, h in enumerate(c.hits):
        hp = h.get("pins")
        if hp is None or len(hp) != len(rec):
            probs.append(f"alternative {k}: pin count differs")
            continue
        used = set()
        for tip, base, numbers in rec:
            m = [j for j, (_n, t, _b) in enumerate(hp) if math.dist(t, tip) <= tau]
            if len(m) != 1 or m[0] in used:
                probs.append(f"alternative {k}: terminal at {tuple(round(v, 3) for v in tip)} not matched one-to-one")
                continue
            used.add(m[0])
            n, _t, b = hp[m[0]]
            if math.dist(b, base) > tau:
                probs.append(f"alternative {k}: pin {n} direction differs")
            if n not in numbers:
                probs.append(f"alternative {k}: pin number {n} not recorded for its terminal")
            elif idn.get("id_state") == "CONFIRMED" and tuple(numbers) != (n,):
                probs.append(f"alternative {k}: pin number assignment differs")
    return probs


ROLE_PRIORITY = ("template", "stripped", "excluded", "proposal", "discovery")


def role_index(c: "RecoveryCandidate") -> dict[int, str]:
    """prim id -> the most specific lineage role it holds (template > stripped > excluded > proposal >
    discovery). Every id here is forbidden as independent pin-line evidence of this candidate."""
    out: dict[int, str] = {}
    for role, ids in (("discovery", c.discovery_prim_ids), ("proposal", c.source_prim_ids),
                      ("excluded", c.excluded_prim_ids), ("stripped", c.stripped_prim_ids),
                      ("template", c.matched_prim_ids)):
        for i in ids:
            out[i] = role
    return out


def evidence_role_problems(c: "RecoveryCandidate", *, pins: bool = True) -> list[str]:
    """Structural independence of the evidence roles (see module docstring). Fail closed on anything missing."""
    probs = []
    roles = {"discovery": c.discovery_prim_ids, "proposal": c.source_prim_ids, "template": c.matched_prim_ids,
             "stripped": c.stripped_prim_ids, "excluded": c.excluded_prim_ids}
    for r, ids in roles.items():
        if len(set(ids)) != len(ids):
            probs.append(f"duplicate prim ids within {r}")
    if not c.discovery_prim_ids:
        probs.append("missing discovery lineage")
    if not c.matched_prim_ids:
        probs.append("no template-fit prims")
    src = set(c.source_prim_ids)
    if not src <= set(c.discovery_prim_ids):
        probs.append("proposal prims outside the discovery lineage")
    m, st, ex = set(c.matched_prim_ids), set(c.stripped_prim_ids), set(c.excluded_prim_ids)
    if m & st or m & ex or st & ex:
        probs.append("prim holds two of template/stripped/excluded")
    if (m | st | ex) - src:
        probs.append("template/stripped/excluded prims outside the proposal")
    if src - (m | st | ex):
        probs.append(f"source coverage incomplete: {len(src - (m | st | ex))} proposal prim(s) without a role")
    if (st or ex) and "+RELAX" not in c.operation:
        probs.append("stripped/excluded prims without a relaxation")
    if pins:
        forbidden = role_index(c)
        seen: dict[int, int] = {}
        for p in c.pins:
            if p.line_prim is None:
                continue
            if p.line_prim in forbidden:
                probs.append(f"pin {p.index}: pin line {p.line_prim} is {forbidden[p.line_prim]} evidence "
                             "(not independent)")
            if p.line_prim in seen:
                probs.append(f"pin {p.index}: pin line {p.line_prim} also used by pin {seen[p.line_prim]}")
            seen.setdefault(p.line_prim, p.index)
    return probs


def discovery_of(baseline, source_clusters) -> tuple[tuple[int, ...] | None, list[str]]:
    """Re-derive the discovery lineage from the baseline: every source cluster must exist and be NONE."""
    by_cl = {cr.cluster.id: cr for cr in baseline}
    probs, ids = [], set()
    if not source_clusters:
        return None, ["no source clusters"]
    for k in source_clusters:
        cr = by_cl.get(k)
        if cr is None:
            probs.append(f"source cluster {k} not in the baseline")
        elif cr.resolution.status != "NONE":
            probs.append(f"source cluster {k} is not a NONE cluster")
        else:
            ids.update(cr.cluster.prim_ids)
    return (None if probs else tuple(sorted(ids))), probs


def _hit_key(d: dict) -> tuple:
    return (d["symbol"], d["lib_token"], d["orient"], round(d["scale"], 9), round(d["tx"], 6), round(d["ty"], 6))


def _place(d: dict, x: float, y: float):
    a, b, c, e = D4[d["orient"]]
    s = d["scale"]
    return (s * (a * x + b * y) + d["tx"], s * (c * x + e * y) + d["ty"])


# ----------------------------------------------------------------------------------------------- baseline claims

def _baseline_claims(pi, baseline):
    """Everything the baseline already owns: primitives of resolved clusters, S2 stub / pin-line primitives, and
    every resolved pin terminal (tip with its tolerance)."""
    prims, tips = set(), []
    for cr in baseline:
        if cr.resolution.status != "NONE":
            prims.update(cr.cluster.prim_ids)
        for p in cr.pins:
            if p.stub is not None:
                prims.add(p.stub[0])
            plen = math.hypot(p.tip[0] - p.base[0], p.tip[1] - p.base[1])
            tips.append((p.tip, s2_tolerance(plen)))
        for sel in cr.pin_lines:
            for r in sel:
                if r.get("line"):
                    prims.add(r["line"][0])
    return prims, tips


def check_lineage(pi, cls, baseline, eps: float, pin_params: PinParams, exclude: frozenset) -> list[str]:
    """Recompute the baseline body clusters (pure) and prove that the ClusterResults handed to recovery are exactly
    that partition: NONE clusters identical, resolved clusters a subset of their recomputed cluster."""
    from .symbols import body_clusters
    ref = {c.id: c for c in body_clusters(pi, cls, eps, pin_params.cluster_gap_frac, exclude)}
    probs = []
    if len(ref) != len(baseline):
        probs.append(f"baseline has {len(baseline)} clusters, recomputed {len(ref)}")
    for cr in baseline:
        c = ref.get(cr.cluster.id)
        if c is None:
            probs.append(f"cluster {cr.cluster.id} not reproduced")
        elif cr.resolution.status == "NONE" and tuple(sorted(c.prim_ids)) != tuple(sorted(cr.cluster.prim_ids)):
            probs.append(f"NONE cluster {cr.cluster.id} prims differ from recomputation")
        elif not set(cr.cluster.prim_ids) <= set(c.prim_ids):
            probs.append(f"resolved cluster {cr.cluster.id} has prims outside its recomputed cluster")
    return probs


# ----------------------------------------------------------------------------------------------- propose

def _proposals(pi, cls, baseline, params: RecoveryParams, stats: Counter):
    by_id = {q.id: q for q in pi.prims}
    min_part = params.min_part_frac * math.hypot(pi.geom.width, pi.geom.height)
    units = []        # (source cluster ids, prim ids, op)
    split_children: dict[int, list] = {}
    for cr in baseline:
        if cr.resolution.status != "NONE":
            continue
        ids = tuple(sorted(cr.cluster.prim_ids))
        if len(ids) > params.max_split_prims:
            stats["budget_split_skipped"] += 1
            units.append(((cr.cluster.id,), ids, None))
            continue
        comps = touch_components(ids, by_id, params.touch_eps)
        if len(comps) > 1:
            for comp in comps:
                units.append(((cr.cluster.id,), comp, OP_SPLIT))
                split_children.setdefault(cr.cluster.id, []).append(comp)
        else:
            units.append(((cr.cluster.id,), ids, None))
    baseline_sets = {tuple(sorted(cr.cluster.prim_ids)) for cr in baseline}
    props, seen = [], set()

    def add(src, ids, op):
        key = tuple(sorted(ids))
        if key in seen or key in baseline_sets:
            return
        seen.add(key)
        props.append((tuple(sorted(set(src))), key, op))

    boxes = [_bbox_ids(u[1], by_id) for u in units]
    for (src, ids, op), b in zip(units, boxes):
        if op == OP_SPLIT:
            if _maxdim(b) >= min_part:
                add(src, ids, op)
            else:
                stats["split_components_below_min_part"] += 1
    big = [k for k, b in enumerate(boxes) if _maxdim(b) >= min_part]
    order = sorted(big, key=lambda k: boxes[k][0])
    for n, a in enumerate(order):
        ba = boxes[a]
        reach = params.merge_gap_frac * _maxdim(ba)
        near = []
        for b in order[n + 1:]:
            bb = boxes[b]
            if bb[0] > ba[2] + reach:
                break
            g = _gap(ba, bb)
            if g <= params.merge_gap_frac * min(_maxdim(ba), _maxdim(bb)):
                near.append((g, b))
        for _g, b in sorted(near)[:params.max_neighbours]:
            add(units[a][0] + units[b][0], units[a][1] + units[b][1], OP_MERGE)
    if len(props) > params.max_proposals:
        stats["budget_proposals_dropped"] += len(props) - params.max_proposals
        props = props[:params.max_proposals]
    stats["units"] = len(units)
    stats["proposals"] = len(props)
    return props


def propose(pi, cls, baseline, symbols, params: RecoveryParams, eps: float, stats: Counter,
            deadline: float | None = None) -> list:
    """deadline (time.time() value): proposals not matched by then are counted in budget_deadline_dropped, never
    silently lost; candidates already built are complete."""
    by_id = {q.id: q for q in pi.prims}
    syms = list(symbols)
    _lib, collide = library_index(syms)
    out = []
    props = _proposals(pi, cls, baseline, params, stats)
    for k, (src, ids, op) in enumerate(props):
        if deadline is not None and time.time() > deadline:
            stats["budget_deadline_dropped"] += len(props) - k
            break
        cl = _rebuild(-(k + 1), ids, by_id)
        hits, stripped, opname = _hits_sorted(match_cluster(cl, syms)), (), op
        mcl = cl
        if not hits and params.relax:
            for cl2, stubs2 in relaxations(cl, by_id, eps):
                h2 = _hits_sorted(match_cluster(cl2, syms))
                if h2:
                    mcl, hits, stripped, opname = cl2, h2, tuple(sorted(stubs2)), op + "+RELAX"
                    break
        if not hits:
            stats["proposals_no_template"] += 1
            continue
        geom = geometry_of(pi, ids)
        qres = resolve_qualified(mcl, hits)
        disc, _dp = discovery_of(baseline, src)
        matched_ids = tuple(sorted(mcl.prim_ids))
        tau = tolerance(mcl, MatchParams())
        out.append(RecoveryCandidate(
            id=f"RC{k}", operation=opname, source_clusters=src, source_prim_ids=ids,
            matched_prim_ids=matched_ids, stripped_prim_ids=stripped,
            discovery_prim_ids=disc or (),
            excluded_prim_ids=tuple(sorted(set(ids) - set(matched_ids) - set(stripped))),
            geometry=geom, geometry_sha256=geometry_hash(geom), frame=FRAME,
            tolerances={"template_tau": tau, "s2_tau_abs": S2_TAU_ABS_MAX, "s2_rel": S2_REL_MAX,
                        "touch_eps": params.touch_eps, "merge_gap_frac": params.merge_gap_frac,
                        "relax_eps": eps},
            hits=tuple(_hit_dict(h) for h in hits),
            identity=identity_record(qres, collide),
            history=[(None, PROPOSED, f"{opname} matched {len(hits)} template alternative(s)")]))
    return out


# ----------------------------------------------------------------------------------------------- evidence gate

def _measure_pin(tip, base, lines, forbidden: dict, kind_of, tol: float, by_id):
    """All distinct measured 2-point lines whose two ends agree with base and tip within tol, excluding EVERY
    primitive of the candidate's own lineage (`forbidden`: prim id -> role, see role_index). Returns
    (supporting [(pid, (p0, p1) as ingested, orientation, residual)], roles of agreeing lineage lines)."""
    grid, cell = lines
    gx, gy = math.floor(tip[0] / cell), math.floor(tip[1] / cell)
    span = max(1, math.ceil(tol / cell))
    sup, reused, seen = [], set(), set()
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            for _end, _far, pid in grid.get((gx + dx, gy + dy), ()):
                if pid in seen:
                    continue
                seen.add(pid)
                q = by_id[pid]
                p0, p1 = tuple(map(float, q.pts[0])), tuple(map(float, q.pts[-1]))
                o = stub_orientation(p0, p1, base, tip, tol)
                if o is None:
                    continue
                if pid in forbidden:
                    reused.add(forbidden[pid])
                    continue
                if kind_of(pid) == "TEXT":
                    continue
                at_base, at_tip = (p0, p1) if o == "FORWARD" else (p1, p0)
                res = max(math.dist(at_base, base), math.dist(at_tip, tip))
                sup.append((pid, (p0, p1), o, res))
    return sorted(sup), reused


def gate(c: RecoveryCandidate, pi, cls, lines, min_part: float) -> None:
    by_id = {q.id: q for q in pi.prims}
    # 1. lineage completeness
    lin = []
    if not c.source_prim_ids:
        lin.append("no source primitives")
    for i in c.source_prim_ids:
        q = by_id.get(i)
        if q is None:
            lin.append(f"prim {i} missing")
        elif cls.records[i].cls != LC_BODY or q.kind != "polyline":
            lin.append(f"prim {i} is not a BODY polyline")
    if not set(c.matched_prim_ids) <= set(c.source_prim_ids) or not set(c.stripped_prim_ids) <= set(c.source_prim_ids):
        lin.append("matched/stripped prims outside the source group")
    if set(c.matched_prim_ids) & set(c.stripped_prim_ids):
        lin.append("prim both matched and stripped")
    lin += evidence_role_problems(c, pins=False)
    if lin:
        c.to(REJECTED, "lineage: " + "; ".join(lin))
        return
    # 2. degenerate / glyph-sized geometry
    b = _bbox_ids(c.matched_prim_ids, by_id)
    if _maxdim(b) <= 1e-9:
        c.to(REJECTED, "degenerate: zero-size geometry")
        return
    if _maxdim(b) < min_part:
        c.to(REJECTED, f"too_small: {_maxdim(b):.3f} < {min_part:.3f} (text-like strokes)")
        return
    # 3. identity and transform
    idn = c.identity
    if idn["status"] == "DIFFERENT_TIP_SETS":
        c.to(UNKNOWN, "ambiguous_transform: accepted templates predict different pin positions")
        return
    if idn["status"] != "UNIQUE":
        c.to(REJECTED, f"no_template: {idn['status']}")
        return
    if len(idn["symbols"]) != 1:
        c.to(UNKNOWN, f"multiple_symbols: {idn['symbols']}")
        return
    if len(idn.get("identities") or ()) != 1:
        c.to(UNKNOWN, f"multiple_symbol_identities: {idn.get('identities')}")
        return
    if idn.get("library_collision", True):
        c.to(UNKNOWN, f"library_identity_collision: {idn.get('library_collision')}")
        return
    if not idn["pins"]:
        c.to(REJECTED, "no_pins: template has no pins")
        return
    if idn["id_state"] not in ID_OK:
        c.to(UNKNOWN, f"ambiguous_identity: {idn['id_state']}")
        return
    tau = c.tolerances["template_tau"]
    tips = [p[0] for p in idn["pins"]]
    if any(math.dist(tips[i], tips[j]) <= tau for i in range(len(tips)) for j in range(i + 1, len(tips))):
        c.to(UNKNOWN, "stacked_pins: two predicted terminals coincide")
        return
    alt = alternative_problems(c)
    if alt:
        c.to(UNKNOWN, "ambiguous_alternatives: " + "; ".join(alt))
        return
    # 4. independent physical pin-line evidence, every pin
    forbidden = role_index(c)
    kind_of = lambda pid: cls.records[pid].cls  # noqa: E731
    pins, bad, used = [], [], Counter()
    for k, (tip, base, numbers) in enumerate(idn["pins"]):
        plen = math.dist(tip, base)
        if not (math.isfinite(plen) and plen > 0):
            pins.append(PinEvidence(k, tuple(numbers), tuple(tip), tuple(base), 0.0, None, None, None, None,
                                    "zero_length_pin"))
            bad.append("zero_length_pin")
            continue
        tol = s2_tolerance(plen)
        sup, reused = _measure_pin(tip, base, lines, forbidden, kind_of, tol, by_id)
        if not sup:
            why = ("pin_line_reused_from_" + next(r for r in ROLE_PRIORITY if r in reused)) if reused \
                else "missing_pin_line"
        elif len(sup) > 1:
            why = f"overlapping_pin_lines: {[s[0] for s in sup]}"
        else:
            why = None
        line = sup[0] if len(sup) == 1 else None
        pins.append(PinEvidence(k, tuple(numbers), tuple(tip), tuple(base), tol,
                                line[0] if line else None, line[1] if line else None,
                                line[2] if line else None, line[3] if line else None, why))
        if why:
            bad.append(why.split(":", 1)[0])
        else:
            used[line[0]] += 1
    c.pins = tuple(pins)
    if any(n > 1 for n in used.values()):
        c.to(UNKNOWN, "duplicate_pin_line: one line supports two terminals")
        return
    if bad:
        c.to(UNKNOWN, "pin_evidence: " + ",".join(sorted(set(bad))))
        return
    er = evidence_role_problems(c)
    if er:
        c.to(REJECTED, "evidence_roles: " + "; ".join(er))
        return
    c.to(EVIDENCE_SUPPORTED, f"all {len(pins)} pins have a distinct measured pin line")


def resolve_conflicts(cands: list, claimed_prims: set, claimed_tips: list) -> None:
    live = [c for c in cands if c.state != REJECTED]
    # against the baseline: the baseline always wins, the candidate is rejected
    bcell = max([tt for _t, tt in claimed_tips] + [s2_tolerance(0.0)]) * 2
    bgrid: dict[tuple, list] = {}
    for t, tt in claimed_tips:
        bgrid.setdefault((math.floor(t[0] / bcell), math.floor(t[1] / bcell)), []).append((t, tt))

    def near_claimed(p) -> bool:
        gx, gy = math.floor(p.predicted_tip[0] / bcell), math.floor(p.predicted_tip[1] / bcell)
        span = max(1, math.ceil(p.tol / bcell))
        return any(math.dist(p.predicted_tip, t) <= max(p.tol, tt)
                   for dx in range(-span, span + 1) for dy in range(-span, span + 1)
                   for t, tt in bgrid.get((gx + dx, gy + dy), ()))

    for c in live:
        if c.state not in (EVIDENCE_SUPPORTED, PROMOTABLE, UNKNOWN):
            continue
        hit = set(c.source_prim_ids) & claimed_prims
        lp = {p.line_prim for p in c.pins if p.line_prim is not None} & claimed_prims
        tip_hit = any(near_claimed(p) for p in c.pins)
        if hit or lp or tip_hit:
            why = ("baseline_claim: " + ("prims " if hit else "") + ("pin_line " if lp else "")
                   + ("terminal" if tip_hit else "")).strip()
            c.to(REJECTED, why)
    live = [c for c in cands if c.state != REJECTED]
    # among candidates: any shared primitive, pin line or terminal -> all involved become UNKNOWN.
    # Indexed (prim -> candidates, line -> candidates, terminal grid) instead of all pairs; same relation.
    flag: dict[str, set] = {}
    owners: dict[tuple, list[int]] = {}
    for n, c in enumerate(live):
        # one key space: a pin line of one candidate that is a grouped primitive of another is a conflict too
        for i in set(c.source_prim_ids) | {p.line_prim for p in c.pins if p.line_prim is not None}:
            owners.setdefault(("p", i), []).append(n)
    for ns in owners.values():
        for a in set(ns):
            for b in set(ns):
                if a != b:
                    flag.setdefault(live[a].id, set()).add(live[b].id)
    tips = [(n, p.predicted_tip, p.tol) for n, c in enumerate(live) for p in c.pins]
    cell = max([t for _n, _p, t in tips] + [1e-6]) * 2
    grid: dict[tuple, list] = {}
    for n, tp, t in tips:
        grid.setdefault((math.floor(tp[0] / cell), math.floor(tp[1] / cell)), []).append((n, tp, t))
    for n, tp, t in tips:
        gx, gy = math.floor(tp[0] / cell), math.floor(tp[1] / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m, tq, u in grid.get((gx + dx, gy + dy), ()):
                    if m != n and math.dist(tp, tq) <= max(t, u):
                        flag.setdefault(live[n].id, set()).add(live[m].id)
    for c in live:
        if c.id in flag and c.state in (EVIDENCE_SUPPORTED, PROMOTABLE):
            c.to(UNKNOWN, f"candidate_conflict: {sorted(flag[c.id])}")


# ----------------------------------------------------------------------------------------------- audit

def audit_candidate(c: RecoveryCandidate, pi, cls, symbols, baseline) -> list[str]:
    """Re-derive the candidate from the page, the library and the baseline alone; every disagreement is a problem.
    baseline=None cannot re-derive the discovery lineage and therefore fails closed."""
    probs = []
    by_id = {q.id: q for q in pi.prims}
    if any(i not in by_id for i in c.source_prim_ids):
        return ["source prim missing from page"]
    g = geometry_of(pi, c.source_prim_ids)
    if g != c.geometry or geometry_hash(g) != c.geometry_sha256:
        probs.append("geometry/hash mismatch")
    if not c.matched_prim_ids or not set(c.matched_prim_ids) <= set(c.source_prim_ids):
        return probs + ["matched prims not within source"]
    # evidence lineage: discovery from the baseline, roles partition the proposal, pin lines outside all of it
    if baseline is None:
        probs.append("discovery lineage not re-derivable (no baseline)")
    else:
        disc, dp = discovery_of(baseline, c.source_clusters)
        probs += ["discovery: " + x for x in dp]
        if disc is not None and disc != tuple(c.discovery_prim_ids):
            probs.append("discovery lineage differs from the baseline")
    probs += ["evidence roles: " + x for x in evidence_role_problems(c)]
    syms = list(symbols)
    lib, collide = library_index(syms)
    eps = c.tolerances.get("relax_eps")
    if "+RELAX" in c.operation:
        # the stripped / excluded interpretation must follow from the baseline relaxation of the proposal
        src_cl = _rebuild(0, tuple(c.source_prim_ids), by_id)
        if eps is None:
            probs.append("relaxation eps missing")
        elif match_cluster(src_cl, syms):
            probs.append("relaxation not needed: the proposal matches as drawn")
        else:
            got = None
            for cl2, stubs2 in relaxations(src_cl, by_id, eps):
                if match_cluster(cl2, syms):
                    got = (tuple(sorted(cl2.prim_ids)), tuple(sorted(stubs2)))
                    break
            if got != (tuple(c.matched_prim_ids), tuple(c.stripped_prim_ids)):
                probs.append("stripped/template split does not follow from the relaxation")
    elif c.stripped_prim_ids or c.excluded_prim_ids or tuple(c.matched_prim_ids) != tuple(c.source_prim_ids):
        probs.append("prims outside the template fit without a relaxation")
    cl = _rebuild(0, tuple(c.matched_prim_ids), by_id)
    raw = _hits_sorted(match_cluster(cl, syms))
    hits = tuple(_hit_dict(h) for h in raw)
    if [_hit_key(h) for h in hits] != [_hit_key(h) for h in c.hits]:
        probs.append("template re-match differs")
    elif any(len(a["pins"]) != len(b.get("pins") or ()) or any(
            x[0] != y[0] or math.dist(x[1], y[1]) > 1e-6 or math.dist(x[2], y[2]) > 1e-6
            for x, y in zip(a["pins"], b.get("pins") or ())) for a, b in zip(hits, c.hits)):
        probs.append("alternative pins do not follow from symbol + transform")
    qres = resolve_qualified(cl, list(raw))
    probs += identity_record_problems(c.identity, identity_record(qres, collide))
    probs += pin_evidence_identity_problems(c)
    if len(qres.identities) != 1 or len(c.identity.get("identities") or ()) != 1:
        probs.append("symbol identity not unique (library-qualified)")
    if set(qres.identities) & collide:
        probs.append("library identity collision")
    if not c.hits or any(_identity_of(h) not in lib for h in c.hits):
        return probs + ["symbol not in library"]
    probs += alternative_problems(c)
    h0 = c.hits[0]
    s0 = lib[_identity_of(h0)]
    pred = sorted((_place(h0, *p.tip), _place(h0, *p.base)) for p in s0.pins)
    got = sorted((p.predicted_tip, p.predicted_base) for p in c.pins)
    if len(pred) != len(got) or any(math.dist(a[0], b[0]) > 1e-6 or math.dist(a[1], b[1]) > 1e-6
                                    for a, b in zip(pred, got)):
        probs.append("predicted pins do not follow from symbol + transform")
    for p in c.pins:
        if p.line_prim is None or p.line_prim not in by_id:
            probs.append(f"pin {p.index}: no measured line")
            continue
        q = by_id[p.line_prim]
        if q.kind != "polyline" or len(q.pts) != 2 or q.line_closed or cls.records[q.id].cls == "TEXT":
            probs.append(f"pin {p.index}: line prim is not a 2-point open line")
            continue
        if p.line_prim in c.matched_prim_ids:
            probs.append(f"pin {p.index}: line is template evidence (not independent)")
        if p.tol <= 0 or p.tol > s2_tolerance(math.dist(p.predicted_tip, p.predicted_base)) + 1e-12:
            probs.append(f"pin {p.index}: tolerance wider than policy")
        a, b = tuple(map(float, q.pts[0])), tuple(map(float, q.pts[1]))
        if p.line_pts != (a, b):
            probs.append(f"pin {p.index}: recorded line geometry differs from the page")
        o = stub_orientation(a, b, p.predicted_base, p.predicted_tip, p.tol)
        if o is None:
            probs.append(f"pin {p.index}: measured line disagrees with prediction")
        elif o != p.orientation:
            probs.append(f"pin {p.index}: recorded orientation differs")
    return probs


# ----------------------------------------------------------------------------------------------- entry point

def recover(pi: PageIngest, cls: PageClassification, baseline, symbols, *, eps: float = EPS_DEFAULT,
            pin_params: PinParams = PinParams(), exclude: frozenset = frozenset(),
            params: RecoveryParams = RecoveryParams(), guard: bool = True, guard_objs: tuple = (),
            deadline: float | None = None) -> RecoveryReport:
    """Build recovery candidates for one page without touching the baseline.
    guard=True fingerprints (pi, cls, baseline, *guard_objs) before and after and raises if anything changed."""
    t0 = time.time()
    syms = list(symbols)
    before = baseline_fingerprint(pi, cls, baseline, *guard_objs) if guard else None
    stats: Counter = Counter()
    probs = check_lineage(pi, cls, baseline, eps, pin_params, exclude)
    if probs:
        rep = RecoveryReport(STATUS_BLOCKED, [], dict(stats), probs)
    else:
        cands = propose(pi, cls, baseline, syms, params, eps, stats, deadline)
        lines = _line_index(pi, cls)
        min_part = params.min_part_frac * math.hypot(pi.geom.width, pi.geom.height)
        for c in cands:
            gate(c, pi, cls, lines, min_part)
        claimed_prims, claimed_tips = _baseline_claims(pi, baseline)
        resolve_conflicts(cands, claimed_prims, claimed_tips)
        for c in cands:
            if c.state == EVIDENCE_SUPPORTED:
                ap = audit_candidate(c, pi, cls, syms, baseline)
                if ap:
                    c.to(REJECTED, "audit: " + "; ".join(ap))
                else:
                    c.to(PROMOTABLE, "independent re-derivation agrees")
        rep = RecoveryReport(STATUS_OK, cands, dict(stats), [])
    if guard:
        after = baseline_fingerprint(pi, cls, baseline, *guard_objs)
        if after != before:
            raise AssertionError("recovery mutated the baseline")
        rep.stats["baseline_fingerprint"] = before
    rep.stats["seconds"] = round(time.time() - t0, 3)
    return rep


def reaudit(rep: RecoveryReport, pi, cls, symbols, baseline) -> None:
    """Re-run the independent audit over a (possibly stored / transported) report: any PROMOTABLE candidate whose
    lineage or evidence no longer re-derives from the page, the library and the baseline is demoted to REJECTED."""
    for c in rep.candidates:
        if c.state == PROMOTABLE:
            ap = audit_candidate(c, pi, cls, list(symbols), baseline)
            if ap:
                c.to(REJECTED, "audit: " + "; ".join(ap))


def recover_page(pi, cls, baseline, symbols, prm=None, pin_params: PinParams = PinParams(), **kw) -> RecoveryReport:
    """recover() with the same eps / exclusion set as rules.analyze_page."""
    from .rules import RulesParams, find_dots, off_colour_body
    prm = prm or RulesParams()
    dots = find_dots(pi, cls, prm)
    exclude = frozenset(v[2] for v in dots.values()) | off_colour_body(pi, cls)
    return recover(pi, cls, baseline, symbols, eps=prm.eps, pin_params=pin_params, exclude=exclude, **kw)
