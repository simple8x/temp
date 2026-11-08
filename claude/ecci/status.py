"""Edge status = pure function of (roles, rule, negative evidence). Invariants I1, I2, I3, I7.

There is deliberately NO field and NO function that assigns a status by hand: `classify_edge`
is the only way a status comes into being. Rule preconditions (`n_prims`) are checked here so
that e.g. TR1 can never be applied to a 4-arm point (I7) whatever a caller claims.

Statuses (design v2 s1.2):
  VERIFIED : every premise recorded and satisfied, rule SOUND, no negative evidence.
  INFERRED : consistent but relies on a CONVENTIONAL rule, or a role is only CANDIDATE,
             or the basis is weak (RASTER/OCR).
  UNKNOWN  : not enough evidence, or negative evidence present.
  CONFLICT : assigned only by the solver (constraint violation), never by classify_edge.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evidence import ROLE_CANDIDATE, ROLE_CONFIRMED, ROLE_UNKNOWN

VERIFIED = "VERIFIED"
INFERRED = "INFERRED"
UNKNOWN = "UNKNOWN"
CONFLICT = "CONFLICT"

# Trust order used by the monotonicity property (I6). CONFLICT is the lowest trust.
RANK = {CONFLICT: 0, UNKNOWN: 1, INFERRED: 2, VERIFIED: 3}

SOUND = "SOUND"
CONVENTIONAL = "CONVENTIONAL"

# rule -> (kind, required role kinds {a, b}, precondition on n_prims)
#   TR1 two independent wire primitives end-to-end at one point (exactly 2 prims)
#   TR2 wire arm meets a CONFIRMED junction dot where >= 3 arms meet
#   TR3 pin tip meets exactly one wire (its own stub, or the single wire ending at the tip)
#   TR4 T-touch / several wires at a pin tip, no dot: drawing convention
#   TR5 collinear gap <= g_max: convention
#   TR6 same net name: convention
#   SITE: link committed by resolving an ambiguity site (never VERIFIED)
RULES: dict[str, tuple[str, frozenset[str], str]] = {
    "TR1": (SOUND, frozenset({"WIRE"}), "eq2"),
    "TR2": (SOUND, frozenset({"WIRE", "DOT"}), "any"),
    "TR3": (SOUND, frozenset({"PIN", "WIRE"}), "eq1"),
    "TR4": (CONVENTIONAL, frozenset({"WIRE", "PIN", "DOT"}), "any"),
    "TR5": (CONVENTIONAL, frozenset({"WIRE"}), "any"),
    "TR6": (CONVENTIONAL, frozenset({"WIRE", "PIN", "DOT", "LABEL"}), "any"),
}


@dataclass(frozen=True)
class RoleRef:
    """Premise: the role one end of an edge plays, with the evidence that supports it."""
    obj: str
    kind: str                       # "WIRE" | "PIN" | "DOT" | "LABEL"
    status: str                     # ROLE_CONFIRMED | ROLE_CANDIDATE | ROLE_UNKNOWN
    evidence: tuple[str, ...] = ()  # the pairwise-independent evidence ids (>= 2 for CONFIRMED)
    weak: bool = False              # basis includes RASTER/OCR (I3)


@dataclass(frozen=True)
class Edge:
    id: str
    a: str
    b: str
    rule: str
    roles: tuple[RoleRef, RoleRef]
    coord: str = ""                 # id of the geometric fact (node / point) the rule was applied at
    n_prims: int = 0                # distinct independent primitives at that point (TR1/TR3 precondition)
    negative: tuple[str, ...] = ()  # negative-evidence records
    prov: tuple[str, ...] = ()      # page/op/bbox references (I8)


def effective_role(r: RoleRef) -> tuple[str, str]:
    if r.status == ROLE_CONFIRMED:
        if len(set(r.evidence)) < 2:
            return ROLE_CANDIDATE, "CONFIRMED_WITHOUT_2_EVIDENCE"
        return ROLE_CONFIRMED, ""
    return r.status, ""


def classify_edge(e: Edge) -> tuple[str, str]:
    """Return (status, reason). Pure: no registry, no global state, no side effects."""
    rule = RULES.get(e.rule)
    if rule is None:
        return UNKNOWN, "UNKNOWN_RULE"
    kind, role_kinds, pre = rule
    if len(e.roles) != 2:
        return UNKNOWN, "BAD_ARITY"
    if any(r.kind not in role_kinds for r in e.roles):
        return UNKNOWN, "RULE_ROLE_KIND_MISMATCH"
    if e.a == e.b:
        return UNKNOWN, "SELF_LOOP"
    if any(r.status not in (ROLE_CONFIRMED, ROLE_CANDIDATE) for r in e.roles):
        return UNKNOWN, "ROLE_UNKNOWN"
    if pre == "eq2" and e.n_prims != 2:
        return UNKNOWN, "RULE_PRECONDITION_N_PRIMS"
    if pre == "eq1" and e.n_prims != 1:
        return UNKNOWN, "RULE_PRECONDITION_N_PRIMS"
    if e.negative:
        return UNKNOWN, "NEGATIVE_EVIDENCE"
    if kind == CONVENTIONAL:
        return INFERRED, "CONVENTIONAL_RULE"
    eff = [effective_role(r) for r in e.roles]
    if any(s != ROLE_CONFIRMED for s, _ in eff):
        return INFERRED, "ROLE_NOT_CONFIRMED"
    if any(r.weak for r in e.roles):
        return INFERRED, "WEAK_SOURCE"
    if not e.coord:
        return INFERRED, "MISSING_COORD_RECORD"
    return VERIFIED, "PREMISES_COMPLETE"


def audit_verified(e: Edge) -> list[str]:
    """Independent re-check of the premise record of a VERIFIED edge (TC-H6 / I2).

    Written separately from `classify_edge` on purpose, so that a bug in one does not hide
    in the other. Returns a list of problems; empty means the record is complete."""
    problems: list[str] = []
    rule = RULES.get(e.rule)
    if rule is None or rule[0] != SOUND:
        problems.append("rule is not SOUND")
    if not e.coord:
        problems.append("no coordinate record")
    if len(e.roles) != 2:
        problems.append("need exactly two role records")
    for r in e.roles:
        if r.status != ROLE_CONFIRMED:
            problems.append(f"role of {r.obj} is {r.status}")
        if len(set(r.evidence)) < 2:
            problems.append(f"role of {r.obj} has < 2 independent evidence ids")
        if r.weak:
            problems.append(f"role of {r.obj} rests on RASTER/OCR")
    if e.negative:
        problems.append("negative evidence present")
    return problems
