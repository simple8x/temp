"""M4.2 test scenes (test helper, NOT part of ecci): PDF content + the connectivity the drawing MEANS.

The expected nets are written down from the construction of each scene (which pin ends a drawn wire joins, in
KiCad's own semantics: a crossing without a dot does not connect, a wire end on another wire does), never
from ECCI output, so they are an independent ground truth for the export.
"""
from __future__ import annotations

from collections import namedtuple

from render_kicad import ORIENTS, draw_symbol, place
from test_rules import LIB, H, disc, line, run

GtPin = namedtuple("GtPin", "ref number x y net hidden lib_id")
S = 3.0


def _sym(name, o, t):
    return draw_symbol(LIB.symbols[name], o, S, t, body_w=0.9, pin_w=0.45)


def tip(name, o, t, number):
    p = next(p for p in LIB.symbols[name].pins if p.number == number)
    return place(ORIENTS[o], S, t, *p.tip)


class Scene:
    def __init__(self, name):
        self.name, self.content, self.parts, self.nets = name, b"", [], []

    def add(self, ref, sym, o, t):
        self.content += _sym(sym, o, t)
        self.parts.append((ref, sym, o, t))

    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            self.content += line(*a, *b)

    def gt_pins(self):
        """Every pin of every drawn part, page coordinates (y down), net = expected net or a singleton."""
        net_of = {x: f"N{i}" for i, grp in enumerate(self.nets) for x in grp}
        out = []
        for ref, sym, o, t in self.parts:
            for p in LIB.symbols[sym].pins:
                x, y = place(ORIENTS[o], S, t, *p.tip)
                out.append(GtPin(ref, p.number, x, H - y, net_of.get((ref, p.number), f"solo:{ref}:{p.number}"),
                                 False, sym))
        return out

    def run(self):
        return run(self.content, symbols=LIB.symbols)


def chain(o: str) -> Scene:
    """D (anode) -- wire -- LED (cathode), both in orientation `o`, laid out along the pin axis."""
    s = Scene(f"chain_{o}")
    t1 = (120.0, 100.0)
    a = tip("D", o, t1, "2")
    u = ((a[0] - t1[0]) / 11.43, (a[1] - t1[1]) / 11.43)
    t2 = (t1[0] + u[0] * 52.86, t1[1] + u[1] * 52.86)
    s.add("D1", "D", o, t1)
    s.add("L1", "LED", o, t2)
    s.wire(a, tip("LED", o, t2, "1"))
    s.nets = [{("D1", "2"), ("L1", "1")}]
    return s


def _hbus(s: Scene):
    s.add("D1", "D", "r0", (60.0, 100.0))
    s.add("D2", "D", "r0", (240.0, 100.0))
    s.wire(tip("D", "r0", (60.0, 100.0), "1"), tip("D", "r0", (240.0, 100.0), "2"))


def crossing() -> Scene:
    """Two straight wires cross without a dot: two nets, never one."""
    s = Scene("crossing")
    _hbus(s)
    s.add("L1", "LED", "r90", (150.0, 170.0))
    s.add("L2", "LED", "r90", (150.0, 30.0))
    a, b = tip("LED", "r90", (150.0, 170.0), "2"), tip("LED", "r90", (150.0, 30.0), "1")
    s.wire(a, b)
    s.nets = [{("D1", "1"), ("D2", "2")}, {("L1", "2"), ("L2", "1")}]
    return s


def t_junction(dot: bool) -> Scene:
    """A wire ends on the middle of another. KiCad connects it; ECCI marks it INFERRED without a dot."""
    s = Scene("t_dot" if dot else "t_nodot")
    _hbus(s)
    s.add("L1", "LED", "r90", (150.0, 170.0))
    a = tip("LED", "r90", (150.0, 170.0), "2")
    s.wire(a, (150.0, 100.0))
    if dot:
        s.content += disc(150.0, 100.0, 1.0)
    s.nets = [{("D1", "1"), ("D2", "2"), ("L1", "2")}]
    return s


def ambiguous_r() -> Scene:
    """A symmetric R (pin numbers not decidable) wired to a D: the R is never placed."""
    s = Scene("ambiguous_r")
    s.add("R1", "R", "r90", (100.0, 100.0))
    s.add("D1", "D", "r0", (200.0, 100.0))
    s.wire(tip("R", "r90", (100.0, 100.0), "1"), tip("D", "r0", (200.0, 100.0), "2"))
    rt = tip("R", "r90", (100.0, 100.0), "1")
    s.nets = [{("R1", "1"), ("D1", "2")}] if rt[0] < 100 else [{("R1", "2"), ("D1", "2")}]
    return s


def all_scenes() -> list[Scene]:
    return [chain(o) for o in ORIENTS] + [crossing(), t_junction(False), t_junction(True), ambiguous_r()]
