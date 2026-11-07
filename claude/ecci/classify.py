"""Line-class and role inference for vector primitives (M2).

Classes: BORDER, WIRE, BODY, OTHER. A class is an hypothesis about *what kind of drawing
element* a primitive is. It says nothing about electrical connectivity.

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

from .evidence import EvidenceItem, EvidenceRegistry, decide_role
from .ir import PageIngest, Prim, SRC_VECTOR

LC_BORDER = "BORDER"
LC_WIRE = "WIRE"
LC_BODY = "BODY"
LC_OTHER = "OTHER"


@dataclass(frozen=True)
class ClassifyParams:
    manhattan_tol_deg: float = 0.5
    border_frac: float = 0.85          # bbox >= this fraction of page width AND height
    body_max_frac: float = 0.35        # BODY bbox diagonal <= this fraction of page diagonal
    min_population: int = 3            # wire style needs this many Manhattan open lines
    min_mode_share: float = 0.5        # ... and the modal style must hold this share
    width_round: int = 3


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


def classify_page(pi: PageIngest, params: ClassifyParams = ClassifyParams()) -> PageClassification:
    W, H = pi.geom.width, pi.geom.height
    page = pi.geom.page_index
    reg = EvidenceRegistry()
    notes: list[str] = []
    pagestat = f"pagestat:{page}"

    pop = [p for p in pi.prims if p.kind == "polyline" and p.stroke and not p.fill
           and not p.line_closed and is_manhattan(p, params.manhattan_tol_deg)
           and p.prov.source == SRC_VECTOR]
    mode: tuple | None = None
    share = 0.0
    if len(pop) >= params.min_population:
        key, cnt = Counter(style_key(p, params.width_round) for p in pop).most_common(1)[0]
        share = cnt / len(pop)
        if share >= params.min_mode_share:
            mode = key
        else:
            notes.append(f"wire style undetermined: modal share {share:.2f} < {params.min_mode_share}")
    else:
        notes.append(f"wire style undetermined: only {len(pop)} Manhattan open lines")

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
    return PageClassification(records, reg, mode, share, notes)
