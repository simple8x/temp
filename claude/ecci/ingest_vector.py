"""Vector ingestion: a small PDF content-stream interpreter producing primitives.

What it does (FACT, covered by tests):
  * tracks the CTM through q/Q/cm and form XObjects (/Matrix), applies the page base matrix
    (MediaBox/CropBox/Rotate/UserUnit) so every coordinate is in normalized page space;
  * transforms path points at construction time (as the PDF spec requires);
  * flattens cubic Béziers with a bounded error (flatten.py);
  * records provenance: the painting operator index (and the chain of `Do` operators for
    forms) plus the index of the operator that started the subpath.

What it does NOT do is listed in the IngestReport on every page rather than ignored:
text, images, shadings, patterns, clipping, optional content and soft masks are not
interpreted. Primitives hidden by clipping or optional-content layers are still emitted.
"""

from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass
from typing import Any

import pikepdf

from .flatten import flatten_cubic
from .ir import (
    IngestReport, IngestResult, PageIngest, Prim, Prov, SRC_VECTOR, validate_prim,
)
from .transform import (
    IDENTITY, Matrix, PageGeom, _inherited, apply, mat_mul, page_geometry,
    stroke_width_scale,
)

PAINT_OPS = frozenset({"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"})
CONSTRUCT_OPS = frozenset({"m", "l", "c", "v", "y", "h", "re"})
CLIP_OPS = frozenset({"W", "W*"})
_IGNORABLE_OPS = frozenset({
    "BT", "ET", "Tc", "Tw", "Tz", "TL", "Tf", "Tr", "Ts", "Td", "TD", "Tm", "T*",
    "d0", "d1", "ri", "i", "M", "j", "J", "BMC", "EMC", "MP", "DP", "BX", "EX",
})
_TEXT_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})

MAX_FORM_DEPTH = 16
MAX_GSTATE_DEPTH = 256


class IngestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class _BadOperand(Exception):
    pass


def _num(x: Any) -> float:
    try:
        v = float(x)
    except Exception as e:  # noqa: BLE001
        raise _BadOperand(f"non-numeric operand {x!r}") from e
    if not math.isfinite(v):
        raise _BadOperand("non-finite operand")
    return v


def _nums(operands: list, n: int) -> list[float]:
    if len(operands) != n:
        raise _BadOperand(f"expected {n} operands, got {len(operands)}")
    return [_num(o) for o in operands]


@dataclass
class _GState:
    ctm: Matrix
    line_w: float = 1.0
    dash: tuple[tuple[float, ...], float] | None = None
    stroke_color: tuple | None = None
    fill_color: tuple | None = None
    stroke_cs: str | None = None
    fill_cs: str | None = None

    def copy(self) -> "_GState":
        return _GState(self.ctm, self.line_w, self.dash, self.stroke_color, self.fill_color,
                       self.stroke_cs, self.fill_cs)


class _Sub:
    __slots__ = ("pts", "closed", "first_op", "origin", "flat_ok")

    def __init__(self, first_op: int, origin: str = "path") -> None:
        self.pts: list[tuple[float, float]] = []
        self.closed = False
        self.first_op = first_op
        self.origin = origin
        self.flat_ok = True


def lookup_xobject(resources, name: str):
    """Resolve an XObject by operand name (e.g. '/Fm1'). Returns None if absent."""
    try:
        xo = resources.get("/XObject") if resources is not None else None
        if xo is None:
            return None
        return xo.get(name)
    except Exception:  # noqa: BLE001
        return None


def form_resources(xobj, parent_resources):
    try:
        r = xobj.get("/Resources")
    except Exception:  # noqa: BLE001
        r = None
    return r if r is not None else parent_resources


def form_matrix(xobj) -> Matrix:
    try:
        m = xobj.get("/Matrix")
        if m is None:
            return IDENTITY
        vals = [_num(v) for v in m]
        if len(vals) != 6:
            raise _BadOperand("bad /Matrix length")
        return (vals[0], vals[1], vals[2], vals[3], vals[4], vals[5])
    except _BadOperand:
        return IDENTITY


class _PageInterp:
    def __init__(self, geom: PageGeom, flatten_tol: float, max_prims: int) -> None:
        self.geom = geom
        self.tol = flatten_tol
        self.max_prims = max_prims
        self.prims: list[Prim] = []
        self.report = IngestReport()
        self._active_forms: set = set()
        self._prim_limit_hit = False

    # ------------------------------------------------------------------ stream walker
    def run_stream(self, stream, label: str, resources, gs: _GState, prefix: tuple, depth: int) -> None:
        try:
            instrs = pikepdf.parse_content_stream(stream)
        except Exception as e:  # noqa: BLE001
            self.report.error("CONTENT_STREAM_PARSE", f"{type(e).__name__}: {e}", f"{label}")
            return

        stack: list[_GState] = []
        subs: list[_Sub] = []
        cur: _Sub | None = None
        cur_xy: tuple[float, float] | None = None
        start_xy: tuple[float, float] | None = None
        pending_clip = False
        gs = gs.copy()

        def tr(x: float, y: float) -> tuple[float, float]:
            return apply(gs.ctm, x, y)

        def begin(first_op: int, xy: tuple[float, float], origin: str = "path") -> _Sub:
            nonlocal cur, cur_xy, start_xy
            if cur is not None:
                subs.append(cur)
            cur = _Sub(first_op, origin)
            cur.pts.append(xy)
            cur_xy = xy
            start_xy = xy
            return cur

        for idx, inst in enumerate(instrs):
            op = str(inst.operator)
            if op == "INLINE IMAGE":
                self.report.unsup("inline_image")
                continue
            operands = list(inst.operands)
            try:
                # ---------------- graphics state
                if op == "q":
                    if len(stack) >= MAX_GSTATE_DEPTH:
                        self.report.error("Q_OVERFLOW", "graphics-state stack too deep", f"{label}#{idx}")
                    else:
                        stack.append(gs.copy())
                elif op == "Q":
                    if not stack:
                        self.report.error("Q_UNDERFLOW", "Q without matching q (ignored)", f"{label}#{idx}")
                    else:
                        gs = stack.pop()
                elif op == "cm":
                    a, b, c, d, e, f = _nums(operands, 6)
                    gs.ctm = mat_mul((a, b, c, d, e, f), gs.ctm)
                elif op == "w":
                    (gs.line_w,) = _nums(operands, 1)
                elif op == "d":
                    if len(operands) != 2:
                        raise _BadOperand("d expects 2 operands")
                    arr = tuple(_num(v) for v in operands[0])
                    gs.dash = (arr, _num(operands[1])) if arr else None
                elif op == "gs":
                    self._apply_extgstate(gs, resources, str(operands[0]) if operands else "")
                elif op in ("G", "g"):
                    (v,) = _nums(operands, 1)
                    if op == "G":
                        gs.stroke_color = ("gray", v)
                    else:
                        gs.fill_color = ("gray", v)
                elif op in ("RG", "rg"):
                    r, g_, b_ = _nums(operands, 3)
                    if op == "RG":
                        gs.stroke_color = ("rgb", r, g_, b_)
                    else:
                        gs.fill_color = ("rgb", r, g_, b_)
                elif op in ("K", "k"):
                    c_, m_, y_, k_ = _nums(operands, 4)
                    if op == "K":
                        gs.stroke_color = ("cmyk", c_, m_, y_, k_)
                    else:
                        gs.fill_color = ("cmyk", c_, m_, y_, k_)
                elif op in ("CS", "cs"):
                    name = str(operands[0]) if operands else ""
                    if op == "CS":
                        gs.stroke_cs = name
                    else:
                        gs.fill_cs = name
                elif op in ("SC", "SCN", "sc", "scn"):
                    if operands and isinstance(operands[-1], pikepdf.Name):
                        self.report.unsup("pattern_color")
                        vals: tuple = ("pattern", str(operands[-1]))
                    else:
                        vals = ("cs:" + str((gs.stroke_cs if op in ("SC", "SCN") else gs.fill_cs) or "?"),
                                *[_num(o) for o in operands])
                    if op in ("SC", "SCN"):
                        gs.stroke_color = vals
                    else:
                        gs.fill_color = vals
                # ---------------- path construction
                elif op == "m":
                    x, y = _nums(operands, 2)
                    begin(idx, tr(x, y))
                elif op == "l":
                    x, y = _nums(operands, 2)
                    if cur is None:
                        if cur_xy is None:
                            raise _BadOperand("l without current point")
                        begin(idx, cur_xy)
                    assert cur is not None
                    p = tr(x, y)
                    cur.pts.append(p)
                    cur_xy = p
                elif op in ("c", "v", "y"):
                    if cur is None:
                        if cur_xy is None:
                            raise _BadOperand(f"{op} without current point")
                        begin(idx, cur_xy)
                    assert cur is not None and cur_xy is not None
                    if op == "c":
                        x1, y1, x2, y2, x3, y3 = _nums(operands, 6)
                        c1, c2, p3 = tr(x1, y1), tr(x2, y2), tr(x3, y3)
                    elif op == "v":
                        x2, y2, x3, y3 = _nums(operands, 4)
                        c1, c2, p3 = cur_xy, tr(x2, y2), tr(x3, y3)
                    else:
                        x1, y1, x3, y3 = _nums(operands, 4)
                        p3 = tr(x3, y3)
                        c1, c2 = tr(x1, y1), p3
                    pts, ok = flatten_cubic(cur_xy, c1, c2, p3, self.tol)
                    cur.pts.extend(pts)
                    cur.flat_ok = cur.flat_ok and ok
                    cur_xy = p3
                elif op == "h":
                    if cur is not None:
                        cur.closed = True
                        subs.append(cur)
                        cur = None
                        cur_xy = start_xy
                elif op == "re":
                    x, y, w, h = _nums(operands, 4)
                    if cur is not None:
                        subs.append(cur)
                        cur = None
                    s = _Sub(idx, "re")
                    s.pts = [tr(x, y), tr(x + w, y), tr(x + w, y + h), tr(x, y + h)]
                    s.closed = True
                    subs.append(s)
                    cur_xy = start_xy = s.pts[0]
                elif op in CLIP_OPS:
                    pending_clip = True
                # ---------------- painting
                elif op in PAINT_OPS:
                    if cur is not None:
                        subs.append(cur)
                        cur = None
                    if op in ("s", "b", "b*"):
                        for s in subs:
                            s.closed = True
                    if pending_clip:
                        self.report.count("clip_paths")
                        self.report.unsup("clip_not_applied")
                        pending_clip = False
                    if op != "n":
                        stroke = op in ("S", "s", "B", "B*", "b", "b*")
                        fill = op in ("f", "F", "f*", "B", "B*", "b", "b*")
                        for s in subs:
                            self._emit(s, stroke, fill, gs, prefix, label, idx)
                    subs = []
                    cur_xy = start_xy = None
                # ---------------- XObjects, shading, text, misc
                elif op == "Do":
                    self._do(operands, label, idx, resources, gs, prefix, depth)
                elif op == "sh":
                    self.report.unsup("shading")
                elif op in _TEXT_SHOW_OPS:
                    self.report.unsup("text_show_ops")
                elif op in ("BDC", "BMC"):
                    if op == "BDC" and operands and str(operands[0]) == "/OC":
                        self.report.unsup("optional_content_not_filtered")
                elif op in _IGNORABLE_OPS:
                    pass
                else:
                    self.report.unknown_ops[op] = self.report.unknown_ops.get(op, 0) + 1
            except _BadOperand as e:
                self.report.error("BAD_OPERANDS", f"{op}: {e}", f"{label}#{idx}")
            except Exception as e:  # noqa: BLE001 - defensive: one bad operator must not kill a page
                self.report.error("OPERATOR_EXCEPTION", f"{op}: {type(e).__name__}: {e}", f"{label}#{idx}")

        if stack:
            self.report.error("Q_UNBALANCED", f"{len(stack)} unclosed q at end of stream", label)
        if cur is not None or subs:
            self.report.count("unpainted_path_at_end_of_stream")

    # -------------------------------------------------------------------- helpers
    def _apply_extgstate(self, gs: _GState, resources, name: str) -> None:
        try:
            d = resources.get("/ExtGState").get(name) if resources is not None else None
        except Exception:  # noqa: BLE001
            d = None
        if d is None:
            self.report.error("EXTGSTATE_NOT_FOUND", name)
            return
        for key in list(d.keys()):
            if key == "/LW":
                gs.line_w = _num(d[key])
            elif key == "/D":
                arr, phase = d[key]
                a = tuple(_num(v) for v in arr)
                gs.dash = (a, _num(phase)) if a else None
            elif key != "/Type":
                self.report.unsup("extgstate_param_ignored:" + str(key))

    def _do(self, operands, label, idx, resources, gs, prefix, depth) -> None:
        if len(operands) != 1:
            raise _BadOperand("Do expects 1 operand")
        name = str(operands[0])
        xo = lookup_xobject(resources, name)
        if xo is None:
            self.report.error("XOBJECT_NOT_FOUND", name, f"{label}#{idx}")
            return
        sub = str(xo.get("/Subtype", ""))
        if sub == "/Image":
            self.report.unsup("image_xobject")
            return
        if sub != "/Form":
            self.report.unsup("xobject_subtype:" + sub)
            return
        if depth >= MAX_FORM_DEPTH:
            self.report.error("FORM_DEPTH_EXCEEDED", name, f"{label}#{idx}")
            return
        try:
            og = xo.objgen
        except Exception:  # noqa: BLE001
            og = id(xo)
        if og in self._active_forms:
            self.report.error("FORM_CYCLE", name, f"{label}#{idx}")
            return
        self._active_forms.add(og)
        try:
            inner = gs.copy()
            inner.ctm = mat_mul(form_matrix(xo), gs.ctm)
            self.report.unsup("form_bbox_clip_not_applied")
            self.run_stream(xo, name.lstrip("/"), form_resources(xo, resources), inner,
                            prefix + ((label, idx),), depth + 1)
        finally:
            self._active_forms.discard(og)

    def _emit(self, s: _Sub, stroke: bool, fill: bool, gs: _GState, prefix: tuple,
              label: str, paint_idx: int) -> None:
        pts: list[tuple[float, float]] = []
        for p in s.pts:                      # drop exact consecutive duplicates
            if not pts or p != pts[-1]:
                pts.append(p)
        if len(s.pts) < 2 and s.origin != "re":
            self.report.count("empty_subpaths")
            return
        if len(self.prims) >= self.max_prims:
            if not self._prim_limit_hit:
                self._prim_limit_hit = True
                self.report.error("PRIM_LIMIT", f"more than {self.max_prims} primitives; output truncated")
            return
        if len(pts) == 1:
            kind, closed = "point", False
        else:
            kind, closed = "polyline", s.closed
            if closed and len(pts) >= 2 and pts[0] == pts[-1]:
                pts.pop()                    # explicit repeat of the first point at close
                if len(pts) < 2:
                    kind, closed = "point", False
        scale, aniso = stroke_width_scale(gs.ctm)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        chain = prefix + ((label, paint_idx),)
        ref = f"p{self.geom.page_index}" + "".join(f"/{lb}#{ix}" for lb, ix in chain)
        prov = Prov(
            page=self.geom.page_index, source=SRC_VECTOR, chain=chain, first_op=s.first_op,
            bbox=(min(xs), min(ys), max(xs), max(ys)),
            lineage=(f"vec:{ref}@{s.first_op}",),
        )
        prim = Prim(
            id=len(self.prims), kind=kind, pts=pts, closed=closed, stroke=stroke, fill=fill,
            stroke_w=(gs.line_w * scale) if stroke else 0.0,
            stroke_color=gs.stroke_color if stroke else None,
            fill_color=gs.fill_color if fill else None,
            dash=gs.dash if stroke else None, origin=s.origin,
            flatten_tol=self.tol, flatten_ok=s.flat_ok,
            anisotropic=aniso if stroke else False, prov=prov,
        )
        validate_prim(prim)
        if not s.flat_ok:
            self.report.error("FLATTEN_TOLERANCE_NOT_GUARANTEED", "depth limit hit", prov.content_ref)
        if aniso and stroke:
            self.report.count("anisotropic_stroke")
        self.prims.append(prim)
        self.report.count("prims")


def open_pdf(source, password: str = "") -> pikepdf.Pdf:
    if isinstance(source, (bytes, bytearray)):
        src: Any = io.BytesIO(bytes(source))
    else:
        if not os.path.exists(source):
            raise IngestError("FILE_NOT_FOUND", str(source))
        src = source
    try:
        return pikepdf.open(src, password=password)
    except pikepdf.PasswordError as e:
        raise IngestError("ENCRYPTED", "password required or wrong") from e
    except pikepdf.PdfError as e:
        raise IngestError("CORRUPT_OR_NOT_PDF", str(e)) from e


def ingest_pdf(source, *, pages: list[int] | None = None, flatten_tol: float = 0.05,
               password: str = "", max_prims: int = 2_000_000) -> IngestResult:
    """Extract vector primitives for the given page indices (default: all pages)."""
    if not (flatten_tol > 0 and math.isfinite(flatten_tol)):
        raise IngestError("BAD_PARAMETER", "flatten_tol must be positive and finite")
    pdf = open_pdf(source, password)
    try:
        n = len(pdf.pages)
        if n == 0:
            raise IngestError("NO_PAGES", "document has no pages")
        wanted = list(range(n)) if pages is None else list(pages)
        for i in wanted:
            if not (0 <= i < n):
                raise IngestError("PAGE_INDEX", f"page {i} out of range 0..{n - 1}")
        out: list[PageIngest] = []
        for i in wanted:
            page = pdf.pages[i]
            geom = page_geometry(page.obj, i)
            interp = _PageInterp(geom, flatten_tol, max_prims)
            for issue in geom.issues:
                interp.report.error(issue, "page geometry", f"p{i}")
            try:
                res = _inherited(page.obj, "/Resources")
                gs = _GState(ctm=geom.base_matrix)
                interp.run_stream(page, "page", res, gs, (), 0)
            except Exception as e:  # noqa: BLE001
                interp.report.error("PAGE_EXCEPTION", f"{type(e).__name__}: {e}", f"p{i}")
                interp.report.count("incomplete")
            out.append(PageIngest(geom, interp.prims, interp.report))
        return IngestResult(out)
    finally:
        pdf.close()
