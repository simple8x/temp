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
    samples = {key: [] for key in groups}
    counts = {key: 0 for key in groups}
    for index, seg in enumerate(segs):
        if seg.kind not in ("line", "rect"):
            continue
        if _is_h(seg):
            side = "top" if abs(seg.y0) <= band_y else (
                "bottom" if abs(height - seg.y0) <= band_y else None
            )
            if side:
                groups[side].append((seg.x0, seg.x1))
                samples[side].append([index, seg.kind, round(seg.x0, 3), round(seg.y0, 3), round(seg.x1, 3), round(seg.y1, 3)])
                counts[side] += 1
        elif _is_v(seg):
            side = "left" if abs(seg.x0) <= band_x else (
                "right" if abs(width - seg.x0) <= band_x else None
            )
            if side:
                groups[side].append((seg.y0, seg.y1))
                samples[side].append([index, seg.kind, round(seg.x0, 3), round(seg.y0, 3), round(seg.x1, 3), round(seg.y1, 3)])
                counts[side] += 1
    dimensions = {"top": width, "bottom": width, "left": height, "right": height}
    return {
        side: {
            "segments": counts[side],
            "coverage_pt": round(_covered_length(groups[side]), 3),
            "coverage_ratio": round(_covered_length(groups[side]) / dimensions[side], 4),
            "samples": samples[side][:20],
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
        box_width = x1 - x0
        if not (width * 0.10 <= box_width <= width * 0.40):
            continue
        for top_i, top_y in top_edges:
            for bottom_i, bottom_y in top_edges:
                box_height = bottom_y - top_y
                if (box_height < height * 0.025 or box_height > height * 0.20
                        or box_width / max(box_height, tol) < 1.5):
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


def _footer_table_segments(segs, width, height):
    """Detect the open, bottom-right revision/approval table used by this PDF.

    Unlike the strict title-block detector, this table is not a closed rectangle:
    its long horizontal rules share endpoints with vertical cell dividers that
    continue beyond the row. Require two aligned footer rules plus left, right,
    and at least one internal vertical divider; position alone is never enough.
    """
    horizontal = []
    for i, seg in enumerate(segs):
        if seg.kind not in ("line", "rect") or not _is_h(seg):
            continue
        x0, x1 = sorted((seg.x0, seg.x1))
        if (seg.y0 >= height * 0.78 and seg.length >= width * 0.50
                and x0 >= width * 0.15 and x1 >= width * 0.95):
            horizontal.append((i, x0, x1, seg.y0))
    excluded = set()
    for pos, a in enumerate(horizontal):
        for b in horizontal[pos + 1:]:
            top, bottom = sorted((a[3], b[3]))
            gap = bottom - top
            if not height * 0.02 <= gap <= height * 0.05:
                continue
            overlap = max(0.0, min(a[2], b[2]) - max(a[1], b[1]))
            if overlap / max(1.0, min(a[2] - a[1], b[2] - b[1])) < 0.90:
                continue
            x0, x1 = min(a[1], b[1]), max(a[2], b[2])
            verticals = []
            for i, seg in enumerate(segs):
                if seg.kind not in ("line", "rect") or not _is_v(seg):
                    continue
                x = seg.x0
                sy0, sy1 = sorted((seg.y0, seg.y1))
                if x0 - 2.0 <= x <= x1 + 2.0 and sy1 >= top - 2.0 and sy0 <= bottom + 2.0:
                    verticals.append((i, x))
            left = any(abs(x - x0) <= 3.0 for _, x in verticals)
            right = any(abs(x - x1) <= 3.0 for _, x in verticals)
            inner = {round(x, 1) for _, x in verticals if x0 + 3.0 < x < x1 - 3.0}
            if not (left and right and inner):
                continue
            excluded.update((a[0], b[0]))
            excluded.update(i for i, _ in verticals)
            for i, seg in enumerate(segs):
                sx0, sx1 = sorted((seg.x0, seg.x1))
                sy0, sy1 = sorted((seg.y0, seg.y1))
                if (sx0 >= x0 - 2.0 and sx1 <= x1 + 2.0
                        and sy0 >= top - 2.0 and sy1 <= bottom + 2.0):
                    excluded.add(i)
    return excluded


def _top_badge_geometry_segments(segs, width, height):
    """Recognize the small rounded classification badge at the top page edge.

    Some exports retain the rounded vector outline but omit the red label from
    the searchable/stroke text layer. Require a compact four-sided outline in
    the top 6% of the page with a wide aspect ratio and both vertical sides.
    """
    hs = [(i, min(seg.x0, seg.x1), max(seg.x0, seg.x1), seg.y0)
          for i, seg in enumerate(segs)
          if seg.kind in ("line", "rect") and _is_h(seg)
          and seg.y0 <= height * 0.06 and width * 0.10 <= seg.length <= width * 0.25]
    vs = [(i, seg.x0, min(seg.y0, seg.y1), max(seg.y0, seg.y1))
          for i, seg in enumerate(segs)
          if seg.kind in ("line", "rect") and _is_v(seg)
          and seg.y0 <= height * 0.08 and seg.y1 <= height * 0.08]
    for a_pos, a in enumerate(hs):
        for b in hs[a_pos + 1:]:
            top, bottom = sorted((a[3], b[3]))
            box_h = bottom - top
            if not height * 0.025 <= box_h <= height * 0.06:
                continue
            overlap = max(0.0, min(a[2], b[2]) - max(a[1], b[1]))
            if overlap / max(1.0, min(a[2] - a[1], b[2] - b[1])) < 0.85:
                continue
            left_edge, right_edge = min(a[1], b[1]), max(a[2], b[2])
            left = [(i, x, y0, y1) for i, x, y0, y1 in vs
                    if left_edge - 6.0 <= x <= left_edge + 1.0
                    and y1 >= top - 2.0 and y0 <= bottom + 2.0]
            right = [(i, x, y0, y1) for i, x, y0, y1 in vs
                     if right_edge - 1.0 <= x <= right_edge + 6.0
                     and y1 >= top - 2.0 and y0 <= bottom + 2.0]
            if not left or not right:
                continue
            x0, x1 = min(x for _, x, _, _ in left), max(x for _, x, _, _ in right)
            box_w = x1 - x0
            if not width * 0.12 <= box_w <= width * 0.25 or box_w / box_h < 3.0:
                continue
            if x0 < width * 0.50 or x1 > width * 0.90:
                continue
            excluded = {a[0], b[0]}
            excluded.update(i for i, _, _, _ in left + right)
            for i, seg in enumerate(segs):
                sx0, sx1 = sorted((seg.x0, seg.x1))
                sy0, sy1 = sorted((seg.y0, seg.y1))
                if (sx0 >= x0 - 2.0 and sx1 <= x1 + 2.0
                        and sy0 >= top - 2.0 and sy1 <= bottom + 2.0):
                    excluded.add(i)
            return excluded
    return set()


def exclude_sheet_metadata(segs, width, height, words=()):
    """Mutate metadata-only segments to non-electrical kinds; return audit counts."""
    frame = _frame_sides(segs, width, height)
    title = _title_block_segments(segs, words, width, height)
    footer = _footer_table_segments(segs, width, height)
    badge = _top_badge_geometry_segments(segs, width, height)
    title -= frame | footer | badge
    for index in frame:
        segs[index].kind = "sheet_frame"
    for index in title | footer:
        segs[index].kind = "title_block"
    for index in badge:
        segs[index].kind = "sheet_metadata_badge"
    return {
        "sheet_frame_segments_excluded": len(frame),
        "title_block_segments_excluded": len(title | footer),
        "footer_table_segments_excluded": len(footer),
        "confidential_badge_segments_excluded": len(badge),
        "edge_profile": _edge_profile(segs, width, height),
        "page_size_pt": [round(width, 3), round(height, 3)],
        "excluded_segment_indices": sorted(frame | title | footer | badge),
    }
