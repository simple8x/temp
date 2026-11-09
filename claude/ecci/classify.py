"""Line-class and role inference for vector primitives (M2).

Classes: BORDER, WIRE, BODY, TEXT, OTHER. A class is an hypothesis about *what kind of drawing
element* a primitive is. It says nothing about electrical connectivity.

TEXT (M2.1): many producers draw text as stroked paths in the same style as wires. A glyph
component is a connected component (primitives that touch) that is small, touches nothing else
and belongs to a VALID TEXT ROW: >= 3 similar components aligned along a line (at least two of
them with 2-D extent), or exactly 2 with strict equality of height and baseline. A text candidate
that is not in a row stays BODY/WIRE: wrongly removing part of a symbol (the arc inside an
oscillator, the arrows of an LED) is worse than leaving a stray letter, and short isolated
segments stay available for gap recovery.

Evidence rules (ECCI v2.1):
  * Every evidence item declares a lineage = the information inputs it was computed from.
    Granularity (ASSUMPTION, documented): the geometry of a primitive (`geom:<id>`), its
    painting attributes (`style:<id>`) and page-wide statistics (`pagestat:<page>`) are
    distinct inputs. Two items sharing any token, or any ancestor, are NOT independent.
  * A role is CONFIRMED only from >= 2 pairwise-independent items (evidence.decide_role).
  * No evidence here uses connectivity (touching, junctions): that would be circular for
    later wire-to-pin reasoning.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .arrangement import EPS_DEFAULT, build_arrangement, independent_prims_at
from .evidence import EvidenceItem, EvidenceRegistry, decide_role
from .flatten import point_segment_distance
from .ir import PageIngest, Prim, SRC_VECTOR

LC_BORDER = "BORDER"
LC_WIRE = "WIRE"
LC_BODY = "BODY"
LC_TEXT = "TEXT"
LC_OTHER = "OTHER"


@dataclass(frozen=True)
class ClassifyParams:
    manhattan_tol_deg: float = 0.5
    style_min_connected: int = 3       # wire-style choice by structure: minimum connected ends   (ASSUMPTION)
    style_max_mean_len_frac: float = 0.2    # mean line length / page diagonal above which a style is sheet furniture (ASSUMPTION)
    style_body_marks: int = 2           # closed/slanted outlines in a colour that mark it as a body colour (ASSUMPTION)
    style_min_len_share: float = 0.1   # a style needs this share of the longest style's total length (ASSUMPTION)
    style_len_dominance: float = 2.0   # mean-length ratio that decides when structure is ambiguous (ASSUMPTION)
    style_dominance: float = 1.5       # ... and the winner must have this many times the runner-up (ASSUMPTION)
    style_min_mean_len_frac: float = 0.004   # mean line length / page diagonal below which a style is not a wire (ASSUMPTION)
    border_frac: float = 0.85          # bbox >= this fraction of page width AND height
    body_max_frac: float = 0.35        # BODY bbox diagonal <= this fraction of page diagonal
    min_population: int = 3            # wire style needs this many Manhattan open lines
    min_mode_share: float = 0.5        # ... and the modal style must hold this share
    width_round: int = 3
    glyph_max_diag_frac: float = 0.03  # glyph component bbox diagonal <= this x page diagonal
    glyph_min_strokes: int = 4         # prims in a component to be judged by its stroke structure (ASSUMPTION)
    glyph_stroke_share: float = 0.8    # share of those prims that must be tiny 2-point strokes (ASSUMPTION)
    glyph_seg_max_frac: float = 0.003  # tiny = length <= this x page diagonal (ASSUMPTION)
    glyph_min_row: int = 3             # components needed to call a group a text row
    row_gap_frac: float = 2.0          # max horizontal gap between row neighbours, x taller height
    row_height_ratio: float = 4.0      # neighbour heights must be within this factor
    row_band_frac: float = 0.25        # vertical tolerance for alignment, x taller height
    pair_tol: float = 0.15             # a 2-glyph row needs equal height and baseline within this


@dataclass(frozen=True)
class ClassRecord:
    prim_id: int
    cls: str
    role_status: str
    evidence: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass
class PageClassification:
    records: dict[int, ClassRecord]
    registry: EvidenceRegistry
    wire_style: tuple | None
    wire_style_share: float
    notes: list[str] = field(default_factory=list)

    def ids(self, cls: str) -> list[int]:
        return sorted(i for i, r in self.records.items() if r.cls == cls)


def style_key(p: Prim, nd: int = 3) -> tuple:
    return (round(p.stroke_w, nd), p.stroke_color, p.dash)


def is_manhattan(p: Prim, tol_deg: float) -> bool:
    if p.kind != "polyline":
        return False
    s = math.sin(math.radians(tol_deg))
    any_seg = False
    for _, a, b in p.segments():
        any_seg = True
        dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
        L = math.hypot(dx, dy)
        if L == 0:
            continue
        if not (dx <= L * s or dy <= L * s):
            return False
    return any_seg


def _bbox(p: Prim) -> tuple[float, float, float, float]:
    xs = [q[0] for q in p.pts]
    ys = [q[1] for q in p.pts]
    return min(xs), min(ys), max(xs), max(ys)


def _components(pi: PageIngest, eps: float) -> tuple[dict[int, int], list[list[int]]]:
    """Connected components of polyline primitives that touch (geometry only)."""
    polys = [p for p in pi.prims if p.kind == "polyline"]
    if not polys:
        return {}, []
    graph = build_arrangement(polys, eps)
    parent = {p.id: p.id for p in polys}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for node in graph.nodes:
        ids = sorted(independent_prims_at(graph, node.id))
        for other in ids[1:]:
            ra, rb = find(ids[0]), find(other)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for p in polys:
        groups.setdefault(find(p.id), []).append(p.id)
    comps = [sorted(v) for _, v in sorted(groups.items(), key=lambda kv: min(kv[1]))]
    comp_of = {i: ci for ci, ids in enumerate(comps) for i in ids}
    return comp_of, comps


def _text_rows(boxes: dict[int, tuple[float, float, float, float]],
               params: ClassifyParams) -> tuple[dict[int, int], dict[int, int]]:
    """Group candidate components into rows. Returns (comp -> row size, comp -> row id)."""
    ids = sorted(boxes, key=lambda c: boxes[c][0])
    parent = {c: c for c in ids}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for ai, a in enumerate(ids):
        ax0, ay0, ax1, ay1 = boxes[a]
        for b in ids[ai + 1:]:
            bx0, by0, bx1, by1 = boxes[b]
            hb = by1 - by0
            h = max(ay1 - ay0, hb)
            if h <= 0:
                continue
            if bx0 - ax1 > params.row_gap_frac * h:
                break
            lo, hi = min(ay1 - ay0, hb), max(ay1 - ay0, hb)
            if hi > 0 and lo > 0 and hi / lo > params.row_height_ratio:
                continue
            band = params.row_band_frac * h
            # neighbours must share a baseline or a cap line (bottom or top edge aligned);
            # mere vertical overlap would chain unrelated graphics (LED arrows) into text rows
            aligned = abs(ay1 - by1) <= band or abs(ay0 - by0) <= band
            if aligned and bx0 - ax1 <= params.row_gap_frac * h:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    members: dict[int, list[int]] = {}
    for c in ids:
        members.setdefault(find(c), []).append(c)
    size: dict[int, int] = {}
    rid: dict[int, int] = {}
    for root, ms in members.items():
        if _row_valid(ms, boxes, params):
            for c in ms:
                size[c] = len(ms)
                rid[c] = root
    return size, rid


def _row_valid(ms: list[int], boxes, params: ClassifyParams) -> bool:
    two_d = [c for c in ms if (boxes[c][2] - boxes[c][0]) > EPS_DEFAULT and (boxes[c][3] - boxes[c][1]) > EPS_DEFAULT]
    if len(ms) >= params.glyph_min_row:
        return len(two_d) >= 2
    if len(ms) == 2 and len(two_d) == 2:
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = boxes[ms[0]], boxes[ms[1]]
        ha, hb = ay1 - ay0, by1 - by0
        h = max(ha, hb)
        return (abs(ha - hb) <= params.pair_tol * h and abs(ay1 - by1) <= params.pair_tol * h
                and abs(ay0 - by0) <= params.pair_tol * h)
    return False


def _connected_ends(group: list[Prim], eps: float) -> int:
    """Number of endpoints of `group` primitives that touch ANOTHER primitive of the same group
    (end-to-end, corner or T). Pin lines of symbols touch symbols, not each other, while wires of
    one net touch each other: this separates the two when they share width and colour."""
    cell = 8.0
    grid: dict[tuple[int, int], list[int]] = {}
    segs: list[tuple[int, tuple, tuple]] = []
    for pi_, p in enumerate(group):
        for _, a, b in p.segments():
            k = len(segs)
            segs.append((pi_, a, b))
            for gx in range(int(math.floor((min(a[0], b[0]) - eps) / cell)), int(math.floor((max(a[0], b[0]) + eps) / cell)) + 1):
                for gy in range(int(math.floor((min(a[1], b[1]) - eps) / cell)), int(math.floor((max(a[1], b[1]) + eps) / cell)) + 1):
                    grid.setdefault((gx, gy), []).append(k)
    n = 0
    for pi_, p in enumerate(group):
        for e in (p.pts[0], p.pts[-1]):
            hit = False
            for k in grid.get((int(math.floor(e[0] / cell)), int(math.floor(e[1] / cell))), ()):
                oi, a, b = segs[k]
                if oi != pi_ and point_segment_distance(e, a, b) <= eps:
                    hit = True
                    break
            n += hit
    return n


AMBIGUOUS_STYLE = ("AMBIGUOUS",)   # no line of the page may be called a wire (never guess)


def _choose_wire_style(pop: list[Prim], params: "ClassifyParams", notes: list[str], diag_page: float,
                       body_colours: dict | None = None):
    if len(pop) < params.min_population:
        notes.append(f"wire style undetermined: only {len(pop)} Manhattan open lines")
        return None, 0.0
    groups: dict[tuple, list[Prim]] = {}
    for p in pop:
        groups.setdefault(style_key(p, params.width_round), []).append(p)
    big = {k: v for k, v in groups.items() if len(v) >= params.min_population}
    if len(big) >= 2:
        # a style whose lines are only a few line-widths long (no-connect crosses, glyph strokes,
        # tick marks) cannot be the wire style: drop it from the structural comparison
        min_len = params.style_min_mean_len_frac * diag_page
        big = {k: v for k, v in big.items()
               if sum(math.dist(p.pts[0], p.pts[-1]) for p in v) / len(v) >= min_len} or big
        # ... nor a colour that also draws closed outlines (symbol bodies; slanted open lines such as bus entries do not count)
        if body_colours:
            clean = {k: v for k, v in big.items() if body_colours.get(k[1], 0) < params.style_body_marks}
            if clean and len(clean) < len(big):
                notes.append(f"wire style: {len(big) - len(clean)} style(s) share a colour with closed outlines")
                big = clean
                if len(big) == 1:
                    k = next(iter(big))
                    return k, len(groups[k]) / len(pop)
        # ... nor lines spanning a large part of the page (drawing-sheet / title-block rules)
        max_len = params.style_max_mean_len_frac * diag_page
        big = {k: v for k, v in big.items()
               if sum(math.dist(p.pts[0], p.pts[-1]) for p in v) / len(v) <= max_len} or big
        # ... nor one that carries a negligible share of the longest style's total length
        tot = {k: sum(math.dist(p.pts[0], p.pts[-1]) for p in v) for k, v in big.items()}
        top = max(tot.values())
        big = {k: v for k, v in big.items() if tot[k] >= params.style_min_len_share * top}
        if len(big) == 1:
            k = next(iter(big))
            notes.append("wire style chosen: the only style with wire-like length after discarding tiny styles")
            return k, len(groups[k]) / len(pop)
    if len(big) >= 2:
        # several populated styles (e.g. wires, pin lines, bus lines in different colours):
        # choose by structure, not by count, and only if one style clearly dominates
        conn = {k: _connected_ends(v, EPS_DEFAULT) for k, v in big.items()}
        ratio = {k: conn[k] / (2.0 * len(big[k])) for k in big}
        ranked = sorted(big, key=lambda k: (-ratio[k], str(k)))
        best_k = ranked[0]
        second = max((ratio[k] for k in ranked[1:]), default=0.0)
        if conn[best_k] >= params.style_min_connected and ratio[best_k] >= params.style_dominance * max(second, 0.02):
            notes.append(f"wire style chosen by structure: {ratio[best_k]:.2f} of ends touch same-style lines, "
                         f"runner-up {second:.2f}")
            return best_k, len(groups[best_k]) / len(pop)
        if conn[best_k] > 0:
            # tie-break (ASSUMPTION, kept explicit): wires are much longer on average than pin lines / glyph strokes
            mean_len = {k: sum(math.dist(p.pts[0], p.pts[-1]) for p in v) / len(v) for k, v in big.items()}
            cands = [k for k in ranked if ratio[k] >= 0.5 * ratio[best_k] and conn[k] >= params.style_min_connected]
            cands.sort(key=lambda k: (-mean_len[k], str(k)))
            if len(cands) >= 2 and mean_len[cands[0]] >= params.style_len_dominance * mean_len[cands[1]]:
                notes.append(f"wire style chosen by length tie-break: mean length {mean_len[cands[0]]:.1f} vs "
                             f"{mean_len[cands[1]]:.1f}")
                return cands[0], len(groups[cands[0]]) / len(pop)
            notes.append(f"wire style undetermined: structure ambiguous ({ratio[best_k]:.2f} vs {second:.2f})")
            return AMBIGUOUS_STYLE, len(groups[best_k]) / len(pop)
    key, cnt = Counter(style_key(p, params.width_round) for p in pop).most_common(1)[0]
    share = cnt / len(pop)
    if share >= params.min_mode_share:
        return key, share
    notes.append(f"wire style undetermined: modal share {share:.2f} < {params.min_mode_share}")
    return None, share


def classify_page(pi: PageIngest, params: ClassifyParams = ClassifyParams()) -> PageClassification:
    W, H = pi.geom.width, pi.geom.height
    page = pi.geom.page_index
    reg = EvidenceRegistry()
    notes: list[str] = []
    pagestat = f"pagestat:{page}"

    # ---- M2.1: glyph (text-as-strokes) components, decided before the wire population
    by_id = {p.id: p for p in pi.prims}
    comp_of, comps = _components(pi, EPS_DEFAULT)
    diag_page = math.hypot(W, H)
    cand_box: dict[int, tuple[float, float, float, float]] = {}
    for ci, ids in enumerate(comps):
        ps = [by_id[i] for i in ids]
        xs = [q[0] for p in ps for q in p.pts]
        ys = [q[1] for p in ps for q in p.pts]
        box = (min(xs), min(ys), max(xs), max(ys))
        if math.hypot(box[2] - box[0], box[3] - box[1]) > params.glyph_max_diag_frac * diag_page:
            continue
        cand_box[ci] = box
    row_size, row_id = _text_rows(cand_box, params) if cand_box else ({}, {})
    if cand_box:
        # vertical text (rotated 90 deg, e.g. pin names on left/right pins): same test on transposed boxes
        tb = {c: (b[1], b[0], b[3], b[2]) for c, b in cand_box.items()
              if c not in row_size and b[2] - b[0] > EPS_DEFAULT and b[3] - b[1] > EPS_DEFAULT}
        vsize, vid = _text_rows(tb, params) if tb else ({}, {})
        for c, n in vsize.items():
            row_size[c] = n
            row_id[c] = -1 - vid[c]
    # second, row-independent kind of text: a small component built from many tiny 2-point strokes
    # (a lone glyph or a short label such as "R2" never forms a row of >= glyph_min_row components)
    seg_max = params.glyph_seg_max_frac * diag_page
    stroke_comps: set[int] = set()
    for ci in cand_box:
        ps = [by_id[i] for i in comps[ci]]
        short = [q for q in ps if len(q.pts) == 2 and not q.line_closed
                 and math.dist(q.pts[0], q.pts[-1]) <= seg_max]
        if len(ps) >= params.glyph_min_strokes and len(short) >= params.glyph_stroke_share * len(ps):
            stroke_comps.add(ci)
    text_comps = set(row_size) | stroke_comps
    text_ids = {i for ci in text_comps for i in comps[ci]}
    if text_comps:
        nrows = len({row_id[c] for c in text_comps if c in row_id})
        notes.append(f"text: {len(text_comps)} glyph components in {nrows} rows, "
                     f"{len(text_ids)} primitives excluded from wire/body classes")

    pop = [p for p in pi.prims if p.kind == "polyline" and p.stroke and not p.fill
           and not p.line_closed and is_manhattan(p, params.manhattan_tol_deg)
           and p.prov.source == SRC_VECTOR and p.id not in text_ids]
    body_colours: dict = {}
    for p in pi.prims:
        if (p.kind == "polyline" and p.stroke and not p.fill and p.id not in text_ids
                and p.prov.source == SRC_VECTOR and p.line_closed):
            c = style_key(p, params.width_round)[1]
            body_colours[c] = body_colours.get(c, 0) + 1
    if body_colours:
        notes.append("body-colour marks (closed outlines): " + ", ".join(
            f"{c[1:]}={n}" if isinstance(c, tuple) else f"{c}={n}" for c, n in sorted(body_colours.items(), key=str)))
    mode, share = _choose_wire_style(pop, params, notes, diag_page, body_colours)
    no_wire = mode == AMBIGUOUS_STYLE
    if no_wire:
        mode = ('__none__',)    # matches no style: every line stays BODY/OTHER, nothing becomes a wire

    records: dict[int, ClassRecord] = {}
    diag = math.hypot(W, H)
    for p in pi.prims:
        pid = p.id
        ev: list[str] = []
        reasons: list[str] = []

        def add(kind: str, lineage: set[str], note: str) -> None:
            eid = f"{kind}:{pid}"
            reg.add(EvidenceItem(eid, kind, SRC_VECTOR, frozenset(lineage)))
            ev.append(eid)
            reasons.append(note)

        if p.kind == "point":
            records[pid] = ClassRecord(pid, LC_OTHER, "UNKNOWN", (), ("point primitive",))
            continue
        if pid in text_ids:
            ci = comp_of[pid]
            add("GLYPH_SHAPE", {f"size:{page}:{ci}", f"contact:{page}:{ci}"},
                "small connected component that touches no other primitive")
            if ci in row_size:
                add("GLYPH_ROW", {f"row:{page}:{row_id[ci]}"},
                    f"member of a text row of {row_size[ci]} similar aligned components")
            if ci in stroke_comps:
                add("GLYPH_STROKES", {f"strokes:{page}:{ci}"},
                    "component made of many tiny 2-point strokes")
            dec = decide_role(ev, reg)
            records[pid] = ClassRecord(pid, LC_TEXT, dec.status, tuple(ev), tuple(reasons))
            continue
        x0, y0, x1, y1 = _bbox(p)
        bw, bh = x1 - x0, y1 - y0
        sk = style_key(p, params.width_round)
        differs = mode is not None and sk != mode
        manh = is_manhattan(p, params.manhattan_tol_deg)

        if p.line_closed and bw >= params.border_frac * W and bh >= params.border_frac * H:
            cls = LC_BORDER
            add("FRAME_GEOM", {f"geom:{pid}"}, "closed outline spanning >= border_frac of the page")
            if differs:
                add("FRAME_STYLE", {f"style:{pid}", pagestat}, "style differs from wire style")
        elif (p.stroke and not p.fill and not p.line_closed and manh and
              (mode is None or sk == mode)):
            cls = LC_WIRE
            add("WIRE_SHAPE", {f"geom:{pid}"}, "open, stroke-only, axis-aligned polyline")
            if mode is not None:
                add("WIRE_STYLE", {f"style:{pid}", pagestat}, "style equals the modal wire style")
        elif math.hypot(bw, bh) <= params.body_max_frac * diag:
            cls = LC_BODY
            why = []
            if p.line_closed:
                why.append("closed")
            if not manh:
                why.append("not axis-aligned")
            if differs:
                why.append("style differs from wire style")
            add("BODY_SHAPE", {f"geom:{pid}"}, "small and " + (", ".join(why) or "not a wire"))
            if differs:
                add("BODY_STYLE", {f"style:{pid}", pagestat}, "style differs from wire style")
        else:
            cls = LC_OTHER
            reasons.append("large non-frame element")
        dec = decide_role(ev, reg)
        records[pid] = ClassRecord(pid, cls, dec.status, tuple(ev), tuple(reasons))
    return PageClassification(records, reg, (None if no_wire else mode), share, notes)
