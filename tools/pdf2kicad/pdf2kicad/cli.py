"""pdf2kicad PAGE-LEVEL extractor (experimental).  Usage: python -m pdf2kicad.cli in.pdf PAGE outdir [--info] [--mode M] [--scale S]  (writes netlist.csv + parts.csv for tools/gen_kicad_sch.py)"""
import csv, re, sys
from .primitives import load_page
from .normalize import classify, text_layer_words, estimate_scale
from . import glyphs as G, font, textdecode as T, topology as TP, netlist as NL


def _quality(pg):
    """(clean-decode fraction, horizontal ref count). A correct scale/orientation decodes ~100% of words."""
    allw = G.build_words(G.find_glyphs(pg.segs))
    T.decode_all(allw, font.load_templates(), G.estimate_text_sizes(allw))
    words = [w for w in allw if len(w.glyphs) >= 2]
    if not words:
        return (0.0, 0), allw
    clean = sum(1 for w in words if "?" not in w.text) / len(words)
    return (clean, sum(1 for w in words if NL.REF.match(w.text) and w.rot == 0)), allw


SCALES = (1.0, 0.5, 2.0, 0.7, 1.4, 0.35, 2.8, 0.25, 4.0)


def _window(pg, frac=0.4):
    """Same page restricted to its central window: enough text to judge scale/orientation, ~6x less work."""
    import copy
    xs = [s.x0 for s in pg.segs]
    ys = [s.y0 for s in pg.segs]
    if not xs:
        return pg
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    hw, hh = (max(xs) - min(xs)) * frac / 2, (max(ys) - min(ys)) * frac / 2
    w = copy.copy(pg)
    w.segs = [s for s in pg.segs if abs(s.x0 - cx) <= hw and abs(s.y0 - cy) <= hh]
    w.dots = []
    return w


def _scaled(pg, k):
    import copy
    from .primitives import Seg
    w = copy.copy(pg)
    w.segs = [Seg(s.x0 * k, s.y0 * k, s.x1 * k, s.y1 * k, s.kind) for s in pg.segs]
    w.dots = [(x * k, y * k) for x, y in pg.dots]
    w.width, w.height = pg.width * k, pg.height * k
    return w


def _best_orientation(pdf, page_no, scale, pg0):
    """Full decode at the given setting; if it reads cleanly we are done (and the decoded words are reused).
    Otherwise score every (rotation, scale) on a central window of the page - the PDF is opened once per
    rotation and rescaled in memory - and re-decode the whole page once at the winner.
    Returns (page, rotate, scale, decoded_words or None)."""
    ok = lambda q: q[0] >= 0.98 and q[1] >= 3
    bq, bw = _quality(pg0)
    if ok(bq):
        return pg0, 0, scale, bw
    best, bscore = (0, scale), -1.0
    for r in (0, 90, 270):
        win = _window(load_page(pdf, page_no - 1, scale, r))
        for k in SCALES:
            q, w = _quality(_scaled(win, k))
            n = len([x for x in w if len(x.glyphs) >= 2])
            sc = (q[0] ** 4) * n                  # many words that decode cleanly = right orientation and scale
            if sc > bscore:
                best, bscore = (r, scale * k), sc
    r, s = best
    return load_page(pdf, page_no - 1, s, r), r, s, None


class Unsupported(Exception):
    pass


def extract(pdf, page_no, full=False, scale=1.0, mode="auto", rotate=0):
    kind = classify(pdf, page_no - 1)
    use = kind.kind if mode == "auto" else mode
    if use in ("raster", "empty"):
        raise Unsupported("page %d: %s. %s" % (page_no, use, kind.note))
    auto = mode == "auto"
    if use == "textlayer" and auto and scale == 1.0:
        s, n = estimate_scale(load_page(pdf, page_no - 1, 1.0, rotate))       # real text: segments are all wire/symbol
        if n >= 20:
            res = _core(load_page(pdf, page_no - 1, s, rotate), "textlayer")
        else:
            res = _textlayer_search(pdf, page_no, rotate)
    else:
        pre = None
        pg = load_page(pdf, page_no - 1, scale, rotate)
        if use == "strokefont" and rotate == 0 and auto:
            pg, rotate, scale, pre = _best_orientation(pdf, page_no, scale, pg)
        res = _core(pg, use, pre)
    return res if full else (res[0], res[1])


def _textlayer_search(pdf, page_no, rotate):
    """Text-layer exports come at any drawing scale; the pipeline's distances assume KiCad's.
    Try scales and keep the one where the most referenced parts get a symbol."""
    best, bscore = None, -1.0
    for k in SCALES:
        pg = load_page(pdf, page_no - 1, k, rotate)
        res = _core(pg, "textlayer")
        refs = {w.text for w in res[2] if NL.REF.match(w.text)}
        pins = sum(len(g.terminals) for g in res[0])
        score = len({g.ref for g in res[0]}) / max(1, len(refs)) + 0.0005 * pins   # parts found, then pins attached
        if score > bscore + 1e-9:
            best, bscore = res, score
    return best


def _core(pg, use, pre=None):
    key = lambda a: tuple(round(v, 2) for v in a)
    if use == "strokefont":
        words = pre
        if words is None:
            words = G.build_words(G.find_glyphs(pg.segs))
            T.decode_all(words, font.load_templates(), G.estimate_text_sizes(words))
        ok = lambda w: T.is_text(w) and "?" not in w.text
        xs = lambda w: len(w.glyphs) == 1 and w.text in "Xx"
        text = [w for w in words if ok(w) and not xs(w)]
        drop = {key(s) for w in words if ok(w) or xs(w) for g in w.glyphs for s in g.segs}
        marks = [((w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2) for w in words if xs(w)]
        segs = [s for s in pg.segs if key((s.x0, s.y0, s.x1, s.y1)) not in drop]
    else:                                              # textlayer: text is already text
        text = text_layer_words(pg)
        marks = []
        segs = list(pg.segs)
    # sheet frame / title block: long lines and everything in the bottom strip (84% of height)
    lim = 0.84 * pg.height
    segs = [s for s in segs if s.length <= 0.36 * max(pg.width, pg.height) and s.y0 < lim and s.y1 < lim]
    topo = TP.Topology(segs, pg.dots, marks)
    comps = NL.assign(topo, text)
    for g in comps:                                  # LED arrow heads are tiny wire bits, not pins
        if g.ref.startswith("D"):
            big = [t for t in g.terminals if max(topo._comp_box(t.comp)[2] - topo._comp_box(t.comp)[0],
                                                 topo._comp_box(t.comp)[3] - topo._comp_box(t.comp)[1]) >= 6]
            if len({t.comp for t in big}) >= 2:
                g.terminals = big
    n = NL.nets(topo, comps, text)
    netnames = set(n)
    topo.used_words |= {id(w) for w in text if w.text in netnames}
    return comps, n, text, topo




def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="pdf2kicad", description="schematic PDF page -> netlist.csv/parts.csv/pins.csv")
    ap.add_argument("pdf"); ap.add_argument("page", type=int); ap.add_argument("outdir")
    ap.add_argument("--mode", choices=["auto", "strokefont", "textlayer"], default="auto", help="force the text source")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply all coordinates (PDF drawn at another scale than KiCad A4)")
    ap.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="content stored sideways: rotate clockwise")
    ap.add_argument("--info", action="store_true", help="only report what kind of PDF page this is")
    a = ap.parse_args(argv[1:])
    pdf, page, outdir = a.pdf, a.page, a.outdir
    try:
        k = classify(pdf, page - 1)
    except (ValueError, RuntimeError, OSError) as e:     # bad page number, unreadable/encrypted PDF
        print("ERROR:", e)
        raise SystemExit(2)
    print("page %d: kind=%s segs=%d words=%d ref-like=%d images=%d rotation=%d %s" % (
        page, k.kind, k.segs, k.words, k.reflike, k.images, k.rotation, k.note))
    if a.info:
        return
    import os
    os.makedirs(outdir, exist_ok=True)
    try:
        comps, nets, text, topo = extract(pdf, page, full=True, scale=a.scale, mode=a.mode, rotate=a.rotate)
    except Unsupported as e:
        print("UNSUPPORTED:", e)
        raise SystemExit(3)
    pins, claimed = NL.pin_tokens(topo, comps, text)
    vals = NL.values(topo, comps, text, claimed)
    by = {}
    for g in comps:
        by.setdefault(g.ref, []).extend(g.terminals)
    for ref, ts in by.items():                 # 2-pin parts with no printed pin numbers -> 1/2 by position
        ts = list({t.comp: t for t in ts}.values())
        if len(ts) == 2 and (not any(id(t) in pins for t in ts) or re.match(r'[RLFC]\d', ref)):
            for n, t in enumerate(sorted(ts, key=lambda t: (t.y, t.x)), 1):
                pins[id(t)] = str(n)
    symbol = {}
    LEDV = re.compile(r"red|green|yellow|blue|white|orange|led", re.I)
    for g in comps:
        ts = list({t.comp: t for t in by.get(g.ref, [])}.values())   # one pin per net wire
        if len(ts) != 2 or g.ref in symbol:
            continue
        pre = re.match(r"[A-Z]+", g.ref).group()
        if pre == "D":
            diag = [sg for sg in g.segs if sg.x0 != sg.x1 and sg.y0 != sg.y1]
            ends = {}
            for sg in diag:
                for q in ((round(sg.x0, 1), round(sg.y0, 1)), (round(sg.x1, 1), round(sg.y1, 1))):
                    ends[q] = ends.get(q, 0) + 1
            apex = [q for q, n in ends.items() if n >= 2]
            if apex:                                   # triangle apex points at the cathode bar
                ax, ay = apex[0]
                k = min(ts, key=lambda t: (t.x - ax) ** 2 + (t.y - ay) ** 2)
                a = [t for t in ts if t is not k][0]
                pins[id(k)], pins[id(a)] = "K", "A"
                symbol[g.ref] = "LED" if LEDV.search(vals.get(g.ref, "")) else "D"
        elif pre == "C":
            plus = [w for w in text if w.text == "+" and NL._near(w, g, 0) < 8]
            for t in ts:                       # a '+' already taken as pin token counts too
                if pins.get(id(t)) == "+":
                    pins.pop(id(t)); plus = [type("W", (), dict(x0=t.x, x1=t.x, y0=t.y, y1=t.y))()]
            if plus:
                k = min(ts, key=lambda t: (t.x - (plus[0].x0 + plus[0].x1) / 2) ** 2 + (t.y - (plus[0].y0 + plus[0].y1) / 2) ** 2)
                o = [t for t in ts if t is not k][0]
                pins[id(k)], pins[id(o)] = "+", "-"
                symbol[g.ref] = "CP"
            else:
                symbol[g.ref] = "C"
        elif pre in ("R", "L", "F"):
            symbol[g.ref] = {"R": "R", "L": "L", "F": "FUSE"}[pre]
    seen = set()
    with open(os.path.join(outdir, "netlist.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["net", "ref", "pin"])
        for nm, lst in sorted(nets.items()):
            for ref, t in lst:
                pin = pins.get(id(t), "%.0f_%.0f" % (t.x, t.y))
                if (ref, pin) in seen:
                    pin += "_" + str(len(seen))
                seen.add((ref, pin)); w.writerow([nm, ref, pin])
    with open(os.path.join(outdir, "parts.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ref", "value", "symbol", "block"])
        for r in sorted({g.ref for g in comps}):
            w.writerow([r, vals.get(r, ""), symbol.get(r, ""), "A"])
    with open(os.path.join(outdir, "pins.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ref", "pin", "name", "side"])
        for g in comps:
            if (g.x1 - g.x0) < 20 or (g.y1 - g.y0) < 20 or len({t.comp for t in g.terminals}) < 3:
                continue
            for t in g.terminals:
                pin = pins.get(id(t), "%.0f_%.0f" % (t.x, t.y))
                # name = nearest word just inside the body, along the pin axis
                ix, iy = t.x - t.dx * 6, t.y - t.dy * 6
                cand = [x for x in text if x.text != pin and not NL.REF.match(x.text) and g.x0 - 1 <= x.x0 and x.x1 <= g.x1 + 1 and g.y0 - 1 <= x.y0 and x.y1 <= g.y1 + 1]
                nm = min(cand, key=lambda x: (abs((x.x0 + x.x1) / 2 - ix) + abs((x.y0 + x.y1) / 2 - iy)), default=None)
                side = ("right" if t.dx > 0.5 else "left" if t.dx < -0.5 else "bottom" if t.dy > 0.5 else "top")
                w.writerow([g.ref, pin, nm.text if nm else "", side])
    print("%d components, %d nets -> %s" % (len({g.ref for g in comps}), len(nets), outdir))


def cli():
    main(sys.argv)


if __name__ == "__main__":
    cli()
