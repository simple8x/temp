"""Evidence-based removal of sheet frame and title-block drawing geometry.

Only geometries supported by page-edge continuity or an enclosed, text-bearing
title-block rectangle are excluded. No fixed y-coordinate cutoff is used.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def _is_h(seg):
    return abs(seg.y1 - seg.y0) < 0.06


def _is_v(seg):
    return abs(seg.x1 - seg.x0) < 0.06


def _covered_length(intervals):
    if not intervals:
        return 0.0
    merged = []
    for lo, hi in sorted((min(a, b), max(a, b)) for a, b in intervals):
        if not merged or lo > merged[-1][1] + 1.0:
            merged.append([lo, hi])
        else:
            merged[-1][1] = max(merged[-1][1], hi)
    return sum(hi - lo for lo, hi in merged)


def _edge_profile(segs, width, height):
    band_x, band_y = max(4.0, width * 0.018), max(4.0, height * 0.018)
    groups = {"top": [], "bottom": [], "left": [], "right": []}
    counts = {key: 0 for key in groups}
    for seg in segs:
        if seg.kind not in ("line", "rect"):
            continue
        if _is_h(seg):
            side = "top" if abs(seg.y0) <= band_y else (
                "bottom" if abs(height - seg.y0) <= band_y else None
            )
            if side:
                groups[side].append((seg.x0, seg.x1))
                counts[side] += 1
        elif _is_v(seg):
            side = "left" if abs(seg.x0) <= band_x else (
                "right" if abs(width - seg.x0) <= band_x else None
            )
            if side:
                groups[side].append((seg.y0, seg.y1))
                counts[side] += 1
    dimensions = {"top": width, "bottom": width, "left": height, "right": height}
    return {
        side: {
            "segments": counts[side],
            "coverage_pt": round(_covered_length(groups[side]), 3),
            "coverage_ratio": round(_covered_length(groups[side]) / dimensions[side], 4),
        }
        for side in groups
    }


def _frame_sides(segs, width, height):
    """Return segments belonging to a continuous page-edge frame.

    A side must cover at least 45% of the corresponding page dimension and
    lie within a page-relative edge band. At least two orthogonal sides must
    be present, preventing a long circuit wire near one edge from being
    classified as the page frame.
    """
    band_x, band_y = max(4.0, width * 0.018), max(4.0, height * 0.018)
    candidates = defaultdict(list)
    for index, seg in enumerate(segs):
        if seg.kind not in ("line", "rect"):
            continue
        # Frame borders are often emitted as many short vector fragments.
        # Aggregate all edge-aligned fragments before applying a coverage test.
        if _is_h(seg):
            side = "top" if abs(seg.y0) <= band_y else (
                "bottom" if abs(height - seg.y0) <= band_y else None
            )
            if side:
                candidates[side].append((index, (seg.x0, seg.x1)))
        elif _is_v(seg):
            side = "left" if abs(seg.x0) <= band_x else (
                "right" if abs(width - seg.x0) <= band_x else None
            )
            if side:
                candidates[side].append((index, (seg.y0, seg.y1)))

    recognized = set()
    recognized_sides = set()
    for side, items in candidates.items():
        dimension = width if side in ("top", "bottom") else height
        coverage = _covered_length([interval for _, interval in items])
        if coverage >= dimension * 0.45:
            recognized.update(index for index, _ in items)
            recognized_sides.add(side)
    # Require a three-sided enclosure: a lone edge or a pair of unrelated
    # edge-near wires cannot be discarded as a page frame.
    return recognized if len(recognized_sides) >= 3 else set()


def _title_block_segments(segs, words, width, height):
    """Find a text-bearing closed title-block enclosure in the lower/right sheet area.

    The page-relative area is only a candidate search window. Exclusion requires
    a closed rectangle and at least two text boxes inside it; ordinary circuit
    symbols or wires in that area are not removed by position alone.
    """
    hs, vs = defaultdict(list), defaultdict(list)
    tol = 0.8
    for i, seg in enumerate(segs):
        if seg.kind not in ("line", "rect"):
            continue
        if _is_h(seg) and seg.length >= width * 0.08:
            x0, x1 = sorted((seg.x0, seg.x1))
            hs[(round(x0 / tol), round(x1 / tol))].append((i, seg.y0))
        elif _is_v(seg) and seg.length >= height * 0.025:
            vs[round(seg.x0 / tol)].append((i, min(seg.y0, seg.y1), max(seg.y0, seg.y1)))

    boxes = []
    for (qx0, qx1), top_edges in hs.items():
        x0, x1 = qx0 * tol, qx1 * tol
        if x1 - x0 < width * 0.10:
            continue
        for top_i, top_y in top_edges:
            for bottom_i, bottom_y in top_edges:
                if bottom_y <= top_y or bottom_y - top_y < height * 0.025:
                    continue
                edge_band_x, edge_band_y = max(4.0, width * 0.018), max(4.0, height * 0.018)
                touches_sheet_edge = (
                    x0 <= edge_band_x or width - x1 <= edge_band_x
                    or top_y <= edge_band_y or height - bottom_y <= edge_band_y
                )
                if not touches_sheet_edge:
                    continue
                left = [v for v in vs.get(round(x0 / tol), ())
                        if abs(v[1] - top_y) <= tol and abs(v[2] - bottom_y) <= tol]
                right = [v for v in vs.get(round(x1 / tol), ())
                         if abs(v[1] - top_y) <= tol and abs(v[2] - bottom_y) <= tol]
                if not left or not right:
                    continue
                inside = [
                    w for w in words
                    if w[0] >= x0 + 1 and w[2] <= x1 - 1
                    and w[1] >= top_y + 1 and w[3] <= bottom_y - 1
                    and str(w[4]).strip()
                ]
                if len(inside) >= 2:
                    boxes.append((x0, top_y, x1, bottom_y))
    excluded = set()
    for i, seg in enumerate(segs):
        sx0, sx1 = sorted((seg.x0, seg.x1))
        sy0, sy1 = sorted((seg.y0, seg.y1))
        for x0, y0, x1, y1 in boxes:
            # Include the enclosure and internal table rules, but not a wire
            # entering/leaving the box.
            if (sx0 >= x0 - tol and sx1 <= x1 + tol
                    and sy0 >= y0 - tol and sy1 <= y1 + tol):
                excluded.add(i)
                break
    return excluded


def exclude_sheet_metadata(segs, width, height, words=()):
    """Mutate metadata-only segments to non-electrical kinds; return audit counts."""
    frame = _frame_sides(segs, width, height)
    title = _title_block_segments(segs, words, width, height)
    title -= frame
    for index in frame:
        segs[index].kind = "sheet_frame"
    for index in title:
        segs[index].kind = "title_block"
    return {
        "sheet_frame_segments_excluded": len(frame),
        "title_block_segments_excluded": len(title),
        "edge_profile": _edge_profile(segs, width, height),
        "page_size_pt": [round(width, 3), round(height, 3)],
        "excluded_segment_indices": sorted(frame | title),
    }
