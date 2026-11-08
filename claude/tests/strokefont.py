"""A tiny stroke font for tests: letters are open polylines in the SAME style as wires.

Glyph boxes are 0.6 wide and 1.0 high (scaled by h). Ground truth for 'what is text' comes
from where the test places it, never from ecci output.
"""

GLYPHS = {
    "R": [[(0, 0), (0, 1), (0.5, 1), (0.6, 0.85), (0.6, 0.65), (0.5, 0.5), (0, 0.5)], [(0.3, 0.5), (0.6, 0)]],
    "E": [[(0.6, 1), (0, 1), (0, 0), (0.6, 0)], [(0, 0.5), (0.45, 0.5)]],
    "D": [[(0, 0), (0, 1), (0.35, 1), (0.6, 0.75), (0.6, 0.25), (0.35, 0), (0, 0)]],
    "L": [[(0, 1), (0, 0), (0.6, 0)]],
    "T": [[(0, 1), (0.6, 1)], [(0.3, 1), (0.3, 0)]],
    "H": [[(0, 0), (0, 1)], [(0.6, 0), (0.6, 1)], [(0, 0.5), (0.6, 0.5)]],
    "I": [[(0.3, 0), (0.3, 1)]],
    "1": [[(0.1, 0.8), (0.3, 1), (0.3, 0)]],
    "0": [[(0, 0), (0, 1), (0.6, 1), (0.6, 0), (0, 0)]],
    "2": [[(0, 0.8), (0.2, 1), (0.5, 1), (0.6, 0.8), (0, 0), (0.6, 0)]],
    "K": [[(0, 0), (0, 1)], [(0.6, 1), (0, 0.45), (0.6, 0)]],
    "N": [[(0, 0), (0, 1), (0.6, 0), (0.6, 1)]],
}


def text_content(s: str, x: float, y: float, h: float, width: float = 0.45, advance: float = 0.8) -> bytes:
    """PDF user-space content drawing string `s` with baseline at y, glyph height h."""
    out = [f"{width:.3f} w"]
    cx = x
    for ch in s:
        if ch != " ":
            for path in GLYPHS[ch]:
                pts = [(cx + gx * h, y + gy * h) for gx, gy in path]
                out.append(f"{pts[0][0]:.3f} {pts[0][1]:.3f} m " +
                           " ".join(f"{px:.3f} {py:.3f} l" for px, py in pts[1:]) + " S")
        cx += advance * h
    return ("\n".join(out) + "\n").encode()


def text_width(s: str, h: float, advance: float = 0.8) -> float:
    return (len(s) - 1) * advance * h + 0.6 * h
