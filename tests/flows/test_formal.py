# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The formal flow without EDA tools: SBY output parsing (on captured output), job files,
versions, process handling (with a stand-in sby on POSIX), the CLI, and the errors a caller
can hit."""

import os
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main
from hdl_bag.flows.base import Finding, Report
from hdl_bag.flows.formal import (
    UNSAT,
    SymbiYosys,
    bounded_notes,
    job_sources,
    job_tasks,
    prove,
    run_formal,
    run_sby,
    sby_job,
)
from hdl_bag.gen import Outputs

DATA = Path(__file__).parent / "data" / "sby"
SOURCES = {
    "rtl": "rtl",
    "apb4_min_reset.sv": "formal/apb4_min_reset.sv",
    "bridges_pulp_cdc_reset.sv": "formal/bridges_pulp_cdc_reset.sv",
}
COVER = "not reached within the search depth, so the assertion it guards may hold only vacuously"
posix = pytest.mark.skipif(sys.platform == "win32", reason="a shell-script sby needs POSIX")


def _parse(name: str, job_dir: str = "", **kw: object) -> list[Finding]:
    text = (DATA / name).read_text(encoding="utf-8")
    return SymbiYosys().parse(text, SOURCES, job_dir, **kw)  # type: ignore[arg-type]


def test_a_passing_job_has_no_findings() -> None:
    """Both tasks of a job (bmc and cover) pass with every cover reached; and a k-induction
    proof."""
    assert _parse("pass.log", job_dir="formal") == []
    assert _parse("prove_pass.log") == []


def test_a_failing_assertion_names_the_property_its_file_and_trace() -> None:
    assert _parse("fail.log", job_dir="formal") == [
        Finding("sby", "f.sv", 8, "FORMAL-COVER", "error", f"f_cvr: cover check_13 {COVER}"),
        Finding("sby", "f.sv", 7, "FORMAL-ASSERT", "error",
                "f_bmc: assertion check_10 fails (counterexample formal/f_bmc/engine_0/trace.vcd)"),
    ]  # fmt: skip


def test_an_unreached_cover_is_a_finding() -> None:
    """An unreached cover means the assertions it guards may hold vacuously."""
    assert _parse("cover_unreached.log") == [
        Finding("sby", "formal/bridges_pulp_cdc_reset.sv", 72, "FORMAL-COVER", "error",
                f"bridges_pulp_cdc_reset_cover: cover active_uart_apb4_psel {COVER}")
    ]  # fmt: skip


def test_the_complete_summary_replaces_the_shortened_one_in_the_log() -> None:
    """SBY's log lists only some traces and names its status file for the rest: every
    unreached cover there is a finding, whatever the log listed."""
    full = (
        "engine_0 (smtbmc yices) returned FAIL\n"
        "unreached cover statements:\n"
        "  top.\\u_p.reset_mid_a at bridges_pulp_cdc_reset.sv:10.15-10.40\n"
        "  top.\\u_p.reset_mid_b at bridges_pulp_cdc_reset.sv:20.15-20.40\n"
    )
    findings = _parse("cover_unreached.log", summaries={"bridges_pulp_cdc_reset_cover": full})
    assert [(f.line, f.message.split(": ", 1)[1]) for f in findings] == [
        (10, f"cover reset_mid_a {COVER}"),
        (20, f"cover reset_mid_b {COVER}"),
    ]


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


def test_unsatisfiable_assumptions_are_named() -> None:
    """SBY ends such a run in ERROR with no ERROR: line; the message says what happened."""
    assert [(f.rule, f.message) for f in _parse("unsat.log")] == [
        ("FORMAL-ERROR", f"v_prove: {UNSAT}"),
        ("FORMAL-ERROR", f"v_bmc: {UNSAT}"),
    ]


def test_a_job_without_a_verdict_fails() -> None:
    """SBY v0.69's own timeout option crashes it before the verdict (why run_sby enforces
    the time limit itself): every task without a DONE line is a finding."""
    assert [(f.rule, f.message) for f in _parse("crash.log")] == [
        ("TOOL-FAILED", "to_x_bmc: ended without a verdict line"),
        ("TOOL-FAILED", "to_x_cover: ended without a verdict line"),
    ]


@pytest.mark.parametrize(
    ("output", "expected", "found"),
    [("", (), [("TOOL-FAILED", "sby gave no verdict")]),
     ("sby: a format this parser does not know\n", (), [("TOOL-FAILED", "sby gave no verdict")]),
     ("SBY 10:00:00 [j_prove] DONE (PASS, rc=0)\n", ("j_prove", "j_cover"),
      [("TOOL-FAILED", "j_cover: no output for this task")])],
)  # fmt: skip
def test_output_without_every_verdict_never_passes(
    output: str, expected: tuple[str, ...], found: list[tuple[str, str]]
) -> None:
    findings = SymbiYosys().parse(output, {}, expected=expected)
    assert [(f.rule, f.message) for f in findings] == found


def test_lines_before_ten_in_the_morning_are_read() -> None:
    """SBY pads the hour with a space, not a zero: "SBY  0:07:46 [job] ...". Such a run
    must not lose every line (it did, and gave TOOL-FAILED for a passing job)."""
    output = (
        "SBY  0:07:45 [j] summary:   failed assertion top.\\u_p.quiet_x at p.sv:9.15-9.40\n"
        "SBY  0:07:46 [j] DONE (FAIL, rc=2)\n"
    )
    assert [(f.rule, f.line) for f in SymbiYosys().parse(output, {}, expected=("j",))] == [
        ("FORMAL-ASSERT", 9)
    ]


@pytest.mark.parametrize(
    ("status", "rule", "message"),
    [("TIMEOUT", "FORMAL-TIMEOUT", "j: ran out of time"),
     ("UNKNOWN", "FORMAL-UNKNOWN", "j: ended UNKNOWN (not proved)"),
     ("FAIL", "FORMAL-UNKNOWN", "j: failed without naming a property"),
     ("ERROR", "FORMAL-ERROR", "j: the tool stopped on an error")],
)  # fmt: skip
def test_a_verdict_without_a_named_property_is_still_a_finding(
    status: str, rule: str, message: str
) -> None:
    output = f"SBY 10:00:00 [j] engine_0: smtbmc yices\nSBY 10:00:01 [j] DONE ({status}, rc=1)\n"
    assert [(f.rule, f.message) for f in SymbiYosys().parse(output, {})] == [(rule, message)]


def test_version_of_the_pinned_tool() -> None:
    sby = SymbiYosys()
    assert sby.parse_version("SBY v0.69\n") == "0.69"
    assert sby.is_pinned("0.69") and not sby.is_pinned("0.70") and not sby.is_pinned(None)


def test_job_sources_and_tasks(tmp_path: Path) -> None:
    job = tmp_path / "formal" / "x.sby"
    job.parent.mkdir()
    job.write_text("[tasks]\nprove\ncover\n\n[options]\nprove: mode prove\n\n[files]\n"
                   "rtl ../rtl\nx_reset.sv\n", encoding="utf-8")  # fmt: skip
    assert job_sources(job, tmp_path) == {"rtl": "rtl", "x_reset.sv": "formal/x_reset.sv"}
    assert job_tasks(job) == ["x_prove", "x_cover"]
    job.write_text("[options]\nmode bmc\n", encoding="utf-8")
    assert job_tasks(job) == ["x"]


def test_sby_job_has_a_task_and_a_depth_per_mode() -> None:
    text = sby_job(top="t", files=["rtl/a.sv", "p.sv"], incdirs=["rtl/inc"],
                   depths={"prove": 6, "cover": 30}, copy=[("rtl", "../rtl"), ("p.sv", "p.sv")])  # fmt: skip
    assert text == (
        "[tasks]\nprove\ncover\n\n"
        "[options]\nprove: mode prove\nprove: depth 6\ncover: mode cover\ncover: depth 30\n"
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
    assert [(f.rule, f.message) for f in report.findings] == [
        ("TOOL-MISSING", "'sby' not found on PATH")
    ]


def test_a_missing_job_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        run_sby(tmp_path / "gone.sby", tmp_path)
    assert (
        str(exc.value)
        == f"{tmp_path / 'gone.sby'}: no such SymbiYosys job (run bag generate again)"
    )
    with pytest.raises(ValueError) as exc:
        run_formal(Outputs(tmp_path, (), "top", "x.f", formal=("formal/x_reset.sby",)))
    assert str(exc.value) == f"{tmp_path}: missing formal/x_reset.sby (run bag generate again)"


def test_run_formal_needs_generated_jobs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no generated formal"):
        run_formal(Outputs(tmp_path, (), "top", "x.f"))


# ---------------------------------------------------------------- a stand-in sby (POSIX)
_TASKS = "[tasks]\nprove\ncover\n\n[files]\n"
_PASS = (
    "echo 'SBY 10:00:00 [x_prove] DONE (PASS, rc=0)'\n"
    "echo 'SBY 10:00:01 [x_cover] DONE (PASS, rc=0)'\n"
)


def _fake_sby(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str,
              version: str = "0.69") -> Path:  # fmt: skip
    """A sby on PATH that runs BODY (sh) for a job, and reports VERSION; returns the job."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sby = bin_dir / "sby"
    sby.write_text(f'#!/bin/sh\nif [ "$1" = --version ]; then echo "SBY v{version}"; exit 0; fi\n'
                   f"{body}", encoding="utf-8")  # fmt: skip
    sby.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    job = tmp_path / "x.sby"
    job.write_text(_TASKS, encoding="utf-8")
    return job


@posix
def test_a_clean_exit_with_no_verdict_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _fake_sby(tmp_path, monkeypatch, "exit 0\n")
    assert [(f.rule, f.message) for f in run_sby(job, tmp_path).findings] == [
        ("TOOL-FAILED", "x_cover: no output for this task"),
        ("TOOL-FAILED", "x_prove: no output for this task"),
    ]


@posix
def test_a_failed_exit_names_its_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _fake_sby(tmp_path, monkeypatch, _PASS + "echo 'Traceback: boom'\nexit 3\n")
    assert [(f.rule, f.message) for f in run_sby(job, tmp_path).findings] == [
        ("TOOL-FAILED", "x: exit status 3: Traceback: boom")
    ]


@posix
def test_another_sby_version_is_noted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _fake_sby(tmp_path, monkeypatch, _PASS, version="0.70")
    report = run_sby(job, tmp_path)
    assert report.ok and report.notes == ("sby 0.70 is not the pinned v0.69",)


@posix
def test_a_timeout_stops_sby_and_everything_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SBY runs Yosys and the solvers as children: a timeout must stop them too, not leave
    them running."""
    pid_file = tmp_path / "child.pid"
    job = _fake_sby(tmp_path, monkeypatch, "echo 'SBY 10:00:00 [x_prove] engine_0: ##   0:00:00  "
                    f"Checking assertions in step 3..'\nsleep 300 &\necho $! > {pid_file}\nwait\n")  # fmt: skip
    report = run_sby(job, tmp_path, timeout=2)
    assert [(f.rule, f.message) for f in report.findings] == [
        ("FORMAL-TIMEOUT", "x: no verdict within 2 s, reached step 3 (allow more with --timeout)")
    ]
    child = int(pid_file.read_text())
    for _ in range(50):  # SIGKILL is delivered asynchronously
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        try:  # the entry vanishes once the child is reaped, maybe between two reads
            state = Path(f"/proc/{child}/stat").read_text().split()[2]
        except FileNotFoundError:
            break
        if state == "Z":
            break  # a zombie: dead, waiting for its parent (gone) to be reaped
        time.sleep(0.1)
    else:
        pytest.fail(f"sby's child {child} is still running after the timeout")


@posix
def test_one_task_of_a_job_runs_on_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The formal corpus runs each task of a job as a test of its own (D76), so SBY is asked
    for that task only, and only its verdict is expected."""
    args = tmp_path / "args"
    job = _fake_sby(tmp_path, monkeypatch, f'echo "$@" > {args}\n'
                    "echo 'SBY 10:00:01 [x_cover] DONE (PASS, rc=0)'\n")  # fmt: skip
    report = run_sby(job, tmp_path, task="cover")
    assert report.ok, report.format()
    assert args.read_text(encoding="utf-8") == "-f x.sby cover\n"


def test_a_task_the_job_lacks_is_a_clear_error(tmp_path: Path) -> None:
    job = tmp_path / "x.sby"
    job.write_text(_TASKS, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        run_sby(job, tmp_path, task="bmc")
    assert str(exc.value) == f"{job}: no task bmc (it has prove, cover)"


def test_a_bmc_task_on_its_own_gets_only_its_note(tmp_path: Path) -> None:
    job = tmp_path / "x.sby"
    job.write_text("[tasks]\nbmc\ncover\n\n[options]\nbmc: mode bmc\nbmc: depth 16\n"
                   "cover: mode cover\ncover: depth 16\n\n[files]\n", encoding="utf-8")  # fmt: skip
    assert bounded_notes(job, task="cover") == []
    assert bounded_notes(job, task="bmc") == ["x_bmc: bounded check to depth 16 (bmc), not a proof"]


# ---------------------------------------------------------------- CLI
def test_bag_formal_without_jobs_explains_why(tmp_path: Path) -> None:
    (tmp_path / "bag_outputs.json").write_text(
        '{"files": {}, "top": "t", "filelist": "t.f", "tests": null}\n', encoding="utf-8"
    )
    result = CliRunner().invoke(main, ["formal", str(tmp_path)])
    assert result.exit_code == 1
    assert "no generated formal (Generate formal (SymbiYosys) is N" in result.output


def _stub_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[float | None]:
    """bag flow with every step stubbed out; returns the timeouts formal was run with."""
    out = Outputs(tmp_path, (), "t", "t.f", formal=("formal/t_reset.sby",))
    ok = Report("lint", (), ("x",))
    calls: list[float | None] = []

    def formal(_: Outputs, timeout: float | None = None) -> Report:
        calls.append(timeout)
        return Report("formal", (), ("sby",))

    monkeypatch.setattr("hdl_bag.cli._generate", lambda *_: out)
    monkeypatch.setattr("hdl_bag.cli.sources", lambda _: [])
    monkeypatch.setattr("hdl_bag.cli.include_dirs", lambda _: [])
    monkeypatch.setattr("hdl_bag.cli.lint", lambda *_, **__: ok)
    monkeypatch.setattr("hdl_bag.cli.compile_design", lambda *_, **__: ok)
    monkeypatch.setattr("hdl_bag.cli.run_formal", formal)
    return calls


def test_bag_flow_runs_formal_only_when_asked(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """Formal is opt-in in bag flow (owner, PR 1): a large design's proofs take minutes."""
    calls = _stub_flow(tmp_path, monkeypatch)
    spec = tmp_path / "s.xlsx"
    spec.write_bytes(b"")
    assert CliRunner().invoke(main, ["flow", str(spec)]).exit_code == 0
    assert calls == []
    result = CliRunner().invoke(main, ["flow", str(spec), "--formal", "--formal-timeout", "30"])
    assert result.exit_code == 0, result.output
    assert calls == [30.0]


def test_a_formal_timeout_needs_formal(tmp_path: Path) -> None:
    spec = tmp_path / "s.xlsx"
    spec.write_bytes(b"")
    result = CliRunner().invoke(main, ["flow", str(spec), "--formal-timeout", "30"])
    assert result.exit_code == 2
    assert "--formal-timeout needs --formal" in result.output


def test_a_bmc_task_is_reported_as_bounded_not_proved(tmp_path: Path) -> None:
    """A bmc pass says only that no assertion fails within its depth; the report says so,
    per task. prove and cover tasks get no such note."""
    job = tmp_path / "x.sby"
    job.write_text("[tasks]\nprove\nbmc\ncover\n\n[options]\nprove: mode prove\nprove: depth 6\n"
                   "bmc: mode bmc\nbmc: depth 16\ncover: mode cover\ncover: depth 16\n"
                   "multiclock on\n\n[files]\n", encoding="utf-8")  # fmt: skip
    assert bounded_notes(job) == ["x_bmc: bounded check to depth 16 (bmc), not a proof"]
    job.write_text("[options]\nmode bmc\ndepth 9\n\n[files]\n", encoding="utf-8")
    assert bounded_notes(job) == ["x: bounded check to depth 9 (bmc), not a proof"]
