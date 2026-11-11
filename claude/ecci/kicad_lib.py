"""KiCad symbol library (.kicad_sym) reader: body geometry and pin definitions.

Coordinates stay in library space (millimetres, y UP, as stored in the file). A pin's `at`
is its CONNECTION POINT (tip); its angle is the direction from the tip toward the body.
Only units 0 (common) and 1 are read; other units are reported in `warnings`.
The library file's SHA-256 is part of every template lineage token (`lib:<name>@<sha12>`).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from .sexpr import SexprError, child, children, num, parse

CIRCLE_SEGMENTS = 48
ARC_SEGMENTS = 24


class LibraryError(Exception):
    pass


@dataclass(frozen=True)
class PinDef:
    number: str
    name: str
    x: float
    y: float
    angle: float          # degrees, direction from tip toward body (library space, y up)
    length: float

    @property
    def tip(self) -> tuple[float, float]:
        return (self.x, self.y)

    @property
    def base(self) -> tuple[float, float]:
        a = math.radians(self.angle)
        return (self.x + self.length * math.cos(a), self.y + self.length * math.sin(a))


@dataclass(frozen=True)
class BodyPath:
    pts: tuple[tuple[float, float], ...]
    closed: bool
    smooth: bool = False   # circle/arc samples: interior vertices are not corners


@dataclass(frozen=True)
class SymbolDef:
    name: str
    pins: tuple[PinDef, ...]
    body: tuple[BodyPath, ...]
    lib_token: str
    warnings: tuple[str, ...] = ()

    def segments(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        out = []
        for path in self.body:
            n = len(path.pts)
            for i in range(n - 1):
                out.append((path.pts[i], path.pts[i + 1]))
            if path.closed and n >= 3:
                out.append((path.pts[-1], path.pts[0]))
        return out

    def corners(self) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for path in self.body:
            if path.smooth:
                pts += [path.pts[0], path.pts[-1]] if not path.closed else []
            else:
                pts += list(path.pts)
        return pts


@dataclass
class Library:
    path: str
    sha256: str
    symbols: dict[str, SymbolDef] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _xy(node: list) -> tuple[float, float]:
    return (num(node[1]), num(node[2]))


def _pts(node: list) -> tuple[tuple[float, float], ...]:
    p = child(node, "pts")
    if p is None:
        raise SexprError("polyline without pts")
    return tuple(_xy(c) for c in children(p, "xy"))


def _circle(cx: float, cy: float, r: float) -> BodyPath:
    pts = tuple((cx + r * math.cos(2 * math.pi * k / CIRCLE_SEGMENTS),
                 cy + r * math.sin(2 * math.pi * k / CIRCLE_SEGMENTS)) for k in range(CIRCLE_SEGMENTS))
    return BodyPath(pts, True, True)


def _arc(a: tuple[float, float], m: tuple[float, float], b: tuple[float, float]) -> BodyPath:
    # circle through three points; sample from a to b passing through m
    ax, ay = a; mx, my = m; bx, by = b
    d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(d) < 1e-12:
        return BodyPath((a, b), False)
    ux = ((ax**2 + ay**2) * (my - by) + (mx**2 + my**2) * (by - ay) + (bx**2 + by**2) * (ay - my)) / d
    uy = ((ax**2 + ay**2) * (bx - mx) + (mx**2 + my**2) * (ax - bx) + (bx**2 + by**2) * (mx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    ta, tm, tb = (math.atan2(p[1] - uy, p[0] - ux) for p in (a, m, b))
    def ccw(x, y):  # angle from x to y counter-clockwise in [0, 2pi)
        return (y - x) % (2 * math.pi)
    if ccw(ta, tm) <= ccw(ta, tb):
        sweep = ccw(ta, tb)
    else:
        sweep = -((2 * math.pi) - ccw(ta, tb))
    pts = tuple((ux + r * math.cos(ta + sweep * k / ARC_SEGMENTS),
                 uy + r * math.sin(ta + sweep * k / ARC_SEGMENTS)) for k in range(ARC_SEGMENTS + 1))
    return BodyPath(pts, False, True)


def _read_symbol(node: list, lib_token: str) -> list[SymbolDef]:
    """One SymbolDef per electrical unit. Unit 0 (common graphics/pins) is added to every unit; the De Morgan
    alternative style (style >= 2) is skipped. Unit 1 (or the only unit) keeps the plain symbol name, the other
    units are named '<name>#u<k>', so a template match says WHICH unit of the part was drawn."""
    name = str(node[1])
    per_unit: dict[str, tuple[list[PinDef], list[BodyPath]]] = {}
    warns: list[str] = []
    for sub in children(node, "symbol"):
        parts = str(sub[1]).rsplit("_", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            unit, style = parts[1], int(parts[2])
        else:
            unit, style = "0", 0
        if style >= 2:
            warns.append(f"{name}: De Morgan style {style} of unit {unit} ignored")
            continue
        pins, body = per_unit.setdefault(unit, ([], []))
        for g in sub:
            if not isinstance(g, list) or not g:
                continue
            h = g[0]
            if h == "rectangle":
                (x0, y0), (x1, y1) = _xy(child(g, "start")), _xy(child(g, "end"))
                body.append(BodyPath(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), True))
            elif h == "polyline":
                pts = _pts(g)
                if len(pts) >= 2:
                    body.append(BodyPath(pts, len(pts) >= 4 and pts[0] == pts[-1]))
            elif h == "circle":
                cx, cy = _xy(child(g, "center"))
                body.append(_circle(cx, cy, num(child(g, "radius")[1])))
            elif h == "arc":
                body.append(_arc(_xy(child(g, "start")), _xy(child(g, "mid")), _xy(child(g, "end"))))
            elif h == "pin":
                at = child(g, "at")
                pins.append(PinDef(
                    number=str(child(g, "number")[1]), name=str(child(g, "name")[1]),
                    x=num(at[1]), y=num(at[2]), angle=num(at[3]) if len(at) > 3 else 0.0,
                    length=num(child(g, "length")[1])))
    common_pins, common_body = per_unit.get("0", ([], []))
    units = sorted((u for u in per_unit if u != "0"), key=int) or ["0"]
    out: list[SymbolDef] = []
    for u in units:
        pins, body = per_unit.get(u, ([], []))
        allp, allb = list(common_pins) + (list(pins) if u != "0" else []), list(common_body) + (list(body) if u != "0" else [])
        fixed = []
        for b in allb:                  # closed polylines repeating the first point: drop the duplicate end point
            if b.closed and len(b.pts) >= 2 and b.pts[0] == b.pts[-1]:
                fixed.append(BodyPath(b.pts[:-1], True, b.smooth))
            else:
                fixed.append(b)
        nm = name if u in ("0", units[0]) else f"{name}#u{u}"
        out.append(SymbolDef(nm, tuple(allp), tuple(fixed), lib_token, tuple(warns)))
    return out


def load_library(path: str) -> Library:
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        raise LibraryError(f"cannot read library {path!r}: {e}") from e
    sha = hashlib.sha256(raw).hexdigest()
    try:
        tree = parse(raw.decode("utf-8"))
    except (UnicodeDecodeError, SexprError) as e:
        raise LibraryError(f"cannot parse library {path!r}: {e}") from e
    if not tree or tree[0] != "kicad_symbol_lib":
        raise LibraryError("not a kicad_symbol_lib file")
    lib = Library(path, sha)
    token = f"lib:{path.replace(chr(92), '/').rsplit('/', 1)[-1]}@{sha[:12]}"
    for sym in children(tree, "symbol"):
        try:
            sds = _read_symbol(sym, token)
        except (SexprError, TypeError, IndexError) as e:
            lib.warnings.append(f"symbol {sym[1] if len(sym) > 1 else '?'} skipped: {type(e).__name__}: {e}")
            continue
        for k, sd in enumerate(sds):
            if not sd.body:
                lib.warnings.append(f"symbol {sd.name} skipped: no body graphics")
                continue
            lib.symbols[sd.name] = sd
            if k == 0:
                lib.warnings.extend(sd.warnings)
    return lib
