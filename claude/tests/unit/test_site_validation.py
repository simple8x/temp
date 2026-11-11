import pytest

from ecci.solver import ConnGraph, GraphError, Obj, Option, Site, fourway_site, solve


def graph(site):
    return ConnGraph(
        objects=[Obj(name, "WIRE") for name in ("a", "b", "outside")],
        edges=[],
        sites=[site],
    )


def test_site_option_cannot_link_an_object_outside_its_arms():
    site = Site(
        "s1", "MULTI", (("N", "a"), ("S", "b")),
        (Option("BAD", frozenset({("a", "outside")})),),
    )
    with pytest.raises(GraphError, match="distinct site arms"):
        solve(graph(site))


def test_site_option_cannot_link_an_arm_to_itself():
    site = Site(
        "s1", "MULTI", (("N", "a"), ("S", "b")),
        (Option("BAD", frozenset({("a", "a")})),),
    )
    with pytest.raises(GraphError, match="distinct site arms"):
        solve(graph(site))


def test_site_rejects_duplicate_arm_labels():
    site = Site(
        "s1", "MULTI", (("N", "a"), ("N", "b")),
        (Option("NONE", frozenset()),),
    )
    with pytest.raises(GraphError, match="duplicate arm label"):
        solve(graph(site))


def test_site_rejects_duplicate_arm_objects():
    site = Site(
        "s1", "MULTI", (("N", "a"), ("S", "a")),
        (Option("NONE", frozenset()),),
    )
    with pytest.raises(GraphError, match="duplicate arm object"):
        solve(graph(site))


def test_valid_fourway_domain_uses_only_its_declared_arms():
    site = fourway_site("s1", {"N": "a", "E": "b", "S": "outside", "W": "c"})
    assert solve(graph(site)).sites["s1"].status == "UNKNOWN"
