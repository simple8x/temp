"""Affine matrices (PDF row-vector convention) and page-space normalization.

Normalized page space (FACT, defined here):
  * unit: PDF point (1/72 in) multiplied by /UserUnit
  * origin: top-left corner of the *visible* page (CropBox ∩ MediaBox, after /Rotate)
  * x grows right, y grows DOWN

A matrix is a 6-tuple (a, b, c, d, e, f) with  x' = a*x + c*y + e,  y' = b*x + d*y + f.
`mat_mul(m, n)` applies m first, then n (row-vector product m × n).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

DEFAULT_MEDIABOX = (0.0, 0.0, 612.0, 792.0)


def mat_mul(m: Matrix, n: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def det(m: Matrix) -> float:
    return m[0] * m[3] - m[1] * m[2]


def linear_scales(m: Matrix) -> tuple[float, float]:
    """Singular values (s_max, s_min) of the linear part."""
    a, b, c, d = m[0], m[1], m[2], m[3]
    # eigenvalues of M^T M for the 2x2 linear part
    p = a * a + b * b
    q = c * c + d * d
    r = a * c + b * d
    tr = p + q
    disc = math.sqrt(max(0.0, (p - q) ** 2 + 4.0 * r * r))
    l1 = max(0.0, (tr + disc) / 2.0)
    l2 = max(0.0, (tr - disc) / 2.0)
    return (math.sqrt(l1), math.sqrt(l2))


def stroke_width_scale(m: Matrix) -> tuple[float, bool]:
    """Scale factor for a user-space line width and whether the CTM is anisotropic."""
    s1, s2 = linear_scales(m)
    if s1 == 0.0:
        return 0.0, False
    aniso = (s1 - s2) > 1e-6 * s1
    return math.sqrt(abs(det(m))), aniso


@dataclass(frozen=True)
class PageGeom:
    page_index: int
    width: float          # normalized units (pt * UserUnit), after rotation
    height: float
    rotate: int           # 0 | 90 | 180 | 270 (clockwise, as displayed)
    userunit: float
    mediabox: tuple[float, float, float, float]
    cropbox: tuple[float, float, float, float]   # effective visible box in user space
    base_matrix: Matrix   # default user space -> normalized page space
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "page_index": self.page_index, "width": self.width, "height": self.height,
            "rotate": self.rotate, "userunit": self.userunit,
            "mediabox": list(self.mediabox), "cropbox": list(self.cropbox),
            "base_matrix": list(self.base_matrix), "issues": list(self.issues),
        }


def _norm_box(vals) -> tuple[float, float, float, float] | None:
    try:
        v = [float(x) for x in vals]
    except Exception:
        return None
    if len(v) != 4 or not all(math.isfinite(x) for x in v):
        return None
    x0, x1 = sorted((v[0], v[2]))
    y0, y1 = sorted((v[1], v[3]))
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return (x0, y0, x1, y1)


def _inherited(obj, key: str):
    """Look up an inheritable page attribute, walking /Parent (cycle-safe)."""
    seen = set()
    cur = obj
    while cur is not None:
        try:
            if key in cur:
                return cur[key]
            parent = cur.get("/Parent")
        except Exception:
            return None
        if parent is None:
            return None
        try:
            og = parent.objgen
        except Exception:
            og = id(parent)
        if og in seen:
            return None
        seen.add(og)
        cur = parent
    return None


def page_geometry(page_obj, page_index: int) -> PageGeom:
    issues: list[str] = []

    mb_raw = _inherited(page_obj, "/MediaBox")
    mb = _norm_box(mb_raw) if mb_raw is not None else None
    if mb is None:
        issues.append("BAD_OR_MISSING_MEDIABOX_DEFAULTED")
        mb = DEFAULT_MEDIABOX

    cb_raw = _inherited(page_obj, "/CropBox")
    cb = _norm_box(cb_raw) if cb_raw is not None else None
    if cb_raw is not None and cb is None:
        issues.append("BAD_CROPBOX_IGNORED")
    if cb is None:
        cb = mb
    else:
        ix0, iy0 = max(cb[0], mb[0]), max(cb[1], mb[1])
        ix1, iy1 = min(cb[2], mb[2]), min(cb[3], mb[3])
        if ix1 - ix0 <= 0 or iy1 - iy0 <= 0:
            issues.append("CROPBOX_OUTSIDE_MEDIABOX_USING_MEDIABOX")
            cb = mb
        else:
            cb = (ix0, iy0, ix1, iy1)

    rot_raw = _inherited(page_obj, "/Rotate")
    rot = 0
    if rot_raw is not None:
        try:
            r = int(rot_raw)
            if r % 90 == 0:
                rot = r % 360
            else:
                issues.append("BAD_ROTATE_IGNORED")
        except Exception:
            issues.append("BAD_ROTATE_IGNORED")

    uu_raw = page_obj.get("/UserUnit") if hasattr(page_obj, "get") else None
    uu = 1.0
    if uu_raw is not None:
        try:
            uu = float(uu_raw)
            if not (math.isfinite(uu) and uu > 0):
                raise ValueError
        except Exception:
            uu = 1.0
            issues.append("BAD_USERUNIT_IGNORED")

    x0, y0, x1, y1 = cb
    w, h = x1 - x0, y1 - y0
    flip: Matrix = (1.0, 0.0, 0.0, -1.0, -x0, y1)       # (x,y)->(x-x0, y1-y) = (u,v), y down
    if rot == 0:
        rotm: Matrix = IDENTITY
        pw, ph = w, h
    elif rot == 90:      # (u,v) -> (H - v, u)
        rotm = (0.0, 1.0, -1.0, 0.0, h, 0.0)
        pw, ph = h, w
    elif rot == 180:     # (u,v) -> (W - u, H - v)
        rotm = (-1.0, 0.0, 0.0, -1.0, w, h)
        pw, ph = w, h
    else:                # 270: (u,v) -> (v, W - u)
        rotm = (0.0, -1.0, 1.0, 0.0, 0.0, w)
        pw, ph = h, w
    scale: Matrix = (uu, 0.0, 0.0, uu, 0.0, 0.0)
    base = mat_mul(mat_mul(flip, rotm), scale)
    return PageGeom(page_index, pw * uu, ph * uu, rot, uu, mb, cb, base, tuple(issues))
