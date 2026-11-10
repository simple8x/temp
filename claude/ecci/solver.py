"""Topology solver (design v2 s5.2, with the v2.1 Phase-3 re-solve condition).

Input: an abstract `ConnGraph` (objects, candidate edges with premise records, ambiguity
sites, constraints). Output: `SolveResult` (edge statuses, site results, nets, quarantines).

Properties (each has tests):
  I4  a union is never committed before constraints were checked on the merged net.
  I5  the result does not depend on the order of objects/edges/sites (canonical digest).
  I6  adding constraints/negative evidence never RAISES the status of a candidate edge.
      (For pin-pair statuses it holds on graphs without sites. Resolving a site by
      constraint entailment can legitimately turn UNKNOWN into INFERRED: see docs/M3_STATUS.md.)
  I7  no edge is VERIFIED unless classify_edge says so; FOURWAY sites never create VERIFIED edges.

Phases:
  1  VERIFIED edges only. Nets with a violation are QUARANTINED whole (nobody chooses an edge to drop).
  2  Add INFERRED edges and committed site links; reject the WHOLE witness set of every violation.
  3  Resolve ambiguity sites per interaction cluster by exhaustive enumeration against the
     constraints; a unique consistent option is committed as INFERRED, never VERIFIED. After each
     commit round Phase 2 runs again on the augmented edge set; a commit that creates a violation
     is rolled back (site -> CONFLICT), and the loop repeats until nothing changes.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import defaultdict
from dataclasses import dataclass, field

from .constraints import Constraints, check_constraints, warnings_for, witnesses
from .status import CONFLICT, INFERRED, UNKNOWN, VERIFIED, Edge, RoleRef, classify_edge

K_MAX_DEFAULT = 5


class GraphError(ValueError):
    pass


@dataclass(frozen=True)
class Obj:
    id: str
    kind: str                         # "PIN" | "WIRE" | "DOT" | "LABEL" | "UNRESOLVED"
    attach: str | None = None         # structural membership (UnresolvedTerminal -> its wire), not a claim


@dataclass(frozen=True)
class Option:
    name: str
    links: frozenset                  # frozenset of (a, b) with a < b


@dataclass(frozen=True)
class Site:
    id: str
    kind: str                         # "FOURWAY" | "MULTI"
    arms: tuple                       # ((label, obj id), ...)
    options: tuple                    # (Option, ...)  the finite domain (ASSUMPTION: exhaustive)
    fixed: str | None = None          # option name fixed as an assumption (INFERRED)


@dataclass
class ConnGraph:
    objects: list
    edges: list
    sites: list = field(default_factory=list)
    constraints: Constraints = field(default_factory=Constraints)


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def fourway_site(sid: str, arms: dict[str, str]) -> Site:
    """Domain of a 4-arm point without a dot: all joined, two straight-through pairs,
    two corner pairs, or nothing joined. Duplicates (arms of one object) collapse."""
    n, e, s, w = arms["N"], arms["E"], arms["S"], arms["W"]

    def links(*prs):
        return frozenset(_pair(x, y) for x, y in prs if x != y)

    opts = {
        "ALL4": links((n, e), (n, s), (n, w), (e, s), (e, w), (s, w)),
        "THRU_NS_EW": links((n, s), (e, w)),
        "CORNER_NE_SW": links((n, e), (s, w)),
        "CORNER_NW_SE": links((n, w), (e, s)),
        "NONE": frozenset(),
    }
    seen: dict[frozenset, str] = {}
    for name in sorted(opts):
        seen.setdefault(opts[name], name)
    return Site(sid, "FOURWAY", tuple(sorted(arms.items())),
                tuple(Option(nm, ls) for ls, nm in sorted(seen.items(), key=lambda kv: kv[1])))


@dataclass(frozen=True)
class SiteResult:
    status: str                       # INFERRED (resolved) | UNKNOWN | CONFLICT
    option: str | None
    reason: str


@dataclass(frozen=True)
class Net:
    id: str
    objects: tuple
    status: str                       # "CONFLICT" (quarantined) | "OK"
    closure: str                      # "CLOSED" | "OPEN"
    open_reasons: tuple
    warnings: tuple


@dataclass
class SolveResult:
    edge_status: dict
    sites: dict
    nets: list
    quarantined: list                 # [(objects, violations)]
    warnings: list
    _net_of: dict = field(default_factory=dict, repr=False)
    _vgroup: dict = field(default_factory=dict, repr=False)
    _quar: set = field(default_factory=set, repr=False)
    final_edges: list = field(default_factory=list, repr=False)   # (id, a, b) of the edges that form the nets

    def pair_status(self, p: str, q: str) -> str:
        """VERIFIED / INFERRED = same net with an all-VERIFIED / some-INFERRED path.
        CONFLICT = same quarantined net. Otherwise UNKNOWN. There is no 'different net' claim."""
        if p == q or p not in self._net_of or q not in self._net_of:
            return UNKNOWN
        if self._net_of[p] != self._net_of[q]:
            return UNKNOWN
        if self._net_of[p] in self._quar:
            return CONFLICT
        return VERIFIED if self._vgroup.get(p) == self._vgroup.get(q) and p in self._vgroup else INFERRED

    def to_dict(self) -> dict:
        return {
            "edges": {k: list(v) for k, v in sorted(self.edge_status.items())},
            "sites": {k: [v.status, v.option, v.reason] for k, v in sorted(self.sites.items())},
            "nets": [{"id": n.id, "objects": list(n.objects), "status": n.status, "closure": n.closure,
                      "open_reasons": list(n.open_reasons), "warnings": list(n.warnings)}
                     for n in sorted(self.nets, key=lambda n: n.id)],
            "quarantined": [[sorted(o), [[v.kind, [list(map(str, p)) for p in v.pairs]] for v in vs]]
                            for o, vs in sorted(self.quarantined, key=lambda t: min(t[0]))],
            "warnings": sorted(self.warnings),
        }

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------------------ helpers

def _components(objs: list[str], edges: list) -> dict[str, str]:
    """obj -> representative (the smallest id of its component). Deterministic."""
    parent = {o: o for o in objs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {o: find(o) for o in objs}


def _groups(rep: dict[str, str]) -> dict[str, frozenset]:
    g: dict[str, set] = defaultdict(set)
    for o, r in rep.items():
        g[r].add(o)
    return {r: frozenset(m) for r, m in g.items()}


def _validate(G: ConnGraph) -> None:
    ids = [o.id for o in G.objects]
    if len(set(ids)) != len(ids):
        raise GraphError("duplicate object id")
    known = set(ids)
    eids = [e.id for e in G.edges]
    if len(set(eids)) != len(eids):
        raise GraphError("duplicate edge id")
    for e in G.edges:
        if e.a not in known or e.b not in known:
            raise GraphError(f"edge {e.id} references unknown object")
    for o in G.objects:
        if o.attach is not None and o.attach not in known:
            raise GraphError(f"object {o.id} attaches to unknown object")
    sids = [s.id for s in G.sites]
    if len(set(sids)) != len(sids):
        raise GraphError("duplicate site id")
    for s in G.sites:
        names = [o.name for o in s.options]
        if len(set(names)) != len(names) or not names:
            raise GraphError(f"site {s.id}: bad option list")
        if s.fixed is not None and s.fixed not in names:
            raise GraphError(f"site {s.id}: fixed option not in domain")
        for _, o in s.arms:
            if o not in known:
                raise GraphError(f"site {s.id}: unknown arm object")
        for opt in s.options:
            for a, b in opt.links:
                if a not in known or b not in known:
                    raise GraphError(f"site {s.id}: option link references unknown object")
    for a, b, _ in G.constraints.cannot_link:
        if a not in known or b not in known:
            raise GraphError("cannot_link references unknown object")


def _name_edges(G: ConnGraph) -> list[Edge]:
    """TR6: same CONFIRMED name and scope => INFERRED link (chain over sorted ids). OCR names: none."""
    kind = {o.id: o.kind for o in G.objects}
    by: dict[tuple, list[str]] = defaultdict(list)
    for o, nm in G.constraints.names.items():
        if nm.confirmed and o in kind:
            by[(nm.text, nm.scope)].append(o)
    out = []
    for (text, scope), objs in sorted(by.items()):
        objs = sorted(objs)
        for a, b in zip(objs, objs[1:]):
            out.append(Edge(f"tr6:{scope}:{text}:{a}|{b}", a, b, "TR6",
                            (RoleRef(a, kind[a], "CANDIDATE"), RoleRef(b, kind[b], "CANDIDATE")),
                            coord=f"name:{scope}:{text}"))
    return out


# ------------------------------------------------------------------------------ phase 2

def _phase2(objs, V, S, links, cons):
    """Reject the whole witness set of every violation. Site links are rolled back before
    ordinary INFERRED edges (a site is an entailed guess, an edge is evidence-bearing).
    Returns (active S ids, active link ids, rolled-back link ids)."""
    cur_S = dict(S)
    cur_L = dict(links)
    rolled: set = set()
    for _ in range(len(S) + len(links) + 2):
        edges = list(V) + [(i, a, b) for i, (a, b) in sorted(cur_S.items())] \
            + [(i, a, b) for i, (a, b) in sorted(cur_L.items())]
        rep = _components(objs, edges)
        groups = _groups(rep)
        by_comp: dict[str, list] = defaultdict(list)
        for e in edges:
            by_comp[rep[e[1]]].append(e)
        rejectable = set(cur_S) | set(cur_L)
        bad: set = set()
        for r in sorted(groups):
            if len(groups[r]) < 2:
                continue
            viol = check_constraints(groups[r], cons)
            if not viol:
                continue
            w = witnesses(by_comp[r], viol, rejectable)
            if not w:
                w = {e[0] for e in by_comp[r] if e[0] in rejectable}
            if not w:
                raise AssertionError("violation without rejectable edge (phase 1 should have quarantined it)")
            bad |= w
        if not bad:
            return set(cur_S), set(cur_L), rolled
        bad_links = bad & set(cur_L)
        if bad_links:
            for i in bad_links:
                cur_L.pop(i)
            rolled |= bad_links
            continue
        for i in bad:
            cur_S.pop(i)
    raise AssertionError("phase 2 did not converge")


# ------------------------------------------------------------------------------ solve

def solve(G: ConnGraph, k_max: int = K_MAX_DEFAULT) -> SolveResult:
    _validate(G)
    cons = G.constraints
    objs = sorted(o.id for o in G.objects)
    kind = {o.id: o.kind for o in G.objects}
    edges = list(G.edges) + _name_edges(G)
    classified = {e.id: classify_edge(e) for e in edges}
    emap = {e.id: e for e in edges}
    # structural membership (an unresolved terminal belongs to its wire's net) is NOT an electrical claim: it joins
    # the partition, so the net is reported OPEN, but never the VERIFIED groups that pair_status reads.
    A = sorted((f"attach:{o.id}", o.id, o.attach) for o in G.objects if o.attach)

    V = sorted([(e.id, e.a, e.b) for e in edges if classified[e.id][0] == VERIFIED])
    S0 = {e.id: (e.a, e.b) for e in edges if classified[e.id][0] == INFERRED}

    # ---- phase 1
    rep1 = _components(objs, V + A)
    groups1 = _groups(rep1)
    quarantined: list = []
    Q: set = set()
    qroots: set = set()
    for r in sorted(groups1):
        if len(groups1[r]) < 2:
            # a singleton can still violate C5 only if it shares a net; singletons cannot
            continue
        viol = check_constraints(groups1[r], cons)
        if viol:
            quarantined.append((groups1[r], viol))
            Q |= groups1[r]
            qroots.add(r)

    V_ok = [e for e in V + A if e[1] not in Q]      # quarantined nets take no further part in phases 2-3
    status: dict[str, tuple[str, str]] = {}
    S = {}
    for eid, (a, b) in S0.items():
        if a in Q or b in Q:
            status[eid] = (UNKNOWN, "TOUCHES_CONFLICT_COMPONENT")
        else:
            S[eid] = (a, b)

    # ---- phases 2 and 3 with re-solve
    sites = {s.id: s for s in G.sites}
    site_res: dict[str, SiteResult] = {}
    committed: dict[str, str] = {}
    failed: set = set()
    for sid in sorted(sites):
        s = sites[sid]
        if any(o in Q for _, o in s.arms):
            site_res[sid] = SiteResult(UNKNOWN, None, "TOUCHES_CONFLICT_COMPONENT")
            failed.add(sid)
        elif s.fixed is not None:
            committed[sid] = s.fixed

    def links_of(cm: dict[str, str]) -> tuple[dict, dict]:
        out, owner = {}, {}
        for sid, name in cm.items():
            opt = next(o for o in sites[sid].options if o.name == name)
            for a, b in sorted(opt.links):
                lid = f"site:{sid}:{a}|{b}"
                out[lid] = (a, b)
                owner[lid] = sid
        return out, owner

    active_S: set = set()
    active_L: set = set()
    for _ in range(len(sites) + 3):
        links, owner = links_of(committed)
        active_S, active_L, rolled = _phase2(objs, V_ok, S, links, cons)
        if rolled:
            for lid in rolled:
                sid = owner[lid]
                committed.pop(sid, None)
                failed.add(sid)
                site_res[sid] = SiteResult(CONFLICT, None, "ROLLED_BACK_BY_RESOLVE")
            continue
        # phase 3 against the current partition
        edges_now = list(V) + A + [(i, S[i][0], S[i][1]) for i in sorted(active_S)] \
            + [(i, links[i][0], links[i][1]) for i in sorted(active_L)]
        rep = _components(objs, edges_now)
        groups = _groups(rep)
        todo = [sid for sid in sorted(sites) if sid not in committed and sid not in failed]
        cl_parent = {sid: sid for sid in todo}

        def cfind(x):
            while cl_parent[x] != x:
                cl_parent[x] = cl_parent[cl_parent[x]]
                x = cl_parent[x]
            return x

        first_site_at: dict[str, str] = {}
        for sid in todo:
            for _, o in sites[sid].arms:
                r = rep[o]
                if r in first_site_at:
                    ra, rb = cfind(first_site_at[r]), cfind(sid)
                    if ra != rb:
                        cl_parent[max(ra, rb)] = min(ra, rb)
                else:
                    first_site_at[r] = sid
        clusters: dict[str, list] = defaultdict(list)
        for sid in todo:
            clusters[cfind(sid)].append(sid)
        new: dict[str, str] = {}
        for key in sorted(clusters):
            cl = sorted(clusters[key])
            if len(cl) > k_max:
                for sid in cl:
                    site_res[sid] = SiteResult(UNKNOWN, None, "CLUSTER_TOO_LARGE")
                continue
            doms = [sorted(sites[sid].options, key=lambda o: o.name) for sid in cl]
            consistent = []
            for combo in itertools.product(*doms):
                lk = sorted({l for o in combo for l in o.links})
                par = {r: r for r in groups}

                def f(x, par=par):
                    while par[x] != x:
                        par[x] = par[par[x]]
                        x = par[x]
                    return x

                for a, b in lk:
                    ra, rb = f(rep[a]), f(rep[b])
                    if ra != rb:
                        par[max(ra, rb)] = min(ra, rb)
                mroots: dict[str, set] = defaultdict(set)
                for r in groups:
                    mroots[f(r)].add(r)
                ok = True
                for rs in mroots.values():
                    if len(rs) > 1 and check_constraints(frozenset().union(*(groups[r] for r in rs)), cons):
                        ok = False
                        break
                if ok:
                    consistent.append(combo)
            if not consistent:
                for sid in cl:
                    site_res[sid] = SiteResult(CONFLICT, None, "NO_CONSISTENT_OPTION")
                    failed.add(sid)
            elif len(consistent) == 1:
                for sid, opt in zip(cl, consistent[0]):
                    new[sid] = opt.name
            else:
                for sid in cl:
                    site_res[sid] = SiteResult(UNKNOWN, None, f"AMBIGUOUS_{len(consistent)}_CONSISTENT_ASSIGNMENTS")
        if not new:
            break
        committed.update(new)
    else:
        raise AssertionError("solver did not reach a fixpoint")

    for sid, name in committed.items():
        site_res[sid] = SiteResult(INFERRED, name, "FIXED_ASSUMPTION" if sites[sid].fixed == name
                                   else "ENTAILED_BY_CONSTRAINTS")

    # ---- final statuses
    for eid, (a, b) in S.items():
        status[eid] = (INFERRED, classified[eid][1]) if eid in active_S else (UNKNOWN, "REJECTED_BY_CONSTRAINT")
    vrep = _components(objs, V)
    for e in edges:
        if classified[e.id][0] == VERIFIED:
            status[e.id] = (CONFLICT, "QUARANTINED") if e.a in Q else (VERIFIED, classified[e.id][1])
        elif classified[e.id][0] == UNKNOWN:
            status[e.id] = classified[e.id]
    links, _ = links_of(committed)
    final_edges = list(V) + A + [(i, S[i][0], S[i][1]) for i in sorted(active_S)] \
        + [(i, links[i][0], links[i][1]) for i in sorted(active_L)]
    frep = _components(objs, final_edges)
    fgroups = _groups(frep)
    unknown_inc: dict[str, list] = defaultdict(list)
    for e in edges:
        if status[e.id][0] == UNKNOWN:
            unknown_inc[e.a].append(e.id)
            unknown_inc[e.b].append(e.id)
    open_sites: dict[str, list] = defaultdict(list)
    for sid, sr in site_res.items():
        if sr.status != INFERRED:
            for _, o in sites[sid].arms:
                open_sites[o].append(sid)
    nets: list[Net] = []
    warns: list[str] = []
    for r in sorted(fgroups):
        mem = fgroups[r]
        reasons: list[str] = []
        for o in sorted(mem):
            if kind[o] == "UNRESOLVED":
                reasons.append(f"UNRESOLVED_TERMINAL:{o}")
            for eid in sorted(set(unknown_inc.get(o, ()))):
                reasons.append(f"UNKNOWN_CANDIDATE:{eid}")
            for sid in sorted(set(open_sites.get(o, ()))):
                reasons.append(f"OPEN_SITE:{sid}")
        w = warnings_for(mem, cons)
        warns += w
        nets.append(Net(r, tuple(sorted(mem)), CONFLICT if (mem & Q) else "OK",
                        "OPEN" if reasons else "CLOSED", tuple(sorted(set(reasons))), tuple(w)))
    vg = {}
    for r, mem in _groups(vrep).items():
        if len(mem) > 1 and not (mem & Q):
            for o in mem:
                vg[o] = r
    netq = {frep[o] for o in Q}
    return SolveResult(status, site_res, nets, quarantined, sorted(set(warns)),
                       dict(frep), vg, netq, final_edges)


def audit_result(G: ConnGraph, res: SolveResult) -> list[str]:
    """Structural checks that must hold for every result (C3, C6, I2)."""
    from .status import audit_verified
    problems: list[str] = []
    seen: dict[str, str] = {}
    for n in res.nets:
        for o in n.objects:
            if o in seen:
                problems.append(f"C3: {o} in two nets")
            seen[o] = n.id
    if set(seen) != {o.id for o in G.objects}:
        problems.append("C3: some object is in no net")
    emap = {e.id: e for e in G.edges}
    for eid, (st, _) in res.edge_status.items():
        if st == VERIFIED and eid in emap:
            problems += [f"I2 {eid}: {p}" for p in audit_verified(emap[eid])]
    for sid, sr in res.sites.items():
        if sr.status == INFERRED and sr.option is None:
            problems.append(f"C6: site {sid} resolved without an option")
    return problems
