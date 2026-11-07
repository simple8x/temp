import pytest

from pdf2kicad.evidence_ir import (
    ClaimState,
    ConnectivityClaim,
    DocumentIR,
    ForensicPrimitive,
    Modality,
    Point,
    Relation,
    SourceRef,
    exportable_connectivity,
    unresolved_claims,
)

DOC = "a" * 64


def source(operator_index=12):
    return SourceRef(
        document_sha256=DOC,
        page_number=5,
        object_ref="content-stream:7",
        operator_index=operator_index,
    )


def test_forensic_geometry_does_not_imply_connectivity():
    primitive = ForensicPrimitive(
        primitive_id="stroke-1",
        kind="line",
        points=(Point(1, 2), Point(10, 2)),
        source=source(),
        raw_ctm=(1, 0, 0, 1, 0, 0),
        stroke_width=0.5,
    )
    ir = DocumentIR(DOC, Modality.VECTOR_TEXT, 11, primitives=(primitive,))
    assert ir.primitives == (primitive,)
    assert exportable_connectivity(ir.claims) == ()


def test_verified_connected_requires_direct_evidence():
    with pytest.raises(ValueError, match="evidence"):
        ConnectivityClaim(
            "c1", "pin:U1.1", Relation.CONNECTED, "node:4",
            ClaimState.VERIFIED,
        )


def test_unknown_is_not_exported_and_is_reviewable():
    unknown = ConnectivityClaim(
        "c-unknown", "pin:U1.1", Relation.CONNECTED, "node:4",
        ClaimState.UNKNOWN, rationale="pin endpoint is occluded",
    )
    assert exportable_connectivity((unknown,)) == ()
    assert unresolved_claims((unknown,)) == (unknown,)


def test_conflict_is_not_exported():
    conflict = ConnectivityClaim(
        "c-conflict", "pin:U1.1", Relation.CONNECTED, "node:4",
        ClaimState.CONFLICT, rationale="PDF marker and independent parser disagree",
    )
    assert exportable_connectivity((conflict,)) == ()
    assert unresolved_claims((conflict,)) == (conflict,)


def test_verified_separate_is_not_a_net_edge():
    separate = ConnectivityClaim(
        "c-separate", "node:1", Relation.SEPARATE, "node:2",
        ClaimState.VERIFIED, evidence=(source(),),
    )
    assert exportable_connectivity((separate,)) == ()


def test_document_rejects_cross_document_evidence():
    foreign = SourceRef("b" * 64, 5, "stream:1")
    claim = ConnectivityClaim(
        "c1", "pin:U1.1", Relation.CONNECTED, "node:4",
        ClaimState.VERIFIED, evidence=(foreign,),
    )
    with pytest.raises(ValueError, match="document hash"):
        DocumentIR(DOC, Modality.VECTOR_TEXT, 11, claims=(claim,))


def test_invalid_coordinates_and_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="finite"):
        Point(float("nan"), 1)
    p = ForensicPrimitive(
        "same", "line", (Point(0, 0), Point(1, 0)), source(),
        (1, 0, 0, 1, 0, 0),
    )
    with pytest.raises(ValueError, match="unique"):
        DocumentIR(DOC, Modality.VECTOR_TEXT, 11, primitives=(p, p))
