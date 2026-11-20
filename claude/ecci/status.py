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

import math
import re
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
    if any(not isinstance(r.kind, str) or r.kind not in role_kinds for r in e.roles):
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


# ---- independent audit tables: deliberately NOT derived from RULES / SOUND_KINDS / _valid_ids, so that an edit
# to classify_edge's tables or helpers cannot silently change what the audit accepts.
_AUDIT_KINDS = {"TR1": ("WIRE", "WIRE"), "TR2": ("DOT", "WIRE"), "TR3": ("PIN", "WIRE")}
_AUDIT_ROLE_KINDS = ("WIRE", "PIN", "DOT", "LABEL")
_AUDIT_NUM = r"-?[0-9]+(?:\.[0-9]+)?"
# pipeline coordinate record (rules.py): "node:<id>@x,y" (TR1/TR2) or "pin:<cid>:<k>@x,y" (TR3)
_AUDIT_COORD = re.compile(rf"(?:node|pin):[^@\s]+@({_AUDIT_NUM}),({_AUDIT_NUM})")


def _audit_str(x) -> bool:
    return type(x) is str and x.strip() != ""


def _audit_coord(c) -> str:
    """'' if `c` is a well-formed coordinate record with two finite numbers, else the problem."""
    if not _audit_str(c):
        return "no coordinate record"
    m = _AUDIT_COORD.fullmatch(c)
    if m is None:
        return f"coordinate record {c!r} is not <node|pin>:<id>@<x>,<y> with numeric x, y"
    if not all(math.isfinite(float(v)) for v in m.groups()):
        return f"coordinate record {c!r} is not finite"
    return ""


def _audit_ids(xs, what: str, need: int) -> list[str]:
    """Problems with a tuple of evidence / provenance ids. Never hashes an element before its type is checked."""
    if type(xs) is not tuple:
        return [f"{what} is {type(xs).__name__}, not a tuple"]
    bad = [x for x in xs if not _audit_str(x)]
    if bad:
        return [f"{what} has malformed ids {bad!r}"]
    out = []
    if len(set(xs)) != len(xs):
        out.append(f"{what} has duplicate ids")
    if len(set(xs)) < need:
        out.append(f"{what} has {len(set(xs))} < {need} distinct ids")
    return out


def audit_verified(e: Edge) -> list[str]:
    """Independent re-check of the premise record of a VERIFIED edge (TC-H6 / I2).

    Written separately from `classify_edge` on purpose (own tables, own helpers, never calls it), so that a bug
    in one does not hide in the other. Total: returns a list of problems for any input, never raises; empty means
    the record is complete. Nothing is coerced: a value of the wrong type is a problem, not converted."""
    if not isinstance(e, Edge):
        return ["not an Edge record"]
    try:
        return _audit_verified(e)
    except Exception as x:          # belt and braces: a malformed record must be a violation, not a crash
        return [f"audit could not read the record: {type(x).__name__}"]


def _audit_verified(e: Edge) -> list[str]:
    problems: list[str] = []
    rule = e.rule if type(e.rule) is str else None
    want = _AUDIT_KINDS.get(rule) if rule is not None else None
    if want is None:
        problems.append("rule is not SOUND")
    ends_ok = _audit_str(e.a) and _audit_str(e.b)
    if not ends_ok:
        problems.append(f"malformed endpoint ids a={e.a!r} b={e.b!r}")
    elif e.a == e.b:
        problems.append("self loop")
    c = _audit_coord(e.coord)
    if c:
        problems.append(c)
    if type(e.prov) is not tuple or not e.prov:
        problems.append("no provenance record")
    else:
        bad = _audit_ids(e.prov, "provenance", 1)
        if bad:
            problems += ["no provenance record"] + bad
    if type(e.negative) is not tuple or e.negative:
        problems.append("negative evidence present")
    if type(e.n_prims) is not int:
        problems.append(f"n_prims {e.n_prims!r} is not an int")
    elif rule == "TR1" and e.n_prims != 2 or rule == "TR3" and e.n_prims != 1 or rule == "TR2" and e.n_prims < 2:
        problems.append(f"{rule} at a point with {e.n_prims} primitives")
    if type(e.roles) is not tuple or len(e.roles) != 2 or not all(type(r) is RoleRef for r in e.roles):
        problems.append("need exactly two role records")
        return problems
    kinds = [r.kind for r in e.roles]
    if not all(type(k) is str and k in _AUDIT_ROLE_KINDS for k in kinds):
        problems.append(f"invalid role kinds {kinds!r}")
    elif want is not None and tuple(sorted(kinds)) != want:
        problems.append(f"role kinds {kinds} do not fit {rule}")
    objs = [r.obj for r in e.roles]
    if not all(_audit_str(o) for o in objs):
        problems.append(f"malformed role objects {objs!r}")
    if not (ends_ok and all(_audit_str(o) for o in objs) and objs[0] == e.a and objs[1] == e.b):
        problems.append("role records do not name the edge endpoints")
    for r in e.roles:
        who = r.obj if _audit_str(r.obj) else repr(r.obj)
        if type(r.status) is not str or r.status != ROLE_CONFIRMED:
            problems.append(f"role of {who} is {r.status!r}")
        ev = _audit_ids(r.evidence, "evidence", 2)
        if ev:
            problems.append(f"role of {who} has < 2 valid independent evidence ids ({'; '.join(ev)})")
        if r.weak is not False:
            problems.append(f"role of {who} rests on RASTER/OCR (weak={r.weak!r})")
    return problems
