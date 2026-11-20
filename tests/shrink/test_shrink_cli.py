# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""`bag shrink` end to end on the `multi` corpus config, with lint stubbed: it fails while the
generated RTL still mentions the master `dma`, so everything that can go without it goes."""

import hashlib
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from hdl_bag import __version__, ir
from hdl_bag.cli import main
from hdl_bag.flows import Finding, Report, SimUnavailable
from hdl_bag.gen import Outputs
from hdl_bag.shrink import Signature, evaluate

P2 = Path(__file__).parents[1] / "configs" / "p2"


@pytest.fixture
def spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of `multi` (with its header) in a fresh directory that is also the cwd, so the
    paths the command prints are relative and the same on every run."""
    shutil.copy(P2 / "multi.ir.yaml", tmp_path / "multi.ir.yaml")
    shutil.copy(P2 / "header.txt", tmp_path / "header.txt")
    monkeypatch.chdir(tmp_path)
    return tmp_path / "multi.ir.yaml"


def lint_fails_while_rtl_names(monkeypatch: pytest.MonkeyPatch, word: str) -> None:
    def lint(files: list[Path], *_a: object, **_k: object) -> Report:
        hit = any(word in f.read_text() for f in files)
        found = (Finding("t", "", 0, "SLANG-FOO", "error", "boom"),) if hit else ()
        return Report("lint", found, ("t",))

    monkeypatch.setattr(evaluate, "lint", lint)


def shrink(*args: str) -> Result:
    return CliRunner().invoke(main, ["shrink", *args])


def test_shrink_prints_each_kept_edit_and_writes_the_ir(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_fails_while_rtl_names(monkeypatch, "dma")
    result = shrink("multi.ir.yaml")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[:5] == [
        "baseline: lint fails with SLANG-FOO",
        "kept: drop fabric ahb_s and its ports",
        "kept: drop fabric p3 and its ports",
        "kept: drop fabric p4 and its ports",
        "kept: drop master cpu",
    ]
    assert "kept: drop master dma" not in lines
    assert lines[-3:] == [
        "2 pass(es), 22 run(s); kept 21 edit(s); 19 candidate(s) failed bag check",
        "wrote multi.shrunk.ir.yaml",
        "reproduce: bag flow multi.shrunk.ir.yaml",
    ]
    shrunk = ir.load(spec.with_name("multi.shrunk.ir.yaml"))
    assert [m.name for m in shrunk.masters] == ["dma"]


def test_shrink_output_is_byte_identical_across_two_runs(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_fails_while_rtl_names(monkeypatch, "dma")
    first = shrink("multi.ir.yaml", "-o", "a.ir.yaml")
    second = shrink("multi.ir.yaml", "-o", "b.ir.yaml")
    assert first.output.replace("a.ir.yaml", "X") == second.output.replace("b.ir.yaml", "X")
    assert Path("a.ir.yaml").read_bytes() == Path("b.ir.yaml").read_bytes()


def test_the_written_ir_names_the_original_spec_and_not_a_temp_path(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_fails_while_rtl_names(monkeypatch, "dma")
    assert shrink("multi.ir.yaml").exit_code == 0
    text = Path("multi.shrunk.ir.yaml").read_text()
    assert str(spec.parent) not in text and "/tmp" not in text and "/var/" not in text
    prov = ir.load(Path("multi.shrunk.ir.yaml")).provenance
    assert prov.bag_version == __version__
    assert prov.source == "multi.ir.yaml"
    assert prov.sha256 == hashlib.sha256(spec.read_bytes()).hexdigest()


def test_a_spec_that_does_not_fail_exits_1_and_says_so(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_fails_while_rtl_names(monkeypatch, "no such text")
    result = shrink("multi.ir.yaml", "--step", "lint")
    assert result.exit_code == 1
    assert "error: lint does not fail on the original spec" in result.output
    assert not Path("multi.shrunk.ir.yaml").exists()


def test_an_unknown_step_is_a_usage_error(spec: Path) -> None:
    result = shrink("multi.ir.yaml", "--step", "synth")
    assert result.exit_code == 2
    assert "'synth' is not lint, sim, formal or test=<name>" in result.output


def test_a_workbook_argument_is_refused(tmp_path: Path) -> None:
    book = tmp_path / "spec.xlsx"
    book.write_bytes(b"")
    result = shrink(str(book))
    assert result.exit_code == 2
    assert "is not an IR: run bag ir on the workbook first" in result.output


def test_the_budget_message_appears_only_when_exhausted(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_fails_while_rtl_names(monkeypatch, "dma")
    short = shrink("multi.ir.yaml", "--budget", "2")
    assert short.exit_code == 0, short.output
    assert "stopped at the budget of 2 runs; the result may shrink further (--budget)" in (
        short.output
    )
    assert "stopped at the budget" not in shrink("multi.ir.yaml").output


def sim_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_sim(*_a: object, **_k: object) -> Report:
        raise SimUnavailable("cocotb is not installed")

    monkeypatch.setattr(evaluate, "_run_sim", run_sim)


def formal_outcome(monkeypatch: pytest.MonkeyPatch, *, fails_while_rtl_names: str | None) -> None:
    """Stub formal discovery: it fails on `multi` while the RTL names the word, or passes."""
    sig = Signature(frozenset({"FORMAL-FAIL"}), "arb:prove")

    def step(_spec: object, out: Outputs) -> Signature | None:
        root = out.root.resolve()
        hit = any(str(fails_while_rtl_names) in f.read_text() for f in root.rglob("*.sv"))
        return sig if hit else None

    def discover_formal(*_a: object, **_k: object) -> tuple[evaluate.Step, Signature]:
        if fails_while_rtl_names is None:
            raise evaluate.NoFailure("formal does not fail on the original spec")
        return step, sig

    monkeypatch.setattr(evaluate, "_discover_formal", discover_formal)


def test_an_unavailable_simulator_does_not_stop_the_search_reaching_formal(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_fails_while_rtl_names(monkeypatch, "no such text")
    sim_unavailable(monkeypatch)
    formal_outcome(monkeypatch, fails_while_rtl_names="dma")
    result = shrink("multi.ir.yaml")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "baseline: formal arb:prove fails with FORMAL-FAIL"
    assert Path("multi.shrunk.ir.yaml").exists()


def test_asking_for_sim_explicitly_still_reports_that_it_is_unavailable(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sim_unavailable(monkeypatch)
    result = shrink("multi.ir.yaml", "--step", "sim")
    assert result.exit_code == 1
    assert "error: cannot run the simulation: cocotb is not installed. Install cocotb" in (
        result.output
    )


def test_a_step_that_could_not_be_assessed_is_not_reported_as_passing(
    spec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_fails_while_rtl_names(monkeypatch, "no such text")
    sim_unavailable(monkeypatch)
    formal_outcome(monkeypatch, fails_while_rtl_names=None)
    result = shrink("multi.ir.yaml")
    assert result.exit_code == 1
    assert (
        "error: lint and formal pass on the spec, but sim could not be assessed "
        "(cannot run the simulation: cocotb is not installed. Install cocotb): "
        "nothing to shrink in what ran, so install what is missing and run again"
    ) in result.output
    assert "all pass" not in result.output
