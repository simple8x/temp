"""Evidence-first intermediate representation for PDF-to-schematic recovery.

This module is deliberately a contract layer: it does not infer electrical
connectivity from geometry. Extractors may emit observations and candidate
claims; only claims with explicit, traceable evidence can be marked VERIFIED.
UNKNOWN and CONFLICT claims are never exported as electrical edges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Iterable, Optional, Tuple


class ClaimState(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class Relation(str, Enum):
    CONNECTED = "CONNECTED"
    SEPARATE = "SEPARATE"
    ATTACHED_TO = "ATTACHED_TO"
    SAME_LABEL_SCOPE = "SAME_LABEL_SCOPE"


class Modality(str, Enum):
    VECTOR_TEXT = "VECTOR_TEXT"
    MIXED = "MIXED"
    IMAGE_ONLY = "IMAGE_ONLY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceRef:
    """Stable pointer back to a PDF object/operator or reviewed image region."""

    document_sha256: str
    page_number: int
    object_ref: str
    operator_index: Optional[int] = None
    form_path: Tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if len(self.document_sha256) != 64:
            raise ValueError("document_sha256 must be a 64-character SHA-256 hex digest")
        try:
            int(self.document_sha256, 16)
        except ValueError as exc:
            raise ValueError("document_sha256 must be hexadecimal") from exc
        if self.page_number < 1:
            raise ValueError("page_number is 1-based and must be positive")
        if not self.object_ref.strip():
            raise ValueError("object_ref must not be empty")
        if self.operator_index is not None and self.operator_index < 0:
            raise ValueError("operator_index must be non-negative")


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not (isfinite(self.x) and isfinite(self.y)):
            raise ValueError("coordinates must be finite")


@dataclass(frozen=True)
class ForensicPrimitive:
    """Observed PDF geometry. It carries no implied electrical semantics."""

    primitive_id: str
    kind: str
    points: Tuple[Point, ...]
    source: SourceRef
    raw_ctm: Tuple[float, float, float, float, float, float]
    clip_id: Optional[str] = None
    stroke_width: Optional[float] = None
    visible: Optional[bool] = None

    def __post_init__(self) -> None:
        if not self.primitive_id.strip() or not self.kind.strip():
            raise ValueError("primitive_id and kind must not be empty")
        if len(self.points) < 2:
            raise ValueError("a geometric primitive needs at least two points")
        if len(self.raw_ctm) != 6 or not all(isfinite(v) for v in self.raw_ctm):
            raise ValueError("raw_ctm must contain six finite affine coefficients")
        if self.stroke_width is not None and (
            not isfinite(self.stroke_width) or self.stroke_width < 0
        ):
            raise ValueError("stroke_width must be finite and non-negative")


@dataclass(frozen=True)
class ConnectivityClaim:
    """A semantic relation with explicit provenance and a fail-closed state."""

    claim_id: str
    subject: str
    relation: Relation
    object: str
    state: ClaimState
    evidence: Tuple[SourceRef, ...] = ()
    policy_id: str = ""
    rationale: str = ""
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.claim_id, self.subject, self.object)):
            raise ValueError("claim_id, subject and object must not be empty")
        if self.state is ClaimState.VERIFIED and not self.evidence:
            raise ValueError("VERIFIED claims require at least one source evidence reference")
        if self.state is ClaimState.UNKNOWN and not self.rationale.strip():
            raise ValueError("UNKNOWN claims require a rationale")
        if self.state is ClaimState.CONFLICT and not self.rationale.strip():
            raise ValueError("CONFLICT claims require a conflict explanation")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass(frozen=True)
class DocumentIR:
    document_sha256: str
    modality: Modality
    page_count: int
    primitives: Tuple[ForensicPrimitive, ...] = ()
    claims: Tuple[ConnectivityClaim, ...] = ()
    metadata: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if len(self.document_sha256) != 64:
            raise ValueError("document_sha256 must be a SHA-256 hex digest")
        try:
            int(self.document_sha256, 16)
        except ValueError as exc:
            raise ValueError("document_sha256 must be hexadecimal") from exc
        if self.page_count < 1:
            raise ValueError("page_count must be positive")
        ids = [p.primitive_id for p in self.primitives]
        if len(ids) != len(set(ids)):
            raise ValueError("primitive_id values must be unique")
        claim_ids = [c.claim_id for c in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique")
        if any(p.source.document_sha256 != self.document_sha256 for p in self.primitives):
            raise ValueError("primitive source document hash does not match DocumentIR")
        if any(cite.document_sha256 != self.document_sha256
               for claim in self.claims for cite in claim.evidence):
            raise ValueError("claim evidence document hash does not match DocumentIR")


def exportable_connectivity(claims: Iterable[ConnectivityClaim]) -> Tuple[ConnectivityClaim, ...]:
    """Return only VERIFIED CONNECTED claims; never coerce UNKNOWN/CONFLICT."""
    return tuple(
        claim for claim in claims
        if claim.state is ClaimState.VERIFIED
        and claim.relation is Relation.CONNECTED
    )


def unresolved_claims(claims: Iterable[ConnectivityClaim]) -> Tuple[ConnectivityClaim, ...]:
    """Return UNKNOWN and CONFLICT claims for review artifacts."""
    return tuple(
        claim for claim in claims
        if claim.state in (ClaimState.UNKNOWN, ClaimState.CONFLICT)
    )
