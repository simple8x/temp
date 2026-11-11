"""Constraints C1, C4, C5 and the witness sets used to reject INFERRED edges (design v2 s5).

C3 (a pin belongs to exactly one net) and C6 (one option per site) are structural in the
solver (a partition; one option per site) and are asserted there, not re-checked here.
C2 is rule TR6 (a link, see solver). Names from OCR never create a conflict (TC-C6).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

PAIR_CAP = 200   # above this many violating pairs the witness set falls back to a safe superset


@dataclass(frozen=True)
class Name:
    text: str
    scope: str = "sheet"
    confirmed: bool = True      # False: OCR-derived, may only warn, never conflict or link


@dataclass(frozen=True)
class Constraints:
    names: dict = field(default_factory=dict)            # obj id -> Name
    cannot_link: tuple = ()                               # ((a, b, reason), ...)   C4 and similar
    no_connect: frozenset = frozenset()                   # pin ids carrying a CONFIRMED NC marker (C5)


@dataclass(frozen=True)
class Violation:
    kind: str                                             # "C1" | "C4" | "C5"
    pairs: tuple                                          # ((a, b), ...) objects that must not share a net
    detail: str = ""


def check_constraints(members: frozenset, cons: Constraints) -> list[Violation]:
    """Violations inside ONE candidate net (a set of object ids). Deterministic order."""
    out: list[Violation] = []
    by_name: dict[str, list[str]] = defaultdict(list)
    for o in sorted(members):
        nm = cons.names.get(o)
        if nm is not None and nm.confirmed:
            by_name[nm.text].append(o)
    if len(by_name) > 1:
        texts = sorted(by_name)
        pairs = tuple((a, b) for i, t1 in enumerate(texts) for t2 in texts[i + 1:]
                      for a in by_name[t1] for b in by_name[t2])
        out.append(Violation("C1", pairs, "different confirmed net names: " + ",".join(texts)))
    for a, b, why in sorted(cons.cannot_link):
        if a in members and b in members:
            out.append(Violation("C4", ((a, b),), why))
    if len(members) > 1:
        for p in sorted(cons.no_connect):
            if p in members:
                out.append(Violation("C5", ((p, None),), "pin with NC marker shares a net"))
    return out


def warnings_for(members: frozenset, cons: Constraints) -> list[str]:
    """Non-fatal findings: OCR names that disagree (TC-C6)."""
    texts = sorted({cons.names[o].text for o in members if o in cons.names and not cons.names[o].confirmed})
    conf = sorted({cons.names[o].text for o in members if o in cons.names and cons.names[o].confirmed})
    allt = sorted(set(texts) | set(conf))
    if texts and len(allt) > 1:
        return ["OCR_NAME_DISAGREES:" + "|".join(allt)]
    return []


def block_edges_with(comp_edges: list, a: str, b: str) -> set:
    """Ids of edges lying on SOME simple a-b path.

    Add a virtual edge a-b; an edge lies on a simple a-b path iff it is in the same
    biconnected block as the virtual edge. Iterative Tarjan (no recursion limit)."""
    virt = "\0virtual"
    adj: dict[str, list] = defaultdict(list)
    for eid, u, v in list(comp_edges) + [(virt, a, b)]:
        if u == v:
            continue
        adj[u].append((v, eid))
        adj[v].append((u, eid))
    if a not in adj:
        return set()
    disc = {a: 0}
    low = {a: 0}
    t = 1
    parent_edge: dict[str, object] = {a: None}
    its = {a: iter(adj[a])}
    estack: list = []
    dfs = [a]
    blocks: list[set] = []
    while dfs:
        u = dfs[-1]
        advanced = False
        for v, eid in its[u]:
            if eid == parent_edge[u]:
                continue
            if v not in disc:
                parent_edge[v] = eid
                disc[v] = low[v] = t
                t += 1
                estack.append(eid)
                its[v] = iter(adj[v])
                dfs.append(v)
                advanced = True
                break
            if disc[v] < disc[u]:
                estack.append(eid)
                low[u] = min(low[u], disc[v])
        if advanced:
            continue
        dfs.pop()
        if dfs:
            p = dfs[-1]
            low[p] = min(low[p], low[u])
            if low[u] >= disc[p]:
                blk: set = set()
                while True:
                    e = estack.pop()
                    blk.add(e)
                    if e == parent_edge[u]:
                        break
                blocks.append(blk)
    for blk in blocks:
        if virt in blk:
            return blk - {virt}
    return set()


def witnesses(comp_edges: list, violations: list[Violation], rejectable: set) -> set:
    """Rejectable (INFERRED / site-link) edge ids that lie on a path proving a violation.

    The WHOLE set is returned: when two INFERRED edges jointly cause a violation both are
    witnesses and both are rejected; no edge is preferred (order independence, TC-C4)."""
    npairs = sum(len(v.pairs) for v in violations)
    all_rej = {eid for eid, _, _ in comp_edges if eid in rejectable}
    if npairs > PAIR_CAP:
        return all_rej
    out: set = set()
    for v in violations:
        for a, b in v.pairs:
            if b is None:                                   # C5: any edge incident to the pin
                out |= {eid for eid, x, y in comp_edges if (x == a or y == a) and eid in rejectable}
            else:
                out |= block_edges_with(comp_edges, a, b) & rejectable
    return out
