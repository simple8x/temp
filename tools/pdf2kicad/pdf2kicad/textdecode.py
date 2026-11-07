"""Step 3: turn glyph point clouds into characters by matching the exact KiCad stroke font."""
import numpy as np
from . import glyphs as G

ACCEPT = 0.025          # max mean chamfer distance (em) for a confident match
ACCEPT_WIDE = 0.04      # slightly worse fit is fine when no other letter comes close (e.g. Q with its tail)
WIDE_MARGIN = 0.02
TIE = 0.008             # candidates this close to the best are disambiguated by baseline position
SIZE_TOL = 0.14         # allowed width/height difference (em) before a template is even tried


def _obs(g, rot, em):
    segs = G.frame_segs(g, rot)
    pts = G.sample_points(segs, 0.06 * em)
    px = (pts[:, 0] - pts[:, 0].min()) / em
    up = -pts[:, 1] / em
    return px, up


class _Bank:
    """All templates stacked once so one glyph is compared with every template in a single matrix operation."""
    def __init__(self, templates):
        self.templates = templates
        rel = [np.column_stack((t.pts[:, 0], t.pts[:, 1] - t.y0)) for t in templates]      # y relative to the template's own bottom
        self.rel = rel
        self.counts = np.array([len(r) for r in rel])
        self.starts = np.concatenate(([0], np.cumsum(self.counts)[:-1]))
        self.pts = np.vstack(rel)
        self.x1 = np.array([t.x1 for t in templates])
        self.h = np.array([t.y1 - t.y0 for t in templates])
        self.y0 = np.array([t.y0 for t in templates])
        self.chars = [t.char for t in templates]


_BANKS = {}


def _bank(templates):
    key = id(templates)
    b = _BANKS.get(key)
    if b is None or b.templates is not templates:
        b = _BANKS[key] = _Bank(templates)
    return b


def candidates(g, rot, em, templates):
    """All plausible templates for glyph g as [(cost, char, observed_baseline_up)], best first."""
    px, up = _obs(g, rot, em)
    gw, gh, gy0 = px.max(), up.max() - up.min(), up.min()
    bk = _bank(templates)
    ok = (np.abs(bk.x1 - gw) <= SIZE_TOL) & (np.abs(bk.h - gh) <= SIZE_TOL)
    if not ok.any():
        return []
    obs = np.column_stack((px, up - gy0))
    idx = np.nonzero(ok)[0]                                    # only templates of about the right size
    sub = [bk.rel[i] for i in idx]
    cnt = np.array([len(r) for r in sub])
    starts = np.concatenate(([0], np.cumsum(cnt)[:-1]))
    allp = np.vstack(sub)
    dx = obs[:, 0:1] - allp[None, :, 0]
    dy = obs[:, 1:2] - allp[None, :, 1]
    d2 = dx * dx + dy * dy                                      # squared distances: sqrt only on the minima
    obs_to_t = np.sqrt(np.minimum.reduceat(d2, starts, axis=1)).mean(0)
    t_to_obs = np.add.reduceat(np.sqrt(d2.min(0)), starts) / cnt
    cost = 0.5 * (obs_to_t + t_to_obs)
    out = [(float(cost[k]), bk.chars[i], float(gy0 - bk.y0[i])) for k, i in enumerate(idx)]
    out.sort()
    return out


def decode_word(word, templates, ems):
    if not isinstance(ems, (list, tuple)):
        ems = [ems]
    multi = len(word.glyphs) > 1
    rots = [word.rot] if multi else [0, 90]
    best = None
    for rot, page_em in [(r, e) for r in rots for e in ems]:
        order = sorted(word.glyphs, key=lambda g: G._to_frame(g, rot)[0])
        cands = [candidates(g, rot, page_em, templates) for g in order]
        sure = [c[0][2] for c in cands if c and c[0][0] <= ACCEPT and (len(c) == 1 or c[1][0] - c[0][0] > TIE)]
        base = float(np.median(sure)) if sure else None
        chars, costs = [], []
        for g, c in zip(order, cands):
            if not c:
                g.char, g.score = "?", 9.9
            else:
                top = [x for x in c if x[0] <= c[0][0] + TIE]
                if base is not None and len(top) > 1:
                    top.sort(key=lambda x: abs(x[2] - base))
                margin = (c[1][0] - c[0][0]) if len(c) > 1 else 1.0
                sure_enough = top[0][0] <= ACCEPT or (top[0][0] <= ACCEPT_WIDE and margin >= WIDE_MARGIN)
                g.char, g.score = (top[0][1], top[0][0]) if sure_enough else ("?", top[0][0])
            chars.append(g.char)
            costs.append(g.score)
        text = "".join(chars)
        key = (text.count("?"), max(costs))
        if best is None or key < best[0]:
            best = (key, rot, text, max(costs), order, page_em, list(chars), list(costs))
    _, rot, text, score, order, em_used, chars, costs = best
    for g, ch, c in zip(order, chars, costs):          # keep the winning reading, not the last one tried
        g.char, g.score = ch, c
    word.rot, word.text, word.score, word.glyphs, word.em = rot, text, score, order, em_used
    return word


def strip_junk(words):
    """Unknown glyphs at either end are drawing fragments that touched the text, not letters."""
    out = []
    for w in words:
        gl = list(w.glyphs)
        while len(gl) > 1 and gl[0].char == "?":
            gl.pop(0)
        while len(gl) > 1 and gl[-1].char == "?":
            gl.pop()
        if len(gl) != len(w.glyphs):
            w.glyphs = gl
            w.text = "".join(g.char for g in gl)
            w.update_box()
        out.append(w)
    return out


def is_text(w):
    """A word counts as text when it decoded and is not a stack of plain strokes (ground symbols etc.)."""
    if not w.text or w.text.count("?") * 2 > len(w.text):
        return False
    if len(w.glyphs) > 1 and all(len(g.segs) == 1 for g in w.glyphs):
        return False
    if len(w.glyphs) == 1 and w.glyphs[0].char in "-_|.l" and len(w.glyphs[0].segs) <= 1:
        return False
    return True


def decode_all(words, templates, page_em):
    return strip_junk([decode_word(w, templates, page_em) for w in words])
