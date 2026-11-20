"""Draws library symbols into PDF content streams (test helper, NOT part of ecci).

Ground truth for pin positions is computed here from the placement (rotation/mirror, scale,
translation) with its own matrices, never from ecci.template.
"""

from __future__ import annotations

import math

from ecci.kicad_lib import SymbolDef

# PDF user space (y up). Independent definition of the 8 square symmetries.
ORIENTS = {
    "r0": (1, 0, 0, 1), "r90": (0, -1, 1, 0), "r180": (-1, 0, 0, -1), "r270": (0, 1, -1, 0),
    "mx": (-1, 0, 0, 1), "my": (1, 0, 0, -1), "dg": (0, 1, 1, 0), "ad": (0, -1, -1, 0),
}
K = 0.5522847498


def place(m, s, t, x, y):
    a, b, c, d = m
    return (s * (a * x + b * y) + t[0], s * (c * x + d * y) + t[1])


def _f(v: float) -> str:
    return f"{v:.5f}"


def draw_symbol(sym: SymbolDef, orient: str, s: float, t: tuple[float, float], *, body_w: float,
                pin_w: float, pins: bool = True, skip_pins: tuple[str, ...] = ()) -> bytes:
    m = ORIENTS[orient]
    out = [f"{_f(body_w)} w"]
    for path in sym.body:
        pts = [place(m, s, t, *p) for p in path.pts]
        if path.smooth and path.closed:          # circle -> 4 Béziers through 4 extreme points
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            r = math.hypot(pts[0][0] - cx, pts[0][1] - cy)
            out.append(f"{_f(cx + r)} {_f(cy)} m "
                       f"{_f(cx + r)} {_f(cy + K * r)} {_f(cx + K * r)} {_f(cy + r)} {_f(cx)} {_f(cy + r)} c "
                       f"{_f(cx - K * r)} {_f(cy + r)} {_f(cx - r)} {_f(cy + K * r)} {_f(cx - r)} {_f(cy)} c "
                       f"{_f(cx - r)} {_f(cy - K * r)} {_f(cx - K * r)} {_f(cy - r)} {_f(cx)} {_f(cy - r)} c "
                       f"{_f(cx + K * r)} {_f(cy - r)} {_f(cx + r)} {_f(cy - K * r)} {_f(cx + r)} {_f(cy)} c h S")
            continue
        body = f"{_f(pts[0][0])} {_f(pts[0][1])} m " + " ".join(f"{_f(x)} {_f(y)} l" for x, y in pts[1:])
        out.append(body + (" h S" if path.closed else " S"))
    if pins:
        out.append(f"{_f(pin_w)} w")
        for pin in sym.pins:
            if pin.number in skip_pins or pin.length == 0:
                continue
            tip = place(m, s, t, *pin.tip)
            base = place(m, s, t, *pin.base)
            out.append(f"{_f(base[0])} {_f(base[1])} m {_f(tip[0])} {_f(tip[1])} l S")
    return ("\n".join(out) + "\n").encode()


def expected_tips_norm(sym: SymbolDef, orient: str, s: float, t, page_h: float) -> dict[str, tuple[float, float]]:
    """pin number -> tip in normalized page space (y down) for an unrotated, origin-0 page."""
    m = ORIENTS[orient]
    out = {}
    for pin in sym.pins:
        x, y = place(m, s, t, *pin.tip)
        out[pin.number] = (x, page_h - y)
    return out
