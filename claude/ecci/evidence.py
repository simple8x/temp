"""Evidence provenance, source independence and role-confirmation guards (schema level).

Added for ECCI v2.1 conditions 1 and 2:

1. Two sources are NOT independent merely because they use different methods. They are
   independent only if the information inputs behind them (lineage tokens, closed over
   everything they were derived from) are disjoint.
2. A role (wire, pin, ...) can never be CONFIRMED using evidence that itself depends on a
   connectivity claim (circular reasoning), nor using RASTER/OCR-only evidence.

M1 implements only these pure functions and their tests. No connectivity is inferred here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from .ir import SRC_OCR, SRC_RASTER, SOURCES

ROLE_CONFIRMED = "CONFIRMED"
ROLE_CANDIDATE = "CANDIDATE"
ROLE_UNKNOWN = "UNKNOWN"

# Sources that may never contribute to a CONFIRMED role (invariant I3).
WEAK_SOURCES = frozenset({SRC_RASTER, SRC_OCR})


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str                                   # e.g. "STYLE_LAYER", "TERMINAL_TOUCH", "TEMPLATE_MATCH"
    source: str                                 # one of ir.SOURCES
    lineage: frozenset[str]                     # information inputs, e.g. {"prim:12", "lib:kicad@<sha>"}
    derived_from: frozenset[str] = frozenset()  # ids of other evidence items/claims this one used
    uses_connectivity: bool = False             # True if it presupposes any wire/pin connection

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"unknown evidence source {self.source!r}")
        if not self.lineage:
            raise ValueError(f"evidence {self.id!r} has empty lineage: provenance is mandatory")


@dataclass
class EvidenceRegistry:
    items: dict[str, EvidenceItem] = field(default_factory=dict)

    def add(self, item: EvidenceItem) -> EvidenceItem:
        if item.id in self.items:
            raise ValueError(f"duplicate evidence id {item.id!r}")
        self.items[item.id] = item
        return item

    def closure(self, item_id: str) -> set[str]:
        """All ids reachable via derived_from, including item_id. Unknown ids are kept
        (they count as unresolved ancestors) so a dangling reference can never hide a dependency."""
        seen: set[str] = set()
        stack = [item_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            it = self.items.get(cur)
            if it is not None:
                stack.extend(it.derived_from)
        return seen

    def closed_lineage(self, item_id: str) -> set[str]:
        out: set[str] = set()
        for cid in self.closure(item_id):
            it = self.items.get(cid)
            if it is not None:
                out |= it.lineage
        return out

    def depends_on_connectivity(self, item_id: str) -> bool:
        return any(
            (self.items[c].uses_connectivity if c in self.items else False)
            for c in self.closure(item_id)
        )


def independent(a: str, b: str, reg: EvidenceRegistry) -> bool:
    """True only if neither is an ancestor of the other and closed lineages are disjoint."""
    if a == b:
        return False
    ca, cb = reg.closure(a), reg.closure(b)
    # Any shared ancestor (including one of them being an ancestor of the other) makes them
    # dependent, even when that ancestor is unresolved and so contributes no lineage tokens.
    if not ca.isdisjoint(cb):
        return False
    return reg.closed_lineage(a).isdisjoint(reg.closed_lineage(b))


def max_independent_subset(ids: list[str], reg: EvidenceRegistry, exact_limit: int = 16) -> list[str]:
    """Largest pairwise-independent subset. Exact up to `exact_limit` items; beyond that a
    greedy subset is returned, which is a valid LOWER bound (safe for 'need >= k' tests)."""
    ids = sorted(set(ids))
    if not ids:
        return []
    if len(ids) <= exact_limit:
        for k in range(len(ids), 0, -1):
            for combo in combinations(ids, k):
                if all(independent(x, y, reg) for x, y in combinations(combo, 2)):
                    return list(combo)
        return []
    chosen: list[str] = []
    for i in ids:
        if all(independent(i, c, reg) for c in chosen):
            chosen.append(i)
    return chosen


@dataclass(frozen=True)
class RoleDecision:
    status: str
    independent_items: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]       # (evidence id, reason)


def decide_role(item_ids: list[str], reg: EvidenceRegistry, require: int = 2) -> RoleDecision:
    """Role status from evidence. CONFIRMED needs `require` pairwise-independent usable items.

    Excluded (never counted): evidence that depends on connectivity (circular), and evidence
    from RASTER/OCR sources. Unknown ids are excluded and reported, never trusted.
    """
    usable: list[str] = []
    excluded: list[tuple[str, str]] = []
    for i in sorted(set(item_ids)):
        it = reg.items.get(i)
        if it is None:
            excluded.append((i, "UNKNOWN_EVIDENCE_ID"))
        elif reg.depends_on_connectivity(i):
            excluded.append((i, "CIRCULAR_CONNECTIVITY_BASIS"))
        elif it.source in WEAK_SOURCES:
            excluded.append((i, "WEAK_SOURCE_CANNOT_CONFIRM"))
        else:
            usable.append(i)
    best = max_independent_subset(usable, reg)
    if len(best) >= require:
        status = ROLE_CONFIRMED
    elif usable:
        status = ROLE_CANDIDATE
    else:
        status = ROLE_UNKNOWN
    return RoleDecision(status, tuple(best), tuple(excluded))
