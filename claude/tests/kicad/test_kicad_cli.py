"""M4.2 round trip through the REAL kicad-cli. Collected only with ECCI_KICAD_TESTS=1 (see tests/conftest.py);
there a missing kicad-cli FAILS: no skip can be counted as a pass."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

from ecci import kicad_export as kx, kicad_roundtrip as kr  # noqa: E402
from ecci.m4_pipeline import run_page  # noqa: E402
from m4_helpers import LIB, LIB_TEXT, docs  # noqa: E402
from m4_scenes import all_scenes, chain  # noqa: E402

CLI = kr.kicad_cli()


def test_kicad_cli_is_installed():
    assert CLI, "kicad-cli not found: the KiCad gates cannot run (BLOCKED), and this job must not be green"
    assert kr.kicad_version(CLI)


@pytest.mark.parametrize("scene", all_scenes(), ids=lambda s: s.name)
def test_scene_round_trips_through_kicad_cli(scene, tmp_path):
    pi, cls, cl, built, res = scene.run()
    rec = run_page(tmp_path, pi, cls, cl, built, res, LIB, LIB_TEXT, {"name": scene.name}, scene.gt_pins(), CLI,
                   gt_policy=scene.gt_policy())
    expected = {s: "PASS" for s in rec["stages"]}
    if scene.name == "t_nodot":
        # The T-end touches a wire in KiCad semantics, but without a junction dot ECCI
        # keeps that edge INFERRED and does not export it. The stricter FN gate MUST fail.
        expected["ground_truth"] = "FAIL"
        assert rec["ground_truth"]["pairs"]["FP"] == 0
        assert rec["ground_truth"]["pairs"]["FN"] > 0
        assert "false-negative" in rec["ground_truth"]["gate"]["reason"]
    if scene.name == "ambiguous_r":
        # Synthetic canonical resistor numbering may be exported, but source pin identity
        # remains UNKNOWN and is excluded from GT coverage/PASS.
        expected["ground_truth"] = "INCONCLUSIVE"
        assert rec["ground_truth"]["pin_identity_unverified_synthetic_canonical"]
        assert rec["ground_truth"]["exported_pins_mapped"] < rec["ground_truth"]["gt_scoreable_pins"]
    assert rec["stages"] == expected, rec
    assert (tmp_path / "netlist.xml").exists() and (tmp_path / "roundtrip.json").exists()


@pytest.mark.parametrize("o", ["dg", "ad"])
def test_mirror_order_is_the_one_kicad_uses(o, tmp_path, monkeypatch):
    """The diagonal reflections are the only orientations whose KiCad meaning depends on the mirror/rotate
    order. The configured order must round-trip, and the other one must be DETECTED (the check is sensitive)."""
    pi, conn, syms = docs(chain(o))
    for order, ok in ((kx.MIRROR_ORDER, True), ({"RM": "MR", "MR": "RM"}[kx.MIRROR_ORDER], False)):
        monkeypatch.setattr(kx, "MIRROR_ORDER", order)
        sch, man = kx.export(conn, syms, LIB_TEXT)
        assert sum(d["decision"] == "PLACED" for d in man["symbols"]) == 2 and len(man["nets"]) == 1
        p = tmp_path / f"{order}.kicad_sch"
        p.write_text(sch)
        r = kr.export_netlist(CLI, str(p), str(tmp_path / f"{order}.xml"))
        assert r.returncode == 0, r.stderr
        rt = kr.compare_roundtrip(man, sch, kr.read_netlist(str(tmp_path / f"{order}.xml")))
        assert (rt["problems"] == []) is ok, (order, rt)


def test_unreadable_schematic_fails_and_a_stale_netlist_is_never_read(tmp_path):
    bad = tmp_path / "bad.kicad_sch"
    bad.write_text("(kicad_sch (version 20230121) (generator ecci) (lib_symbols (symbol \"x\" )")
    xml = tmp_path / "bad.xml"
    xml.write_text("<export/>")              # a stale result from an earlier run
    r = kr.export_netlist(CLI, str(bad), str(xml))
    assert r.returncode != 0
    assert not xml.exists()
