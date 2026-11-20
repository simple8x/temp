"""ECCI intermediate representation, schema `ecci-ir/1`.

Pure data (no PDF library dependency). M1 contains geometry and provenance only:
there is intentionally NO field here that states an electrical connection.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterator

SCHEMA_VERSION = "ecci-ir/1"

SRC_VECTOR = "VECTOR"
SRC_RASTER = "RASTER"
SRC_TEXT_LAYER = "TEXT_LAYER"
SRC_OCR = "OCR"
SOURCES = frozenset({SRC_VECTOR, SRC_RASTER, SRC_TEXT_LAYER, SRC_OCR})

Point = tuple[float, float]
BBox = tuple[float, float, float, float]


class IRValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Prov:
    """Where a primitive came from, precisely enough to find it again in the PDF.

    chain: ((stream_label, op_index), ...) from the outermost content stream inwards.
      stream_label is "page" for the page content, otherwise the XObject resource name.
      op_index indexes the parsed instruction list of that stream. All but the last entry
      point at the `Do` operator that invoked the next form XObject; the last entry is the
      painting operator that produced the primitive.
    first_op: index (in the innermost stream) of the `m`/`re` that started the subpath.
    lineage: tokens naming the information inputs behind this primitive (see evidence.py).
    """

    page: int
    source: str
    chain: tuple[tuple[str, int], ...]
    first_op: int
    bbox: BBox
    lineage: tuple[str, ...] = ()

    @property
    def op_index(self) -> int:
        return self.chain[-1][1]

    @property
    def content_ref(self) -> str:
        return f"p{self.page}" + "".join(f"/{label}#{idx}" for label, idx in self.chain)

    def to_dict(self) -> dict:
        return {
            "page": self.page, "source": self.source,
            "chain": [[a, b] for a, b in self.chain], "first_op": self.first_op,
            "bbox": list(self.bbox), "lineage": list(self.lineage),
            "content_ref": self.content_ref,
        }

    @staticmethod
    def from_dict(d: dict) -> "Prov":
        return Prov(
            page=int(d["page"]), source=str(d["source"]),
            chain=tuple((str(a), int(b)) for a, b in d["chain"]),
            first_op=int(d["first_op"]), bbox=tuple(float(x) for x in d["bbox"]),  # type: ignore[arg-type]
            lineage=tuple(d.get("lineage", ())),
        )


@dataclass
class Prim:
    id: int
    kind: str                       # "polyline" | "point"
    pts: list[Point]                # normalized page space
    closed: bool                    # explicit `h` or `re`
    stroke: bool
    fill: bool
    stroke_w: float                 # normalized units; 0.0 means hairline
    stroke_color: tuple | None
    fill_color: tuple | None
    dash: tuple[tuple[float, ...], float] | None
    origin: str                     # "path" | "re"
    flatten_tol: float
    flatten_ok: bool                # False => tolerance NOT guaranteed (depth limit hit)
    anisotropic: bool               # stroke width is approximate under this CTM
    prov: Prov

    @property
    def line_closed(self) -> bool:
        """Closed as *line geometry*: explicit close, or a fill-only outline."""
        return self.closed or (self.fill and not self.stroke)

    def segments(self) -> Iterator[tuple[int, Point, Point]]:
        """Yield (seg_idx, p, q); seg_idx i joins vertex i to vertex (i+1) % n."""
        if self.kind != "polyline":
            return
        n = len(self.pts)
        for i in range(n - 1):
            yield i, self.pts[i], self.pts[i + 1]
        if self.line_closed and n >= 3:
            yield n - 1, self.pts[n - 1], self.pts[0]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "pts": [list(p) for p in self.pts],
            "closed": self.closed, "stroke": self.stroke, "fill": self.fill,
            "stroke_w": self.stroke_w,
            "stroke_color": list(self.stroke_color) if self.stroke_color else None,
            "fill_color": list(self.fill_color) if self.fill_color else None,
            "dash": [list(self.dash[0]), self.dash[1]] if self.dash else None,
            "origin": self.origin, "flatten_tol": self.flatten_tol,
            "flatten_ok": self.flatten_ok, "anisotropic": self.anisotropic,
            "prov": self.prov.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "Prim":
        dash = d.get("dash")
        return Prim(
            id=int(d["id"]), kind=str(d["kind"]), pts=[(float(x), float(y)) for x, y in d["pts"]],
            closed=bool(d["closed"]), stroke=bool(d["stroke"]), fill=bool(d["fill"]),
            stroke_w=float(d["stroke_w"]),
            stroke_color=tuple(d["stroke_color"]) if d.get("stroke_color") else None,
            fill_color=tuple(d["fill_color"]) if d.get("fill_color") else None,
            dash=(tuple(dash[0]), float(dash[1])) if dash else None,
            origin=str(d["origin"]), flatten_tol=float(d["flatten_tol"]),
            flatten_ok=bool(d["flatten_ok"]), anisotropic=bool(d["anisotropic"]),
            prov=Prov.from_dict(d["prov"]),
        )


def validate_prim(p: Prim) -> None:
    """Structural validation. Raises IRValidationError; never repairs silently."""
    if p.kind not in ("polyline", "point"):
        raise IRValidationError(f"prim {p.id}: bad kind {p.kind!r}")
    if p.prov.source not in SOURCES:
        raise IRValidationError(f"prim {p.id}: bad source {p.prov.source!r}")
    if not p.prov.chain:
        raise IRValidationError(f"prim {p.id}: empty provenance chain")
    if not p.pts:
        raise IRValidationError(f"prim {p.id}: no points")
    if p.kind == "polyline" and len(p.pts) < 2:
        raise IRValidationError(f"prim {p.id}: polyline needs >= 2 points")
    if p.kind == "point" and len(p.pts) != 1:
        raise IRValidationError(f"prim {p.id}: point needs exactly 1 point")
    for x, y in p.pts:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise IRValidationError(f"prim {p.id}: non-finite coordinate")
    if not (p.stroke or p.fill):
        raise IRValidationError(f"prim {p.id}: unpainted primitive must not be emitted")
    if not (math.isfinite(p.stroke_w) and p.stroke_w >= 0):
        raise IRValidationError(f"prim {p.id}: bad stroke_w")


# ---------------------------------------------------------------- ingest containers

@dataclass
class IngestReport:
    """Everything that was NOT extracted or went wrong. Silence is never success."""

    counts: dict[str, int] = field(default_factory=dict)
    unsupported: dict[str, int] = field(default_factory=dict)
    unknown_ops: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def unsup(self, key: str, n: int = 1) -> None:
        self.unsupported[key] = self.unsupported.get(key, 0) + n

    def error(self, code: str, message: str, where: str = "") -> None:
        self.errors.append({"code": code, "message": message, "where": where})

    def to_dict(self) -> dict:
        return {
            "counts": dict(self.counts), "unsupported": dict(self.unsupported),
            "unknown_ops": dict(self.unknown_ops), "errors": list(self.errors),
        }


@dataclass
class PageIngest:
    geom: Any                       # transform.PageGeom
    prims: list[Prim]
    report: IngestReport

    def to_dict(self) -> dict:
        return {"geom": self.geom.to_dict(), "prims": [p.to_dict() for p in self.prims],
                "report": self.report.to_dict()}


@dataclass
class IngestResult:
    pages: list[PageIngest]
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {"schema": self.schema, "pages": [p.to_dict() for p in self.pages]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# ------------------------------------------------------------- arrangement containers

@dataclass(frozen=True)
class SegRef:
    """A piece [t0, t1] (parameter along the source segment) covered by an edge."""

    prim_id: int
    seg_idx: int
    t0: float
    t1: float


@dataclass
class Node:
    id: int
    xy: Point
    # Raw geometric facts only (FACT). None of these is an electrical statement.
    vertices: tuple[tuple[int, int], ...]   # (prim_id, vertex_idx) whose vertex lies here
    through: tuple[tuple[int, int], ...]    # (prim_id, seg_idx) passing through, not ending here
    edges: tuple[int, ...] = ()


@dataclass
class Edge:
    id: int
    n0: int
    n1: int
    refs: tuple[SegRef, ...]


@dataclass
class ArrangementGraph:
    nodes: list[Node]
    edges: list[Edge]
    points: list[tuple[int, Point]]         # isolated point primitives (e.g. dots)
    eps: float
    stats: dict[str, float]
    # prim_id -> (number of vertices, line_closed): needed to recognise wrap-around continuity
    prim_info: dict[int, tuple[int, bool]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION, "eps": self.eps, "stats": dict(self.stats),
            "prim_info": {str(k): [v[0], v[1]] for k, v in sorted(self.prim_info.items())},
            "nodes": [{"id": n.id, "xy": list(n.xy), "vertices": [list(v) for v in n.vertices],
                       "through": [list(v) for v in n.through], "edges": list(n.edges)}
                      for n in self.nodes],
            "edges": [{"id": e.id, "n0": e.n0, "n1": e.n1,
                       "refs": [[r.prim_id, r.seg_idx, r.t0, r.t1] for r in e.refs]}
                      for e in self.edges],
            "points": [[pid, list(xy)] for pid, xy in self.points],
        }
