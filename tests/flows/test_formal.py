# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The formal flow without EDA tools: SBY output parsing (on captured output), job files,
versions, and the errors a caller can hit."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main
from hdl_bag.flows.base import Finding
from hdl_bag.flows.formal import SymbiYosys, job_sources, prove, run_formal, run_sby, sby_job
from hdl_bag.gen import Outputs

DATA = Path(__file__).parent / "data" / "sby"
SOURCES = {
    "rtl": "rtl",
    "apb4_min_reset.sv": "formal/apb4_min_reset.sv",
    "bridges_pulp_cdc_reset.sv": "formal/bridges_pulp_cdc_reset.sv",
}


def _parse(name: str, sources: dict[str, str] | None = None, job_dir: str = "") -> list[Finding]:
    return SymbiYosys().parse((DATA / name).read_text(encoding="utf-8"), sources or SOURCES,
                              job_dir)  # fmt: skip


def test_a_passing_job_has_no_findings() -> None:
    """Both tasks of a generated job (bmc and cover) pass, with every cover reached."""
    assert _parse("pass.log", job_dir="formal") == []


def test_a_failing_assertion_names_the_property_its_file_and_trace() -> None:
    findings = _parse("fail.log", job_dir="formal")
    assert Finding("sby", "f.sv", 7, "FORMAL-ASSERT", "error",
                   "f_bmc: assertion check_10 fails (counterexample "
                   "formal/f_bmc/engine_0/trace.vcd)") in findings  # fmt: skip


def test_an_unreached_cover_is_a_finding() -> None:
    """An unreached cover means the assertions it guards may hold vacuously."""
    assert _parse("cover_unreached.log") == [
        Finding("sby", "formal/bridges_pulp_cdc_reset.sv", 72, "FORMAL-COVER", "error",
                "bridges_pulp_cdc_reset_cover: cover active_uart_apb4_psel not reached")
    ]  # fmt: skip
    assert [f.rule for f in _parse("fail.log")] == ["FORMAL-COVER", "FORMAL-ASSERT"]


def test_a_property_that_holds_but_is_not_inductive() -> None:
    """Mode prove: the base case passes and induction fails, so SBY says UNKNOWN."""
    assert _parse("induction.log") == [
        Finding("sby", "i.sv", 4, "FORMAL-INDUCTION", "error",
                "i: assertion c_bounded is not inductive (trace i/engine_0/trace_induct.vcd)")
    ]  # fmt: skip


def test_a_property_that_fails_outright_is_not_also_reported_as_not_inductive() -> None:
    assert [(f.rule, f.message) for f in _parse("prove_fail.log")] == [
        ("FORMAL-ASSERT", "g: assertion quiet_q fails (counterexample g/engine_0/trace.vcd)")
    ]


def test_a_tool_error_keeps_its_location() -> None:
    assert _parse("error.log") == [
        Finding("sby", "", 0, "FORMAL-ERROR", "error",
                "e: base: e.sv:1: ERROR: expected expression")
    ]  # fmt: skip


def test_a_job_without_a_verdict_fails() -> None:
    """SBY v0.69's own timeout option crashes it before the verdict (why run_sby enforces
    the time limit itself): every task without a DONE line is a finding."""
    assert [(f.rule, f.message) for f in _parse("crash.log")] == [
        ("TOOL-FAILED", "to_x_bmc: ended without a verdict line"),
        ("TOOL-FAILED", "to_x_cover: ended without a verdict line"),
    ]


@pytest.mark.parametrize(
    ("status", "rule"),
    [("TIMEOUT", "FORMAL-TIMEOUT"), ("UNKNOWN", "FORMAL-UNKNOWN"), ("FAIL", "FORMAL-UNKNOWN"),
     ("ERROR", "FORMAL-ERROR")],
)  # fmt: skip
def test_a_verdict_without_a_named_property_is_still_a_finding(status: str, rule: str) -> None:
    output = f"SBY 10:00:00 [j] engine_0: smtbmc yices\nSBY 10:00:01 [j] DONE ({status}, rc=1)\n"
    assert [f.rule for f in SymbiYosys().parse(output, {})] == [rule]


def test_version_of_the_pinned_tool() -> None:
    sby = SymbiYosys()
    assert sby.parse_version("SBY v0.69\n") == "0.69"
    assert sby.is_pinned("0.69") and not sby.is_pinned("0.70") and not sby.is_pinned(None)


def test_job_sources_map_destinations_to_the_run_directory(tmp_path: Path) -> None:
    job = tmp_path / "formal" / "x.sby"
    job.parent.mkdir()
    job.write_text("[options]\nmode bmc\n\n[files]\nrtl ../rtl\nx_reset.sv\n", encoding="utf-8")
    assert job_sources(job, tmp_path) == {"rtl": "rtl", "x_reset.sv": "formal/x_reset.sv"}


def test_sby_job_has_a_task_and_a_depth_per_mode() -> None:
    text = sby_job(top="t", files=["rtl/a.sv", "p.sv"], incdirs=["rtl/inc"],
                   depths={"bmc": 12, "cover": 30}, copy=[("rtl", "../rtl"), ("p.sv", "p.sv")])  # fmt: skip
    assert text == (
        "[tasks]\nbmc\ncover\n\n"
        "[options]\nbmc: mode bmc\nbmc: depth 12\ncover: mode cover\ncover: depth 30\n"
        "multiclock on\n\n"
        "[engines]\nsmtbmc yices\n\n"
        "[script]\nplugin -i slang\nread_slang -I rtl/inc --top t rtl/a.sv p.sv\nprep -top t\n\n"
        "[files]\nrtl ../rtl\np.sv\n"
    )


@pytest.mark.parametrize(
    ("depths", "message"),
    [({}, "formal modes must be some of bmc, prove, cover: none"),
     ({"sim": 3}, "formal modes must be some of bmc, prove, cover: ['sim']"),
     ({"bmc": 0}, "formal depth must be at least 1, not 0 (bmc)")],
)  # fmt: skip
def test_sby_job_refuses_bad_modes_and_depths(depths: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError) as exc:
        sby_job(top="t", files=[], incdirs=[], depths=depths, copy=[])  # type: ignore[arg-type]
    assert str(exc.value) == message


def test_prove_refuses_files_outside_the_run_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be relative to"):
        prove([Path("../a.sv")], "a", tmp_path, properties=[], depth=5)


def test_a_missing_sby_is_a_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hdl_bag.flows.formal.shutil.which", lambda _: None)
    job = tmp_path / "x.sby"
    job.write_text("[files]\n", encoding="utf-8")
    report = run_sby(job, tmp_path)
    assert not report.ok
    assert [(f.rule, f.message) for f in report.findings] == [
        ("TOOL-MISSING", "'sby' not found on PATH")
    ]


def test_run_formal_needs_generated_jobs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no generated formal"):
        run_formal(Outputs(tmp_path, (), "top", "x.f"))


def test_bag_formal_without_jobs_explains_why(tmp_path: Path) -> None:
    (tmp_path / "bag_outputs.json").write_text(
        '{"files": {}, "top": "t", "filelist": "t.f", "tests": null}\n', encoding="utf-8"
    )
    result = CliRunner().invoke(main, ["formal", str(tmp_path)])
    assert result.exit_code == 1
    assert "no generated formal (Generate formal (SymbiYosys) is N" in result.output
