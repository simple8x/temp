import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "m4_runner", Path(__file__).with_name("run_benchmark.py")
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_pairwise_metrics_are_exact():
    ref = {("R1", "1"), ("R1", "2"), ("U1", "1")}
    got = {("R1", "1"), ("R1", "2"), ("U1", "1")}
    result = MODULE.metrics(got, ref)
    assert result["tp"] == 3
    assert result["fp"] == 0
    assert result["fn"] == 0
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0


def test_pairwise_metrics_count_false_positive_and_negative():
    result = MODULE.metrics({1, 2}, {2, 3})
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 1)
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5


def test_empty_denominator_is_not_reported_as_zero_accuracy():
    result = MODULE.metrics(set(), set())
    assert result["precision"] is None
    assert result["recall"] is None
    assert result["f1"] is None


def test_kicad_xml_reference_netlist_parser(tmp_path):
    xml = tmp_path / "reference.xml"
    xml.write_text(
        '<export><nets>'
        '<net code="1" name="GND"><node ref="R1" pin="1"/>'
        '<node ref="U1" pin="2"/></net>'
        '<net code="2" name="VCC"><node ref="U1" pin="1"/>'
        '<node ref="C1" pin="2"/></net>'
        '</nets></export>',
        encoding="utf-8",
    )
    pins, pairs, summary = MODULE.parse_reference_xml(xml)
    assert pins == {("R1", "1"), ("U1", "2"), ("U1", "1"), ("C1", "2")}
    assert pairs == {
        (("R1", "1"), ("U1", "2")),
        (("C1", "2"), ("U1", "1")),
    }
    assert summary == {"net_count": 2, "connected_pin_count": 4, "pair_count": 2}


def test_dataset_uses_immutable_commit_ids_and_explicit_license_fields():
    import json
    manifest = json.loads(Path(__file__).with_name("m4_dataset.json").read_text())
    assert len(manifest["projects"]) >= 10
    for item in manifest["projects"]:
        assert len(item["commit"]) == 40
        assert item["license"] in {"MIT", "CC0-1.0"}
        assert item["license_file"]
        assert item["schematic"].endswith(".kicad_sch")
