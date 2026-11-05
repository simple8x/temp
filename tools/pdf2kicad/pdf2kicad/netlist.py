"""Step 5: assign refs/values to symbol groups, name nets, export ref-level connectivity."""
import math, re
from collections import defaultdict

COLOURS = {"Red", "Green", "Yellow", "Blue", "White", "Orange", "RED", "GREEN", "YELLOW", "BLUE", "WHITE"}
REF = re.compile(r"^(RN|SW|[A-Z])\d+[A-Z]?$")


def _near(w, g, margin):
    cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
    dx = max(g.x0 - cx, 0, cx - g.x1)
    dy = max(g.y0 - cy, 0, cy - g.y1)
    return math.hypot(dx, dy)


def _named(g, others):
    best = min(others, key=lambda w: _near(w, g, 0), default=None)
    if best is None or _near(best, g, 0) >= 14:
        return ""
    t = best.text
    return t if (len(t) >= 2 and not t.isdigit()) else ""


PARTNO = re.compile(r"^[A-Z]{2,}[A-Z0-9]*[-.]\d[\w.\-]*$|^SMAJ\d|^[A-Z]{2,}\d{3,}[A-Z]?$")   # HCPL-0600, KF-3.81-10-R, SMAJ30CA, MP1584


def _netlike(t):
    return not PARTNO.match(t) and len(t) >= 2 and t not in COLOURS and not t.isdigit() and not re.fullmatch(r"[\d.]+[kKMmunpRVA]?[FHΩ]?[A-Za-z]{0,2}", t)


def assign(topo, words):
    """words: decoded text words. Returns components [{ref,value,group,terminals}] and nets."""
    isref = lambda t: REF.match(t) and not re.fullmatch(r"[AB]\d{1,2}", t)   # A4/B7 = USB-C pin names
    refs = [w for w in words if isref(w.text)]
    bigs = [g for g in topo.groups if len(g.terminals) >= 6 and (g.x1 - g.x0) > 20 and (g.y1 - g.y0) > 20]
    refs = [w for w in refs if not any(g.x0 < (w.x0 + w.x1) / 2 < g.x1 and g.y0 < (w.y0 + w.y1) / 2 < g.y1 for g in bigs)]  # pin names
    others = [w for w in words if not isref(w.text)]
    comps = []
    # ref -> nearest group that has >=2 attached terminals (a component), each group takes the nearest ref
    cand = [g for g in topo.groups if len(g.terminals) >= 2]
    taken = {}
    used_r, used_g = set(), set()
    multi = lambda t: re.match(r"(U|J|SW|RN|Y|X)\d", t) is not None
    from .assign import linear_sum_assignment
    import numpy as np
    for phase in (0, 1):                  # phase 0: ICs/connectors claim big groups first
        R = [i for i, w in enumerate(refs) if i not in used_r and (phase == 1 or multi(w.text))]
        Gs = [j for j, g in enumerate(cand) if j not in used_g and (phase == 1 or (len(g.terminals) >= 3 and max(g.x1 - g.x0, g.y1 - g.y0) >= 18))]
        if not R or not Gs:
            continue
        BIG = 1e3
        cost = np.array([[(_near(refs[i], cand[j], 0) if _near(refs[i], cand[j], 0) <= 30 else BIG) for j in Gs] for i in R])
        ri, ci = linear_sum_assignment(cost)         # globally best pairing, not nearest-first
        for r_, c_ in zip(ri, ci):
            if cost[r_, c_] >= BIG:
                continue
            i, j = R[r_], Gs[c_]
            used_r.add(i); used_g.add(j)
            g = cand[j]; g.ref = re.sub(r"^(U\d+)[A-H]$", r"\1", refs[i].text); g.kind = "component"
            comps.append(g)
    # leftovers: a part whose pins stop far short of its body (crystals) -> adopt open wire ends near its box
    for i, w in enumerate(refs):
        if i in used_r:
            continue
        best = None
        for g in topo.groups:
            if g.ref or g.terminals or _near(w, g, 0) > 20:
                continue
            ends = [t for t in topo.terminals if t.kind == "open" and
                    max(g.x0 - t.x, 0, t.x - g.x1) ** 2 + max(g.y0 - t.y, 0, t.y - g.y1) ** 2 <= 9.0 ** 2]
            if len(ends) >= 2 and (best is None or len(ends) < len(best[1])):
                best = (g, ends)
        if best:
            g, ends = best
            g.ref, g.kind, g.terminals = w.text, "component", ends
            for t in ends:
                t.kind, t.group = "attach", topo.groups.index(g)
            used_r.add(i); comps.append(g)
    # fold small leftover pieces (diode bars, LED arrows, polarity marks) into the nearest component
    def gap(g, h):
        dx = max(g.x0 - h.x1, h.x0 - g.x1, 0); dy = max(g.y0 - h.y1, h.y0 - g.y1, 0)
        return math.hypot(dx, dy)
    for g in topo.groups:
        if g.ref or len(g.terminals) > 1:
            continue
        near = min(comps, key=lambda c: gap(g, c), default=None)
        if near is not None and gap(g, near) <= 6 and (g.x1 - g.x0) < 14 and (g.y1 - g.y0) < 14 and not _named(g, others):
            near.terminals.extend(g.terminals); g.kind = "merged"
    # single-terminal groups = power / ground / flag symbols; one-to-one nearest net-like word
    syms = [g for g in topo.groups if g.terminals and len({t.comp for t in g.terminals}) == 1 and not g.ref and g.kind != "merged"]
    cands = [w for w in others if _netlike(w.text)]
    pr = sorted((_near(w, g, 0), i, j) for i, w in enumerate(cands) for j, g in enumerate(syms))
    uw, ug = set(), set()
    for d, i, j in pr:
        if d > 14 or i in uw or j in ug:
            continue
        uw.add(i); ug.add(j)
        syms[j].kind, syms[j].name = "netsym", cands[i].text
    topo.used_words = {id(cands[i]) for i in uw} | {id(w) for w in refs}
    return comps


def nets(topo, comps, words):
    """Net = wire component; name from net symbols touching it or text sitting on it."""
    name_of = {}
    for g in topo.groups:
        if g.kind == "netsym" and g.name:
            for t in g.terminals:
                name_of.setdefault(t.comp, g.name)
    # label flags: a small outline wrapped around a word; its tip meets the wire end within a few pt
    flagged = set()
    for w in words:
        if id(w) in getattr(topo, "used_words", ()) or id(w) in getattr(topo, "value_ids", ()) or not _netlike(w.text):
            continue
        cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
        fl = [c for c in topo.wire_comps
              if (lambda b: b[0] - 0.5 <= cx <= b[2] + 0.5 and b[1] - 0.5 <= cy <= b[3] + 0.5 and b[2] - b[0] < 70 and b[3] - b[1] < 70)(topo._comp_box(c))]
        if not fl:
            continue
        fb = topo._comp_box(fl[0])
        for c in fl:
            name_of.setdefault(c, w.text)
        for c in topo.wire_comps:
            if c in fl:
                continue
            for (x, y) in topo._dangling(c):
                dx = max(fb[0] - x, 0, x - fb[2]); dy = max(fb[1] - y, 0, y - fb[3])
                if (dx * dx + dy * dy) ** 0.5 <= 5.5:
                    name_of.setdefault(c, w.text)
        flagged.add(id(w))
        topo.used_words.add(id(w))
    # plain net labels: a net-like word lying on / right above a wire names that wire
    from .topology import pt_seg_dist
    class _P:  # point proxy
        pass
    for w in words:
        if id(w) in getattr(topo, "used_words", ()) or id(w) in getattr(topo, "value_ids", ()) or id(w) in flagged or not _netlike(w.text):
            continue
        cx, cy = (w.x0 + w.x1) / 2, w.y1 + 0.3
        best = None
        for c in topo.wire_comps:
            if c in name_of:
                continue
            for k in topo.comps[c]:
                sg = topo.segs[topo.W[k]]
                cxm = (w.x0 + w.x1) / 2
                if not (min(sg.x0, sg.x1) - 1.0 <= cxm <= max(sg.x0, sg.x1) + 1.0 or abs(sg.x1 - sg.x0) < 1e-6):
                    continue                                   # a label sits along its wire, not beside a pin stub
                d = min(pt_seg_dist(cxm, py, sg)[0] for py in (w.y0 - 0.3, (w.y0 + w.y1) / 2, w.y1 + 0.3))
                if d <= 2.4 and (best is None or d < best[0]):
                    best = (d, c)
        if not best:                      # label stands at the open end of a wire (up to 7pt away, same line)
            wy = (w.y0 + w.y1) / 2
            for c in topo.wire_comps:
                if c in name_of:
                    continue
                for (x, y) in topo._dangling(c):
                    dxe = max(w.x0 - x, 0, x - w.x1)
                    if abs(y - wy) <= 3.0 and dxe <= 7.0 and (best is None or dxe < best[0]):
                        best = (dxe, c)
        if best:
            name_of[best[1]] = w.text
            topo.used_words.add(id(w))
    # global names: same name on different wire comps -> same net
    out = defaultdict(list)
    for g in comps:
        for t in g.terminals:
            nm = name_of.get(t.comp, "N%d" % t.comp)
            out[nm].append((g.ref, t))
    return out


def ref_pairs(netmap):
    """Set of unordered ref pairs sharing a net (ground-truth comparable)."""
    pairs = set()
    for nm, lst in netmap.items():
        rs = sorted({r for r, _ in lst})
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                pairs.add((rs[i], rs[j]))
    return pairs


PINTOK = re.compile(r"^\d{1,2}$|^[A-C]\d{1,2}$|^[+\-]$|^[AK]$")


def pin_tokens(topo, comps, words):
    """pin id per terminal = nearest short pin-number-like word within 8pt, else x/y coordinate."""
    boxes = [g for g in comps if (g.x1 - g.x0) > 20 and (g.y1 - g.y0) > 20]
    inside = lambda w: any(g.x0 < (w.x0 + w.x1) / 2 < g.x1 and g.y0 < (w.y0 + w.y1) / 2 < g.y1 for g in boxes)
    cand = [w for w in words if PINTOK.match(w.text) and id(w) not in getattr(topo, "used_words", ()) and not inside(w)]
    taken, out = set(), {}
    def dist(w, t):                      # number sits beside the stub: measure to stub end and to its middle
        cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
        return min(math.hypot(cx - t.x, cy - t.y), math.hypot(cx - (t.x + t.dx * 5), cy - (t.y + t.dy * 5)) + 1.5)
    multi = [g for g in comps if len({t.comp for t in g.terminals}) >= 3]     # 2-pin parts: their numbers are not printed, nearby digits are values
    pr = sorted((dist(w, t), i, id(t), t) for g in multi for t in g.terminals for i, w in enumerate(cand))
    for d, i, tid, t in pr:
        if d > 12 or i in taken or tid in out:
            continue
        taken.add(i); out[tid] = cand[i].text
    return out, {id(cand[i]) for i in taken}


def values(topo, comps, words, claimed):
    topo.value_ids = getattr(topo, 'value_ids', set())
    """value = nearest unclaimed non-ref word to each component (>=2 chars)."""
    netnames = {g.name for g in topo.groups if g.name}
    def inside_body(w):          # pin names / internal captions of a big symbol are not its value
        cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
        return any((g.x1 - g.x0) > 20 and (g.y1 - g.y0) > 20 and len(g.terminals) >= 3 and g.x0 < cx < g.x1 and g.y0 < cy < g.y1 for g in comps)
    cand = [w for w in words if id(w) not in claimed and id(w) not in getattr(topo, "used_words", ())
            and (len(w.text) >= 2 or w.text.isdigit()) and not REF.match(w.text) and w.text not in netnames and not inside_body(w)]
    out = {}
    SHAPE = {   # what a value looks like for the part type; a matching word is strongly preferred
        "R": r"\d+(\.\d+)?[kKMGR]?\d*|\d+(\.\d+)?\s?[kKM]?[Ωo]?",
        "C": r"\d+(\.\d+)?\s?[pnuμmµ]?F|\d+[pnuμµ]\d*",
        "L": r"\d+(\.\d+)?\s?[nuμµm]?H",
        "J": r"(?=.*\d)[\w.+\-]{4,}", "U": r"(?=.*\d)[\w.+\-]{3,}", "Q": r"(?=.*\d)[\w.+\-]{3,}",
        "D": r"(?=.*\d)[\w.+\-]{3,}|[A-Z]{3,}",       # diode part number, or LED colour written in capitals   # part numbers, not pin captions
    }
    def pen(w, g):
        rx = SHAPE.get(re.match(r"[A-Z]+", g.ref).group()) if g.ref else None
        if rx is None:
            return 0.0
        return 0.0 if re.fullmatch(rx, w.text) else 40.0          # wrong shape = effectively far away
    from .assign import linear_sum_assignment
    import numpy as np
    if not cand or not comps:
        return out
    BIG = 1e3
    cost = np.array([[(lambda d: d if d <= 28 else BIG)(_near(w, g, 0) + pen(w, g)) for g in comps] for w in cand])
    wi, gi = linear_sum_assignment(cost)             # global pairing: neighbours cannot steal each other's value
    for i, j in zip(wi, gi):
        if cost[i, j] < BIG and comps[j].ref not in out:
            w0 = cand[i]
            parts, cur = [w0], w0
            while True:                    # multi-word value on one line: 'SMD 3x6', 'USB type C Receptacle'
                nxt = [x for x in words if x is not cur and id(x) not in topo.value_ids and abs((x.y0 + x.y1) / 2 - (cur.y0 + cur.y1) / 2) < 1.2
                       and 0 <= x.x0 - cur.x1 <= 6.5 and not REF.match(x.text) and x.text not in netnames and id(x) not in getattr(topo, "used_words", ())
                       and x not in parts]
                if not nxt:
                    break
                cur = min(nxt, key=lambda x: x.x0)
                parts.append(cur)
            out[comps[j].ref] = " ".join(x.text for x in parts)
            topo.value_ids.update(id(x) for x in parts)
    return out
