"""Fail-closed ground-truth evaluation (external review P1): an incomplete corpus is never a result."""
import hashlib
import importlib
import os
import pathlib
import subprocess
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "tools"


@pytest.fixture()
def gt_eval(monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    monkeypatch.delenv("GT_ONLY", raising=False)
    sys.modules.pop("gt_eval", None)
    return importlib.import_module("gt_eval")


def _dirs(tmp_path, n):
    out = []
    for k in range(n):
        d = tmp_path / f"p{k}"
        d.mkdir(exist_ok=True)
        (d / "a.xml").write_text("x")
        (d / "a.pdf").write_text("x")
        out.append(d)
    return out


PIN = {"KICAD_SRC_SHA": "deadbeef", "KICAD_CLI_VERSION": "9.0.9", "EXPECT_EXPORT_OK": "2",
       "EXPECT_EXPORT_FAIL": "brokenproj", "EXPECT_MIN_PAGES": "2", "EXPECT_TOTAL_PAGES": "3",
       "SKIP_REPEATED_SHEET": "p1#p7"}


def test_page_error_is_a_problem(gt_eval, tmp_path):
    cov = gt_eval.coverage([[{"project": "a"}], [{"project": "b#p0", "error": "TimeoutError: page exceeded 150s"}]],
                           _dirs(tmp_path, 2), PIN)
    assert cov["errors"] == ["b#p0"] and any("page error" in p for p in cov["problems"])
    assert cov["evaluated"] == 1 and cov["unevaluated_pages"] == 1


def test_a_planned_page_that_never_ran_is_a_problem(gt_eval, tmp_path):
    cov = gt_eval.coverage([[{"project": "a"}, {"task": {"name": "a#p1"}}]], _dirs(tmp_path, 1), PIN)
    assert "page never evaluated: a#p1" in cov["problems"] and cov["evaluated"] == 1


def test_unlisted_unmapped_page_fails_the_run(gt_eval, tmp_path):
    rs = [[{"project": "p0"}, {"project": "p0#p3", "skipped": "page not mapped to one sheet (best 1.0, runner-up 1.0)"}]]
    cov = gt_eval.coverage(rs, _dirs(tmp_path, 2), PIN)
    assert any("not pinned (unlisted_unmapped): p0#p3" in p for p in cov["problems"])
    assert cov["disposition"]["unlisted_unmapped"] == ["p0#p3"]


def test_pinned_unmapped_page_is_accounted_for_but_not_counted_as_measured(gt_eval, tmp_path):
    rs = [[{"project": "p0"}, {"project": "p1"}],
          [{"project": "p1#p7", "skipped": "page not mapped to one sheet (best 1.0, runner-up 1.0)"}],
          [{"project": "brokenproj", "skipped": "no pdf/netlist"}]]
    cov = gt_eval.coverage(rs, _dirs(tmp_path, 2), PIN)
    assert cov["problems"] == [], cov["problems"]
    assert cov["evaluated"] == 2 and cov["unevaluated_pages"] == 1 and cov["page_coverage"] == 0.6667
    assert cov["disposition"]["repeated_sheet"] == ["p1#p7"] and cov["disposition"]["no_export"] == ["brokenproj"]


def test_a_stale_skip_pin_fails_the_run(gt_eval, tmp_path):
    # mapping improved and p1#p7 is now evaluated: the pin is stale and must be updated, not ignored
    rs = [[{"project": "p0"}, {"project": "p1#p7"}, {"project": "x"}],
          [{"project": "brokenproj", "skipped": "no pdf/netlist"}]]
    cov = gt_eval.coverage(rs, _dirs(tmp_path, 2), PIN)
    assert any("pinned as skipped but evaluated" in p for p in cov["problems"])


def test_missing_export_failure_or_page_total_fails_the_run(gt_eval, tmp_path):
    rs = [[{"project": "p0"}, {"project": "p1"}],
          [{"project": "p1#p7", "skipped": "page not mapped to one sheet (best 1.0, runner-up 1.0)"}]]
    cov = gt_eval.coverage(rs, _dirs(tmp_path, 2), PIN)      # brokenproj never reported
    assert any("export-failure set" in p for p in cov["problems"])
    rs2 = [[{"project": "p0"}], [{"project": "brokenproj", "skipped": "no pdf/netlist"}]]
    cov2 = gt_eval.coverage(rs2, _dirs(tmp_path, 2), PIN)
    assert any("pinned as skipped but not reported at all" in p for p in cov2["problems"])
    assert any("pages accounted for != pinned total" in p for p in cov2["problems"])


def test_gt_only_fails_when_a_selected_page_was_not_evaluated(gt_eval, tmp_path, monkeypatch):
    monkeypatch.setattr(gt_eval, "ONLY", ["p1#p7", "p0"])
    rs = [[{"project": "p0"}],
          [{"project": "p1#p7", "skipped": "page not mapped to one sheet (best 1.0, runner-up 1.0)"}]]
    cov = gt_eval.coverage(rs, _dirs(tmp_path, 2), PIN)
    assert any("GT_ONLY selected pages that were not evaluated: ['p1#p7']" in p for p in cov["problems"])
    monkeypatch.setattr(gt_eval, "ONLY", ["p0"])
    assert gt_eval.coverage([[{"project": "p0#p0"}]], _dirs(tmp_path, 1), PIN)["problems"] == []


def test_pins_reads_both_real_pin_files(gt_eval):
    pin = gt_eval.pins()
    assert pin["KICAD_CLI_VERSION"] and pin["EXPECT_TOTAL_PAGES"].isdigit()
    assert len(pin["SKIP_REPEATED_SHEET"].split(",")) == 42
    # the page bookkeeping must NOT be part of the export cache key, or every inventory fix costs a re-export
    wf = (TOOLS.parents[1] / ".github/workflows/claude-gt.yml").read_text()
    assert "gt_source.txt" in wf.split("key: gt-out-")[1].split("\n")[0]
    assert "gt_pages.txt" not in wf.split("key: gt-out-")[1].split("\n")[0]


def test_main_exits_nonzero_when_a_project_raises(gt_eval, tmp_path, monkeypatch, capsys):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "broken.xml").write_text("<not a netlist")
    (d / "broken.pdf").write_bytes(b"not a pdf")
    monkeypatch.setenv("GT_WORKERS", "1")
    assert gt_eval.main(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "GT_INCOMPLETE" in out and '"inventory_consistent": false' in out


def _fake_gt(tmp_path, n_ok=None, failed=None):
    """A gt_out tree that satisfies every pin, so each test can break exactly one thing."""
    pins = dict(line.split("=", 1) for f in ("gt_source.txt", "gt_pages.txt")
                for line in (TOOLS / f).read_text().splitlines()
                if "=" in line and not line.startswith("#"))
    n_ok = int(pins["EXPECT_EXPORT_OK"]) if n_ok is None else n_ok
    failed = pins["EXPECT_EXPORT_FAIL"].split(",") if failed is None else failed
    out = tmp_path / "gt_out"
    for k in range(n_ok):
        d = out / f"proj{k}"
        (d / "src").mkdir(parents=True)
        (d / f"proj{k}.pdf").write_text(f"pdf{k}")
        (d / f"proj{k}.xml").write_text(f"net{k}")
        (d / "src" / "sheet.kicad_sch").write_text("(kicad_sch)")
    for name in failed:
        (out / name).mkdir(parents=True, exist_ok=True)
        (out / name / "pdf.log").write_text("failed")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()   # noqa: E731
    (out / "PROVENANCE.txt").write_text(
        f"KICAD_SOURCE_SHA={pins['KICAD_SRC_SHA']}\nKICAD_CLI={pins['KICAD_CLI_VERSION']}\n"
        f"EXPORT_SCRIPT_SHA256={sha(TOOLS / 'gt_export.sh')}\nSOURCE_PINS_SHA256={sha(TOOLS / 'gt_source.txt')}\n")
    _write_manifest(out)
    return out


def _write_manifest(out):
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()   # noqa: E731
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    (out / "MANIFEST.sha256").write_text("".join(f"{sha(p)}  ./{p.relative_to(out)}\n" for p in files))


def _verify(out):
    return subprocess.run(["bash", str(TOOLS / "gt_verify.sh"), str(out)], capture_output=True, text=True)


def test_verify_accepts_a_complete_consistent_corpus(tmp_path):
    r = _verify(_fake_gt(tmp_path))
    assert r.returncode == 0 and "GT_VERIFY OK" in r.stdout, r.stdout


def test_verify_rejects_tampered_file_wrong_pin_and_missing_provenance(tmp_path):
    out = _fake_gt(tmp_path)
    (out / "proj0" / "proj0.xml").write_text("tampered")
    assert "GT_VERIFY FAIL: manifest" in _verify(out).stdout
    out = _fake_gt(tmp_path / "b")
    prov = (out / "PROVENANCE.txt").read_text()
    (out / "PROVENANCE.txt").write_text(prov.replace("KICAD_SOURCE_SHA=", "KICAD_SOURCE_SHA=0"))
    assert "GT_VERIFY FAIL: source sha" in _verify(out).stdout
    (out / "PROVENANCE.txt").unlink()
    r = _verify(out)
    assert r.returncode == 1 and "no provenance" in r.stdout


def test_verify_rejects_a_truncated_manifest(tmp_path):
    out = _fake_gt(tmp_path)
    lines = (out / "MANIFEST.sha256").read_text().splitlines(keepends=True)
    (out / "MANIFEST.sha256").write_text("".join(lines[:-3]))
    r = _verify(out)
    assert r.returncode == 1 and "manifest inventory differs" in r.stdout
    assert "only on disk" in r.stdout


def test_verify_rejects_an_undeclared_extra_file(tmp_path):
    out = _fake_gt(tmp_path)
    (out / "proj0" / "sneaked-in.xml").write_text("extra")
    r = _verify(out)
    assert r.returncode == 1 and "manifest inventory differs" in r.stdout


def test_verify_rejects_a_missing_pdf_and_an_empty_one(tmp_path):
    out = _fake_gt(tmp_path)
    (out / "proj1" / "proj1.pdf").unlink()
    _write_manifest(out)                      # manifest kept consistent: only the inventory rule can catch this
    r = _verify(out)
    assert r.returncode == 1 and ("0 pdf / 1 netlist" in r.stdout or "exported projects, expected" in r.stdout)
    out = _fake_gt(tmp_path / "c")
    (out / "proj1" / "proj1.pdf").write_text("")
    _write_manifest(out)
    assert "empty pdf" in _verify(out).stdout


def test_verify_rejects_a_project_with_a_pdf_but_no_netlist(tmp_path):
    """One extra complete project keeps the OK count at the pinned value, so only the per-project
    pdf/netlist pairing rule can notice that proj0 lost its netlist."""
    out = _fake_gt(tmp_path, n_ok=33)
    (out / "proj0" / "proj0.xml").unlink()
    _write_manifest(out)
    r = _verify(out)
    assert r.returncode == 1 and "proj0: 1 pdf / 0 netlist" in r.stdout, r.stdout


def test_verify_rejects_a_changed_project_set(tmp_path):
    # a project that was pinned as an export failure now has output, and one pinned OK project is gone
    out = _fake_gt(tmp_path, n_ok=31)
    r = _verify(out)
    assert r.returncode == 1 and "exported projects, expected" in r.stdout
    out2 = _fake_gt(tmp_path / "d", failed=["CM5_MINIMA_3"])
    r2 = _verify(out2)
    assert r2.returncode == 1 and "export-failure set" in r2.stdout


def test_verify_rejects_a_project_without_source_sheets(tmp_path):
    out = _fake_gt(tmp_path)
    (out / "proj2" / "src" / "sheet.kicad_sch").unlink()
    _write_manifest(out)
    r = _verify(out)
    assert r.returncode == 1 and "no source sheet copied" in r.stdout


def test_plan_and_run_are_split_and_output_stays_in_page_order(gt_eval, tmp_path, monkeypatch, capsys):
    # three single-page projects; run_task is replaced, so this checks planning, dispatch and output order only
    for name in ("c_proj", "a_proj", "b_proj"):
        d = tmp_path / name
        (d / "src").mkdir(parents=True)
        (d / f"{name}.xml").write_text("net")
        (d / "src" / f"{name}.kicad_sch").write_text("(kicad_sch)")
        (d / f"{name}.pdf").write_bytes(b"pdf")
    monkeypatch.setattr(gt_eval.pikepdf, "open", lambda *a, **k: __import__("contextlib").nullcontext(type("P", (), {"pages": [0]})()))
    monkeypatch.setenv("GT_WORKERS", "1")
    plans = [gt_eval._safe_plan(d) for d in sorted(tmp_path.iterdir())]
    assert [p[0]["task"]["name"] for p in plans] == ["a_proj", "b_proj", "c_proj"]
    assert all("task" in p[0] and "pdir" in p[0]["task"] for p in plans)


def _selftest_demos(tmp_path, dangling=False):
    """Two minimal 'demos' projects for the export self-test; one can be given an unreadable sheet."""
    src = tmp_path / "demos"
    for name in ("good", "bad"):
        d = src / name
        d.mkdir(parents=True)
        (d / f"{name}.kicad_pro").write_text("{}")
        (d / f"{name}.kicad_sch").write_text("(kicad_sch)")
        (d / "sub.kicad_sch").write_text("(kicad_sch)")
    if dangling:                       # find lists it, cp cannot stat it -> the copy step must fail
        (src / "bad" / "broken.kicad_sch").symlink_to(tmp_path / "nowhere.kicad_sch")
    return src


def _run_export(tmp_path, src, kicad_rc=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    cli = bin_dir / "kicad-cli"
    cli.write_text("#!/usr/bin/env bash\n"
                   'if [ "$1" = version ]; then echo 9.0.9; exit 0; fi\n'
                   'out=""; while [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done\n'
                   f'[ -n "$out" ] && echo x > "$out"\nexit {kicad_rc}\n')
    cli.chmod(0o755)
    env = {**os.environ, "GT_SELFTEST": "1", "GT_SRC_DIR": str(src),
           "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(TOOLS / "gt_export.sh"), str(tmp_path / "out")],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def test_export_selftest_reports_both_projects_and_cannot_pass_as_ground_truth(tmp_path):
    r = _run_export(tmp_path, _selftest_demos(tmp_path))
    assert "EXPORT_OK=2" in r.stdout, r.stdout + r.stderr
    assert "KICAD_SOURCE_SHA=SELFTEST" in (tmp_path / "out" / "PROVENANCE.txt").read_text()
    assert r.returncode != 0 and "EXPORT_COUNT_MISMATCH" in r.stdout      # 2 != the pinned 32
    assert "GT_VERIFY FAIL" in _verify(tmp_path / "out").stdout


def test_export_fails_the_project_when_sheets_cannot_be_copied(tmp_path):
    r = _run_export(tmp_path, _selftest_demos(tmp_path, dangling=True))
    assert "FAIL copy-sheets bad" in r.stdout, r.stdout + r.stderr
    assert "EXPORT_OK=1" in r.stdout and r.returncode != 0
    assert not (tmp_path / "out" / "bad").exists()         # no half-copied project is left behind
    assert "bad" in [line.split("=")[1] for line in (tmp_path / "out" / "PROVENANCE.txt").read_text().splitlines()
                     if line.startswith("EXPORT_FAIL=")][0]


def test_export_fails_the_project_when_kicad_cli_fails(tmp_path):
    r = _run_export(tmp_path, _selftest_demos(tmp_path), kicad_rc=3)
    assert "FAIL pdf good" in r.stdout and "EXPORT_OK=0" in r.stdout and r.returncode != 0


def test_gt_eval_has_a_working_entrypoint(tmp_path):
    """Regression: a rewrite of gt_eval.py once dropped `if __name__ == "__main__"`, so the CI step imported the
    module, produced no output and exited 0 — a green run that measured nothing (run 42782032226)."""
    (tmp_path / "gt_out").mkdir()
    r = subprocess.run([sys.executable, str(TOOLS / "gt_eval.py"), str(tmp_path / "gt_out")],
                       capture_output=True, text=True, env={**os.environ, "GT_WORKERS": "1"})
    assert "GT_TOTAL " in r.stdout, r.stdout + r.stderr
    assert "GT_COVERAGE " in r.stdout
    assert r.returncode == 1 and "GT_INCOMPLETE" in r.stdout      # an empty corpus is not a result


def test_inventory_fails_when_the_evaluation_did_not_run(tmp_path):
    """The inventory step is the gate that the evaluation produced a result at all."""
    import importlib
    sys.path.insert(0, str(TOOLS))
    inv = importlib.import_module("gt_inventory")
    empty = tmp_path / "empty.log"
    empty.write_text("")
    assert inv.main(str(empty)) == 1
    partial = tmp_path / "partial.log"
    partial.write_text('GT_EVAL {"project": "a", "local_pins": 1, "local_pins_found": 1}\n')
    assert inv.main(str(partial)) == 1                            # pages but no GT_TOTAL, no GT_COVERAGE
    cut = tmp_path / "cut.log"                                    # everything except the final GT_TOTAL line:
    cut.write_text('GT_EVAL {"project": "a", "local_pins": 1, "local_pins_found": 1}\n'
                   'GT_COVERAGE {"evaluated": 1}\n')             # the run died between the two
    assert inv.main(str(cut)) == 1
    good = tmp_path / "good.log"
    good.write_text('GT_EVAL {"project": "a", "local_pins": 1, "local_pins_found": 1}\n'
                    'GT_COVERAGE {"evaluated": 1}\n'
                    'GT_TOTAL {"inventory_consistent": true}\n')
    assert inv.main(str(good)) == 0
    bad = tmp_path / "bad.log"
    bad.write_text('GT_EVAL {"project": "a", "local_pins": 1, "local_pins_found": 1}\n'
                   'GT_COVERAGE {"evaluated": 1}\n'
                   'GT_TOTAL {"inventory_consistent": false}\n')
    assert inv.main(str(bad)) == 1
    unpinned = tmp_path / "unpinned.log"
    unpinned.write_text('GT_EVAL {"project": "a", "local_pins": 1, "local_pins_found": 1}\n'
                        'GT_EVAL {"project": "zz#p9", "skipped": "page not mapped to one sheet (best 1.0, runner-up 1.0)"}\n'
                        'GT_COVERAGE {"evaluated": 1}\n'
                        'GT_TOTAL {"inventory_consistent": true}\n')
    assert inv.main(str(unpinned)) == 1
