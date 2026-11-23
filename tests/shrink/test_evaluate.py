# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The oracle of `bag shrink` with the EDA steps stubbed. Everything up to the step is real:
the workbook is written, read, checked, resolved and generated from the `multi` corpus config."""

import copy
import os
from pathlib import Path
from typing import Any

import pytest

from hdl_bag import ir
from hdl_bag.flows import Finding, Report
from hdl_bag.flows.sim import SEED
from hdl_bag.gen import Outputs
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.model import Provenance
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.shrink import evaluate
from hdl_bag.shrink.evaluate import (
    ShrinkError,
    Signature,
    build,
    discover,
    formal_step,
    lint_step,
    make_oracle,
)
from hdl_bag.template.data import SpecData

SPEC_DIR = Path(__file__).parents[1] / "configs" / "p2"
MULTI = SPEC_DIR / "multi.ir.yaml"


def multi() -> SpecData:
    return to_spec_data(ir.load(MULTI))


def report(flow: str, *found: tuple[str, str]) -> Report:
    return Report(flow, tuple(Finding("t", "", 0, r, "error", m) for r, m in found), ("t",))


@pytest.fixture
def built(tmp_path: Path) -> tuple[ir.Spec, Outputs]:
    result = build(multi(), tmp_path / "base", SPEC_DIR)
    assert not isinstance(result, str), result
    return result


def never(_spec: ir.Spec, _out: Outputs) -> Signature | None:
    raise AssertionError("the step must not run")


def test_a_candidate_that_fails_bag_check_is_invalid_without_running_the_step(
    tmp_path: Path,
) -> None:
    data = multi()
    del data.masters[0]  # the topology still names it
    counts: dict[str, int] = {}
    oracle = make_oracle(tmp_path / "w", SPEC_DIR, never, Signature(frozenset(), None), counts)
    assert oracle(data) is None
    assert counts == {"check": 1}


def test_the_file_header_is_found_next_to_the_ir(tmp_path: Path) -> None:
    """Wrapper > File header resolves against spec_dir, so work (which has no header) fails."""
    assert not isinstance(build(multi(), tmp_path / "a", SPEC_DIR), str)
    assert build(multi(), tmp_path / "b", tmp_path / "b") == "check"


def lint_reports(monkeypatch: pytest.MonkeyPatch, *found: tuple[str, str]) -> None:
    monkeypatch.setattr(evaluate, "lint", lambda *a, **k: report("lint", *found))


def test_the_same_rules_with_another_message_still_reproduce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_reports(monkeypatch, ("A", "line 10"), ("B", "line 99"))
    base = Signature(frozenset({"A", "B"}), None)
    assert make_oracle(tmp_path / "w", SPEC_DIR, lint_step(), base, {})(multi()) is True


def test_a_different_rule_set_does_not_reproduce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_reports(monkeypatch, ("A", "x"), ("C", "y"))
    base = Signature(frozenset({"A", "B"}), None)
    assert make_oracle(tmp_path / "w", SPEC_DIR, lint_step(), base, {})(multi()) is False


def test_a_candidate_resolving_to_the_same_ir_is_not_run_twice(tmp_path: Path) -> None:
    runs: list[int] = []

    def step(_spec: ir.Spec, _out: Outputs) -> Signature | None:
        runs.append(1)
        return None

    oracle = make_oracle(tmp_path / "w", SPEC_DIR, step, Signature(frozenset(), None), {})
    assert oracle(multi()) is False
    assert oracle(copy.deepcopy(multi())) is False
    assert len(runs) == 1


def test_the_memo_key_ignores_provenance(built: tuple[ir.Spec, Outputs]) -> None:
    spec = built[0]
    other = spec.model_copy(
        update={"provenance": Provenance(bag_version="x", source="/elsewhere", sha256="0")}
    )
    assert evaluate._key(spec) == evaluate._key(other)


def test_the_failing_sim_test_is_named_from_the_test_list_not_parsed(
    built: tuple[ir.Spec, Outputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With tests `a` and `ab`, the message "ab failed" must not be read as test `a` failing."""
    monkeypatch.setattr(evaluate, "cocotb_tests", lambda *_a: ["a", "ab"])
    filters: list[str | None] = []

    def run_tests(_out: Outputs, *, test_filter: str | None = None, seed: int = SEED) -> Report:
        filters.append(test_filter)
        return report("sim", ("TEST-FAILED", f"ab failed (seed {seed})"))

    monkeypatch.setattr(evaluate, "run_tests", run_tests)
    step, sig = discover("sim", *built, None)
    assert sig == Signature(frozenset({"TEST-FAILED"}), "ab")
    assert filters == [None, r"\.ab$"]
    assert step(*built) == sig


def test_formal_discovery_takes_the_first_failing_task_in_order(
    built: tuple[ir.Spec, Outputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = [("formal/x_a.sby", "t1"), ("formal/x_b.sby", "t2"), ("formal/x_b.sby", "t3")]
    monkeypatch.setattr(evaluate, "formal_tasks", lambda _out: jobs)
    ran: list[str | None] = []

    def run_sby(_job: Path, _root: Path, *, timeout: float | None, task: str | None) -> Report:
        ran.append(task)
        return report("formal") if task == "t1" else report("formal", ("FORMAL-FAIL", "m"))

    monkeypatch.setattr(evaluate, "run_sby", run_sby)
    _, sig = discover("formal", built[0], built[1], 5.0)
    assert sig == Signature(frozenset({"FORMAL-FAIL"}), "x_b:t2")
    assert ran == ["t1", "t2"]


def test_a_missing_tool_is_refused_with_its_message(
    built: tuple[ir.Spec, Outputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_reports(monkeypatch, ("TOOL-MISSING", "slang not found"))
    with pytest.raises(ShrinkError) as err:
        discover("lint", *built, None)
    assert str(err.value) == (
        "lint reports TOOL-MISSING: a tool is not installed, so every candidate would "
        "'reproduce'. Install it (or run in the tools image) and try again"
    )


def test_a_timeout_only_failure_is_refused(
    built: tuple[ir.Spec, Outputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evaluate, "formal_tasks", lambda _out: [("formal/x_a.sby", None)])
    monkeypatch.setattr(
        evaluate, "run_sby", lambda *a, **k: report("formal", ("FORMAL-TIMEOUT", "slow"))
    )
    with pytest.raises(ShrinkError) as err:
        discover("formal", *built, 1.0)
    assert str(err.value) == (
        "formal x_a only ran out of time (FORMAL-TIMEOUT), which is not deterministic and so "
        "cannot be shrunk. Raise --timeout or pick a task that fails"
    )


def test_a_passing_baseline_is_refused(
    built: tuple[ir.Spec, Outputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    lint_reports(monkeypatch)
    with pytest.raises(ShrinkError, match="lint does not fail on the original spec"):
        discover("lint", *built, None)


def test_a_vanished_formal_task_does_not_reproduce(
    built: tuple[ir.Spec, Outputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    step = formal_step("x_b", "t2", None)
    monkeypatch.setattr(evaluate, "formal_tasks", lambda _out: [("formal/x_b.sby", "t3")])
    assert step(*built) is None  # the job no longer has the task
    monkeypatch.setattr(evaluate, "formal_tasks", lambda _out: [("formal/x_b.sby", "t2")])

    def gone(*_a: Any, **_k: Any) -> Report:
        raise ValueError("no such task")

    monkeypatch.setattr(evaluate, "run_sby", gone)
    assert step(*built) is None


def test_a_candidate_that_crashes_is_counted_and_does_not_end_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(evaluate, "build_workbook", boom)
    counts: dict[str, int] = {}
    oracle = make_oracle(tmp_path / "w", SPEC_DIR, never, Signature(frozenset(), None), counts)
    assert oracle(multi()) is None
    assert oracle(multi()) is None
    assert counts == {"crash": 2}


def test_build_returns_the_reason_a_candidate_was_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = multi()
    del broken.masters[0]
    assert build(broken, tmp_path / "w", SPEC_DIR) == "check"

    def raises(exc: Exception) -> Any:
        def f(*_a: Any, **_k: Any) -> None:
            raise exc

        return f

    with monkeypatch.context() as m:
        m.setattr(ir, "resolve", raises(ir.ResolveError("x")))
        assert build(multi(), tmp_path / "w", SPEC_DIR) == "resolve"
    with monkeypatch.context() as m:
        m.setattr(evaluate, "generate", raises(GenerateError("x")))
        assert build(multi(), tmp_path / "w", SPEC_DIR) == "generate"
    with monkeypatch.context() as m:
        m.setattr(evaluate, "generate", raises(RuntimeError("x")))
        assert build(multi(), tmp_path / "w", SPEC_DIR) == "crash"


@pytest.mark.parametrize("form", ["absolute", "relative"])
def test_a_project_name_cannot_choose_where_the_candidate_workbook_is_written(
    tmp_path: Path, form: str
) -> None:
    """build() writes the candidate workbook before run_checks can reject its name, so the name
    must reach no path. A name that named an existing file overwrote it.

    Both forms, because a fix that refused only absolute names would still be escaped by a
    relative one. This is the place to test that: the work directory is known here, while the
    CLI builds its workbook in a TemporaryDirectory whose path no test can predict.
    """
    victim = tmp_path / "victim.xlsx"
    victim.write_bytes(b"a workbook of the user's own")
    work = tmp_path / "w"
    name = (
        str(tmp_path / "victim")
        if form == "absolute"
        else os.path.relpath(tmp_path / "victim", work)
    )
    data = multi()
    data.project["Project name"] = name
    assert build(data, work, SPEC_DIR) == "check"
    assert victim.read_bytes() == b"a workbook of the user's own"
