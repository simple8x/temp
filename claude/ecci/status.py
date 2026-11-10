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

# Exact role-kind multiset of each SOUND rule (trust boundary of VERIFIED): the set check above lets
# e.g. TR3 run on PIN+PIN or TR2 on WIRE+WIRE; a SOUND rule needs exactly these two kinds.
SOUND_KINDS: dict[str, tuple[str, str]] = {
    "TR1": ("WIRE", "WIRE"),
    "TR2": ("DOT", "WIRE"),
    "TR3": ("PIN", "WIRE"),
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


def _valid_ids(xs) -> bool:
    return isinstance(xs, tuple) and all(isinstance(x, str) and x.strip() for x in xs)


def effective_role(r: RoleRef) -> tuple[str, str]:
    if r.status == ROLE_CONFIRMED:
        if not _valid_ids(r.evidence):
            return ROLE_CANDIDATE, "CONFIRMED_WITH_BAD_EVIDENCE_ID"
        if len(set(r.evidence)) < 2:
            return ROLE_CANDIDATE, "CONFIRMED_WITHOUT_2_EVIDENCE"
        return ROLE_CONFIRMED, ""
    return r.status, ""


def classify_edge(e: Edge) -> tuple[str, str]:
    """Return (status, reason). Pure: no registry, no global state, no side effects."""
    # fail closed on anything that is not exactly the record classify_edge was written for
    if not isinstance(e, Edge) or not isinstance(e.roles, tuple):
        return UNKNOWN, "BAD_TYPE"
    rule = RULES.get(e.rule) if isinstance(e.rule, str) else None
    if rule is None:
        return UNKNOWN, "UNKNOWN_RULE"
    kind, role_kinds, pre = rule
    if len(e.roles) != 2:
        return UNKNOWN, "BAD_ARITY"
    if not all(isinstance(r, RoleRef) for r in e.roles):
        return UNKNOWN, "BAD_TYPE"
    if any(r.kind not in role_kinds for r in e.roles):
        return UNKNOWN, "RULE_ROLE_KIND_MISMATCH"
    if kind == SOUND and tuple(sorted(r.kind for r in e.roles)) != SOUND_KINDS[e.rule]:
        return UNKNOWN, "RULE_ROLE_KIND_MISMATCH"
    if not (isinstance(e.a, str) and isinstance(e.b, str) and e.a and e.b):
        return UNKNOWN, "BAD_ENDPOINT"
    if e.a == e.b:
        return UNKNOWN, "SELF_LOOP"
    if (e.roles[0].obj, e.roles[1].obj) != (e.a, e.b):
        return UNKNOWN, "ROLE_OBJ_MISMATCH"
    if not isinstance(e.n_prims, int) or isinstance(e.n_prims, bool):
        return UNKNOWN, "BAD_N_PRIMS"
    if any(r.status not in (ROLE_CONFIRMED, ROLE_CANDIDATE) for r in e.roles):
        return UNKNOWN, "ROLE_UNKNOWN"
    if pre == "eq2" and e.n_prims != 2:
        return UNKNOWN, "RULE_PRECONDITION_N_PRIMS"
    if pre == "eq1" and e.n_prims != 1:
        return UNKNOWN, "RULE_PRECONDITION_N_PRIMS"
    if not isinstance(e.negative, tuple) or e.negative:
        return UNKNOWN, "NEGATIVE_EVIDENCE"
    if kind == CONVENTIONAL:
        return INFERRED, "CONVENTIONAL_RULE"
    eff = [effective_role(r) for r in e.roles]
    if any(s != ROLE_CONFIRMED for s, _ in eff):
        return INFERRED, "ROLE_NOT_CONFIRMED"
    if any(r.weak is not False for r in e.roles):
        return INFERRED, "WEAK_SOURCE"
    if not (isinstance(e.coord, str) and e.coord.strip()):
        return INFERRED, "MISSING_COORD_RECORD"
    if not e.prov or not _valid_ids(e.prov):
        return INFERRED, "MISSING_PROVENANCE"
    return VERIFIED, "PREMISES_COMPLETE"


def audit_verified(e: Edge) -> list[str]:
    """Independent re-check of the premise record of a VERIFIED edge (TC-H6 / I2).

    Written separately from `classify_edge` on purpose, so that a bug in one does not hide
    in the other. Returns a list of problems; empty means the record is complete."""
    if not isinstance(e, Edge):
        return ["not an Edge record"]
    problems: list[str] = []
    rule = RULES.get(e.rule) if isinstance(e.rule, str) else None
    if rule is None or rule[0] != SOUND:
        problems.append("rule is not SOUND")
    if not (isinstance(e.coord, str) and e.coord.strip()):
        problems.append("no coordinate record")
    if not (isinstance(e.prov, tuple) and e.prov and all(isinstance(x, str) and x.strip() for x in e.prov)):
        problems.append("no provenance record")
    if not isinstance(e.roles, tuple) or len(e.roles) != 2 or not all(isinstance(r, RoleRef) for r in e.roles):
        problems.append("need exactly two role records")
        return problems
    want = {"TR1": ["WIRE", "WIRE"], "TR2": ["DOT", "WIRE"], "TR3": ["PIN", "WIRE"]}.get(e.rule)
    if sorted(r.kind for r in e.roles) != want:
        problems.append(f"role kinds {[r.kind for r in e.roles]} do not fit {e.rule}")
    if e.roles[0].obj != e.a or e.roles[1].obj != e.b:
        problems.append("role records do not name the edge endpoints")
    if e.a == e.b:
        problems.append("self loop")
    need = {"TR1": 2, "TR3": 1}.get(e.rule)
    if need is not None and e.n_prims != need:
        problems.append(f"{e.rule} at a point with {e.n_prims} primitives")
    for r in e.roles:
        if r.status != ROLE_CONFIRMED:
            problems.append(f"role of {r.obj} is {r.status}")
        ev = r.evidence if isinstance(r.evidence, tuple) else ()
        good = {x for x in ev if isinstance(x, str) and x.strip()}
        if len(good) != len(set(ev)) or len(good) < 2:
            problems.append(f"role of {r.obj} has < 2 valid independent evidence ids")
        if r.weak is not False:
            problems.append(f"role of {r.obj} rests on RASTER/OCR")
    if not isinstance(e.negative, tuple) or e.negative:
        problems.append("negative evidence present")
    return problems
