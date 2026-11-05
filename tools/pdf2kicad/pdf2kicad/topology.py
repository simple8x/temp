"""Step 4: wires, symbol bodies and pin attachment points.

Model of a KiCad plot (this is what makes the extraction deterministic):
* a wire is a straight horizontal/vertical segment; wires connect where end points meet or where
  a junction dot sits;
* a wire that ends on the *middle* of another segment without a dot is NOT connected - this is how
  pins land on symbol bodies (rectangles, capacitor plates, diode bars, ground bars ...);
* so the dangling end points of the wire graph are exactly the pin attachment points.
"""
from collections import defaultdict
from dataclasses import dataclass, field
import math

END_TOL = 0.4           # end points closer than this are the same point, pt
DOT_RADIUS = 1.0        # a junction dot joins every wire passing this close to its centre
TOUCH = 0.7             # a dangling end touches a body when closer than this
REACH = 4.2             # pin end may stop this far short of the body, pt
GROUP_GAP = 4.6         # body pieces closer than this belong to one symbol


def pt_seg_dist(px, py, s):
    dx, dy = s.x1 - s.x0, s.y1 - s.y0
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - s.x0) * dx + (py - s.y0) * dy) / L))
    return math.hypot(px - (s.x0 + t * dx), py - (s.y0 + t * dy)), t


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


class Grid:
    """Tiny spatial hash over segments."""

    def __init__(self, cell=6.0):
        self.cell = cell
        self.g = defaultdict(list)

    def add(self, idx, s, pad=0.0):
        c = self.cell
        for cx in range(int((min(s.x0, s.x1) - pad) // c), int((max(s.x0, s.x1) + pad) // c) + 1):
            for cy in range(int((min(s.y0, s.y1) - pad) // c), int((max(s.y0, s.y1) + pad) // c) + 1):
                self.g[(cx, cy)].append(idx)

    def near(self, x, y, r):
        c = self.cell
        out = set()
        for cx in range(int((x - r) // c), int((x + r) // c) + 1):
            for cy in range(int((y - r) // c), int((y + r) // c) + 1):
                out.update(self.g.get((cx, cy), ()))
        return out


@dataclass
class Terminal:
    x: float
    y: float
    comp: int                     # wire component the terminal belongs to
    kind: str = "open"            # attach | nc | open
    body: int = -1                # index into Topology.body_segs it touches
    group: int = -1
    dx: float = 0.0               # direction the wire leaves the body (unit vector)
    dy: float = 0.0


@dataclass
class Group:
    segs: list
    terminals: list = field(default_factory=list)
    x0: float = 0
    y0: float = 0
    x1: float = 0
    y1: float = 0
    ref: str = ""
    kind: str = ""                # component | netsym | orphan
    name: str = ""

    def box(self):
        xs = [v for s in self.segs for v in (s.x0, s.x1)]
        ys = [v for s in self.segs for v in (s.y0, s.y1)]
        self.x0, self.x1, self.y0, self.y1 = min(xs), max(xs), min(ys), max(ys)


class Topology:
    def __init__(self, segs, dots, mark_points=()):
        seen, uniq = set(), []
        for sg in segs:                      # KiCad plots pin lines twice: drop exact duplicates
            a, b = (round(sg.x0, 1), round(sg.y0, 1)), (round(sg.x1, 1), round(sg.y1, 1))
            k = (sg.kind, min(a, b), max(a, b))
            if k not in seen:
                seen.add(k)
                uniq.append(sg)
        segs = uniq
        self._mark_symbol_internals(segs)
        self.segs = segs
        self.dots = dots
        self.marks = list(mark_points)           # centres of no-connect crosses
        self.wire_idx = [i for i, s in enumerate(segs) if s.kind == "line" and (s.is_h or s.is_v) and s.length >= 0.8]
        self._build()

    @staticmethod
    def _mark_symbol_internals(segs):
        """H/V pieces lying wholly inside a cluster of diagonals (TVS / diode drawings) are body, not wire."""
        from .glyphs import seg_dist
        diag = [sg for sg in segs if sg.kind == "line" and not (sg.is_h or sg.is_v)]
        if len(diag) < 2:
            return
        uf = UF(len(diag))
        for i in range(len(diag)):
            for j in range(i + 1, len(diag)):
                a, b = diag[i], diag[j]
                if min(a.x0, a.x1) - 0.5 <= max(b.x0, b.x1) and min(b.x0, b.x1) - 0.5 <= max(a.x0, a.x1) \
                        and min(a.y0, a.y1) - 0.5 <= max(b.y0, b.y1) and min(b.y0, b.y1) - 0.5 <= max(a.y0, a.y1) \
                        and seg_dist((a.x0, a.y0, a.x1, a.y1), (b.x0, b.y0, b.x1, b.y1)) <= 0.5:
                    uf.union(i, j)
        cl = defaultdict(list)
        for i, sg in enumerate(diag):
            cl[uf.find(i)].append(sg)
        boxes = []
        for lst in cl.values():
            if len(lst) >= 2:
                xs = [v for sg in lst for v in (sg.x0, sg.x1)]
                ys = [v for sg in lst for v in (sg.y0, sg.y1)]
                boxes.append((min(xs), min(ys), max(xs), max(ys)))
        for sg in segs:
            if sg.kind == "line" and (sg.is_h or sg.is_v):
                for (x0, y0, x1, y1) in boxes:
                    if min(sg.x0, sg.x1) >= x0 - 0.1 and max(sg.x0, sg.x1) <= x1 + 0.1 \
                            and min(sg.y0, sg.y1) >= y0 - 0.1 and max(sg.y0, sg.y1) <= y1 + 0.1 \
                            and sg.length < (x1 - x0 + y1 - y0):
                        sg.kind = "inner"
                        break

    # ----------------------------------------------------------------------------- wires
    def _build(self):
        W = self.wire_idx
        segs = self.segs
        n = len(W)
        uf = UF(n)
        grid = Grid(8.0)
        for k, i in enumerate(W):
            grid.add(k, segs[i], END_TOL)
        wset = set(W)
        self.vertices = [(x, y) for i, sg in enumerate(segs) if i not in wset for (x, y) in ((sg.x0, sg.y0), (sg.x1, sg.y1))]
        vg = defaultdict(list)
        for (x, y) in self.vertices:
            vg[(int(x // 4), int(y // 4))].append((x, y))

        def at_vertex(x, y):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (vx, vy) in vg.get((int(x // 4) + dx, int(y // 4) + dy), ()):
                        if math.hypot(vx - x, vy - y) <= END_TOL:
                            return True
            return False
        self.at_vertex = at_vertex
        ends = defaultdict(list)                  # rounded end point -> [(k, which)]
        for k, i in enumerate(W):
            s = segs[i]
            for which, (x, y) in enumerate(((s.x0, s.y0), (s.x1, s.y1))):
                ends[(round(x / END_TOL), round(y / END_TOL))].append((k, which, x, y))
        for (bx, by), lst in list(ends.items()):
            # neighbouring buckets too, to be tolerant at bucket borders
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (dx or dy) and (bx + dx, by + dy) in ends:
                        for k1, w1, x1, y1 in lst:
                            for k2, w2, x2, y2 in ends[(bx + dx, by + dy)]:
                                if k1 != k2 and math.hypot(x1 - x2, y1 - y2) <= END_TOL and not at_vertex(x1, y1):
                                    uf.union(k1, k2)
            for a in range(len(lst)):
                for b in range(a + 1, len(lst)):
                    if lst[a][0] != lst[b][0] and not at_vertex(lst[a][2], lst[a][3]):
                        uf.union(lst[a][0], lst[b][0])
        for (dx_, dy_) in self.dots:
            hit = [k for k in grid.near(dx_, dy_, DOT_RADIUS) if pt_seg_dist(dx_, dy_, segs[W[k]])[0] <= DOT_RADIUS]
            for k in hit[1:]:
                uf.union(hit[0], k)
        comps = defaultdict(list)
        for k in range(n):
            comps[uf.find(k)].append(k)
        self.W = W
        self.comp_of = {}
        self.comps = []
        for cid, ks in enumerate(comps.values()):
            self.comps.append(ks)
            for k in ks:
                self.comp_of[k] = cid
        self.wgrid = grid
        self._classify_bodies()
        self._terminals()
        self._groups()

    # ------------------------------------------------------------------- body detection
    def _comp_box(self, cid):
        xs = [v for k in self.comps[cid] for v in (self.segs[self.W[k]].x0, self.segs[self.W[k]].x1)]
        ys = [v for k in self.comps[cid] for v in (self.segs[self.W[k]].y0, self.segs[self.W[k]].y1)]
        return min(xs), min(ys), max(xs), max(ys)

    def _dangling(self, cid):
        """End points of wire component cid that nothing else in the component meets."""
        cnt = defaultdict(list)
        for k in self.comps[cid]:
            s = self.segs[self.W[k]]
            for (x, y) in ((s.x0, s.y0), (s.x1, s.y1)):
                cnt[(round(x / END_TOL), round(y / END_TOL))].append((x, y))
        out = []
        for key, lst in cnt.items():
            near = list(lst)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (dx or dy) and (key[0] + dx, key[1] + dy) in cnt:
                        for p in cnt[(key[0] + dx, key[1] + dy)]:
                            if math.hypot(p[0] - lst[0][0], p[1] - lst[0][1]) <= END_TOL:
                                near.append(p)
            if len(near) == 1:
                out.append(lst[0])
        return out

    def _dir(self, cid, x, y):
        for k in self.comps[cid]:
            sg = self.segs[self.W[k]]
            for (ex, ey, ox, oy) in ((sg.x0, sg.y0, sg.x1, sg.y1), (sg.x1, sg.y1, sg.x0, sg.y0)):
                if math.hypot(ex - x, ey - y) <= END_TOL:
                    L = math.hypot(ox - ex, oy - ey) or 1
                    return (ox - ex) / L, (oy - ey) / L
        return 0.0, 0.0

    def _classify_bodies(self):
        segs, W = self.segs, self.W
        is_body = set()
        # (a) closed loops: every end point shared by exactly two segments
        for cid, ks in enumerate(self.comps):
            if len(ks) >= 3 and not self._dangling(cid):
                is_body.add(cid)
        # (b) isolated single segments that other wires end on, in the middle: plates, bars
        dang = {cid: self._dangling(cid) for cid in range(len(self.comps))}
        for cid, ks in enumerate(self.comps):
            if cid in is_body or len(ks) != 1:
                continue
            s = segs[W[ks[0]]]
            for oc, pts in dang.items():
                if oc == cid or oc in is_body and len(self.comps[oc]) >= 3:
                    continue
                for (x, y) in pts:
                    d, t = pt_seg_dist(x, y, s)
                    L = s.length
                    if d <= TOUCH and 0.8 <= t * L <= L - 0.8:
                        is_body.add(cid)
                        break
                    if L <= 24:                       # plate drawn REACH short of the pin end
                        dx, dy = self._dir(oc, x, y)
                        st = 0.4
                        while st <= REACH:
                            d2, t2 = pt_seg_dist(x - dx * st, y - dy * st, s)
                            if d2 <= 0.35 and 0.8 <= t2 * L <= L - 0.8:
                                is_body.add(cid)
                                break
                            st += 0.4
                        if cid in is_body:
                            break
                if cid in is_body:
                    break
        # (b2) tiny pieces whose every end sits on a body vertex are part of a symbol drawing
        for cid, ks in enumerate(self.comps):
            if cid in is_body or len(ks) > 3:
                continue
            x0, y0, x1, y1 = self._comp_box(cid)
            if x1 - x0 < 10 and y1 - y0 < 10:
                pts = self._dangling(cid)
                if pts and all(self.at_vertex(x, y) for x, y in pts) and not any(
                        self.at_vertex(x, y) is False for x, y in pts):
                    is_body.add(cid)
        # (c) wire pieces lying inside a box loop are symbol internals
        loops = [self._comp_box(c) for c in is_body if len(self.comps[c]) >= 3]
        inner = set()
        for cid, ks in enumerate(self.comps):
            if cid in is_body:
                continue
            b = self._comp_box(cid)
            for L in loops:
                if b[0] >= L[0] + 0.3 and b[2] <= L[2] - 0.3 and b[1] >= L[1] + 0.3 and b[3] <= L[3] - 0.3:
                    inner.add(cid)
                    break
        self.body_comps = is_body
        self.inner_comps = inner
        self.wire_comps = [c for c in range(len(self.comps)) if c not in is_body and c not in inner]

    # ----------------------------------------------------------------------- terminals
    def _terminals(self):
        segs, W = self.segs, self.W
        # every segment that can be a body: all non-wire geometry + body/inner wire pieces
        self.body_segs = []
        wire_set = set()
        for c in self.wire_comps:
            for k in self.comps[c]:
                wire_set.add(W[k])
        for i, s in enumerate(segs):
            if i not in wire_set:
                self.body_segs.append(s)
        bg = Grid(8.0)
        for j, s in enumerate(self.body_segs):
            bg.add(j, s, TOUCH)
        self.bgrid = bg
        terms = []
        for cid in self.wire_comps:
            for (x, y) in self._dangling(cid):
                best, bd = -1, TOUCH
                for j in bg.near(x, y, TOUCH):
                    d, _ = pt_seg_dist(x, y, self.body_segs[j])
                    if d < bd:
                        best, bd = j, d
                t = Terminal(x, y, cid)
                # direction the wire runs from this end (into the wire)
                for k in self.comps[cid]:
                    s = segs[W[k]]
                    for (ex, ey, ox, oy) in ((s.x0, s.y0, s.x1, s.y1), (s.x1, s.y1, s.x0, s.y0)):
                        if math.hypot(ex - x, ey - y) <= END_TOL:
                            L = math.hypot(ox - ex, oy - ey) or 1
                            t.dx, t.dy = (ox - ex) / L, (oy - ey) / L
                if best < 0 and (t.dx or t.dy):
                    # pins are drawn up to REACH short of the body: look along the continuation
                    step = 0.4
                    while step <= REACH and best < 0:
                        qx, qy = x - t.dx * step, y - t.dy * step
                        for j in bg.near(qx, qy, TOUCH):
                            d, _ = pt_seg_dist(qx, qy, self.body_segs[j])
                            if d < TOUCH:
                                best = j
                                break
                        step += 0.4
                if best >= 0:
                    t.kind, t.body = "attach", best
                elif any(math.hypot(x - mx, y - my) <= 1.6 for mx, my in self.marks):
                    t.kind = "nc"
                terms.append(t)
        self.terminals = terms

    # ------------------------------------------------------------------------- symbols
    def _groups(self):
        segs = self.body_segs
        n = len(segs)
        uf = UF(n)
        g = Grid(8.0)
        for j, s in enumerate(segs):
            g.add(j, s, GROUP_GAP)
        from .glyphs import seg_dist
        seen = set()
        for cell in g.g.values():
            for a in range(len(cell)):
                for b in range(a + 1, len(cell)):
                    i, j = cell[a], cell[b]
                    if (i, j) in seen:
                        continue
                    seen.add((i, j))
                    if uf.find(i) != uf.find(j):
                        si, sj = segs[i], segs[j]
                        if seg_dist((si.x0, si.y0, si.x1, si.y1), (sj.x0, sj.y0, sj.x1, sj.y1)) <= GROUP_GAP:
                            uf.union(i, j)
        members = defaultdict(list)
        for j in range(n):
            members[uf.find(j)].append(j)
        self.group_of_seg = {}
        self.groups = []
        for root, js in members.items():
            gi = len(self.groups)
            grp = Group([segs[j] for j in js])
            grp.box()
            self.groups.append(grp)
            for j in js:
                self.group_of_seg[j] = gi
        for t in self.terminals:
            if t.kind == "attach":
                t.group = self.group_of_seg[t.body]
                self.groups[t.group].terminals.append(t)
