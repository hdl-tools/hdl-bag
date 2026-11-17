# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""bag regress per seed (PR 5), with the EDA steps stubbed: the status and step a seed
records, D96's formal budget, the seed arithmetic, and the command line. The EDA run of the
same path is tests/regress/test_regress_eda.py."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main
from hdl_bag.flows import Finding, Report
from hdl_bag.ir import load
from hdl_bag.regress import run
from hdl_bag.regress.generate import random_config
from hdl_bag.regress.run import SeedResult, run_seed, seeds, summary
from hdl_bag.regress.space import dimensions

CONFIGS = Path(__file__).parents[1] / "configs"
# A seed whose config has generated tests and formal (no user-supplied fabric).
SEED = 3


def _ok(flow: str) -> Report:
    return Report(flow, (), ("stub",))


def _bad(flow: str, rule: str = "X-RULE", message: str = "broken") -> Report:
    return Report(flow, (Finding("stub", "", 0, rule, "error", message),), ("stub",))


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub every EDA step to pass, recording the calls in order."""
    seen: list[str] = []

    def stub(name: str) -> Callable[..., Report]:
        def f(*args: Any, **kwargs: Any) -> Report:
            seen.append(name if name != "formal" else f"formal {Path(args[0]).stem}")
            return _ok(name)

        return f

    for name, attr in (("lint", "lint"), ("compile", "compile_design"), ("sim", "run_tests"),
                       ("formal", "run_sby")):  # fmt: skip
        monkeypatch.setattr(run, attr, stub(name))
    return seen


def test_seed_j_of_a_run_is_base_times_span_plus_j_and_shards_split_by_j() -> None:
    assert seeds(20261117, 5) == [202611170000 + j for j in range(5)]
    assert seeds(7, 5, 1, 2) == [70000, 70002, 70004]
    assert seeds(7, 5, 2, 2) == [70001, 70003]
    assert sorted(seeds(7, 9, 1, 3) + seeds(7, 9, 2, 3) + seeds(7, 9, 3, 3)) == seeds(7, 9)


@pytest.mark.parametrize(("args", "message"), [
    ((0, 0), "--seeds must be 1 to 10000, not 0"),
    ((0, 10001), "--seeds must be 1 to 10000, not 10001"),
    ((0, 4, 3, 2), "--shard 3/2: the shard must be 1 to 2"),
    ((-1, 4), "--seed-base must be 0 or more, not -1"),
])  # fmt: skip
def test_a_bad_seed_range_is_refused(args: tuple[int, ...], message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        seeds(*args)


def test_a_passing_seed_runs_every_step_and_writes_its_repro(tmp_path: Path,
                                                             calls: list[str]) -> None:  # fmt: skip
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step) == ("pass", None)
    jobs = [c for c in calls if c.startswith("formal ")]
    assert calls == ["lint", "compile", "sim", *jobs]
    assert jobs[0].endswith("_decode")  # D96: the decode job first
    assert list(r.steps) == ["check", "resolve", "generate", "lint", "compile", "sim", *jobs]
    assert set(r.steps.values()) == {"pass"}
    name = f"rnd{SEED}"
    assert r.workbook == f"{SEED}/{name}.xlsx" and (tmp_path / r.workbook).is_file()
    assert r.ir == f"{SEED}/{name}.ir.yaml"
    assert load(tmp_path / r.ir).project.name == name
    assert r.repro == [f"bag regress --seed {SEED}", f"bag flow {SEED}/{name}.ir.yaml --formal"]
    saved = json.loads((tmp_path / str(SEED) / "result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "pass" and saved["dimensions"] == r.dimensions


def test_a_lint_finding_fails_the_seed_at_lint_and_stops_there(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "lint", lambda *a, **k: _bad("lint", message="unused signal"))
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step) == ("fail", "lint")
    assert r.findings == ["stub: error: unused signal [X-RULE]"]
    assert calls == []  # nothing after lint ran
    assert r.steps["lint"] == "fail" and "compile" not in r.steps


def test_a_formal_job_cut_off_by_the_budget_is_a_timeout_not_a_failure(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D96: the route jobs of a large config cannot finish in the budget; that is reported."""

    def sby(job: Path, root: Path, *, timeout: float | None = None) -> Report:
        if job.stem.endswith("_decode"):
            return _ok("formal")
        return _bad("formal", "FORMAL-TIMEOUT", f"{job.stem}: no verdict within {timeout:g} s")

    monkeypatch.setattr(run, "run_sby", sby)
    r = run_seed(SEED, tmp_path)
    assert r.status == "timeout" and r.step is not None and r.step.startswith("formal ")
    assert r.findings == []
    decode = next(s for s in r.steps if s.endswith("_decode"))
    assert r.steps[decode] == "pass"
    assert {v for s, v in r.steps.items() if s.startswith("formal ") and s != decode} == {"timeout"}


def test_formal_jobs_past_the_budget_are_skipped(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each stubbed job takes 400 s of a 600 s budget: the decode job and one more run."""
    clock = [0.0]
    monkeypatch.setattr(run, "monotonic", lambda: clock[0])

    def sby(job: Path, root: Path, *, timeout: float | None = None) -> Report:
        calls.append(f"formal {job.stem}")
        clock[0] += 400
        return _ok("formal")

    monkeypatch.setattr(run, "run_sby", sby)
    r = run_seed(SEED, tmp_path, budget=600)
    formal = {s: v for s, v in r.steps.items() if s.startswith("formal ")}
    assert len(formal) > 2  # the seed has more jobs than the budget reaches
    assert list(formal.values()) == ["pass", "pass"] + ["skipped"] * (len(formal) - 2)
    assert r.status == "pass"


def test_a_step_that_raises_fails_the_seed_at_that_step(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One broken seed must not stop a nightly shard: the exception becomes its finding."""

    def boom(*args: Any, **kwargs: Any) -> Report:
        raise RuntimeError("verilator crashed")

    monkeypatch.setattr(run, "compile_design", boom)
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step) == ("fail", "compile")
    assert r.findings == ["RuntimeError: verilator crashed"]
    assert (tmp_path / str(SEED) / "result.json").is_file()


def test_a_check_error_on_a_draw_is_a_generator_bug(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(seed: int) -> Any:
        cfg = random_config(seed)
        cfg.data.topology.append(["nowhere", cfg.data.fabrics[0][0], 9, None, None])
        return cfg

    monkeypatch.setattr(run, "random_config", broken)
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step) == ("generator", "check")
    assert any("nowhere" in f for f in r.findings)
    assert r.ir is None and calls == []


def test_the_summary_lists_failures_first_and_counts_each_status() -> None:
    rs = [SeedResult(1, "rnd1", repro=["bag regress --seed 1"]),
          SeedResult(2, "rnd2", "fail", "lint", repro=["bag regress --seed 2"]),
          SeedResult(3, "rnd3", "timeout", "formal x", repro=["bag regress --seed 3"])]  # fmt: skip
    data, text = summary(rs)
    assert [s["seed"] for s in json.loads(data)["seeds"]] == [2, 3, 1]
    assert json.loads(data)["counts"] == {"generator": 0, "fail": 1, "timeout": 1, "pass": 1}
    assert text.splitlines()[2] == "0 generator, 1 fail, 1 timeout, 1 pass of 3 seed(s)"
    assert "| 2 | fail | lint | `bag regress --seed 2` |" in text


def _cli(monkeypatch: pytest.MonkeyPatch, statuses: dict[int, str], *args: str) -> Any:
    def fake(seed: int, run_dir: Path, budget: float = 600, formal: bool = True) -> SeedResult:
        return SeedResult(seed, f"rnd{seed}", statuses.get(seed, "pass"),  # type: ignore[arg-type]
                          repro=[f"bag regress --seed {seed}"])  # fmt: skip

    monkeypatch.setattr(run, "run_seed", fake)
    return CliRunner().invoke(main, ["regress", *args])


def test_bag_regress_runs_its_shard_and_fails_only_on_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "r"
    got = _cli(monkeypatch, {10001: "timeout"}, "--seeds", "4", "--seed-base", "1",
               "--shard", "2/2", "-o", str(out))  # fmt: skip
    assert got.exit_code == 0, got.output
    assert got.output.splitlines() == [
        "seed 10001: timeout",
        "seed 10003: pass",
        "0 generator, 0 fail, 1 timeout, 1 pass of 2 seed(s)",
    ]
    assert (out / "summary.json").is_file() and (out / "summary.md").is_file()
    got = _cli(monkeypatch, {5: "fail"}, "--seed", "5", "-o", str(out))
    assert got.exit_code == 1


@pytest.mark.parametrize(("args", "message"), [
    ((), "give --seeds N or --seed S"),
    (("--seeds", "2", "--seed", "3"), "give --seeds N or --seed S"),
    (("--seeds", "2", "--shard", "3/2"), "--shard 3/2: the shard must be 1 to 2"),
    (("--seeds", "2", "--shard", "x"), "'x' is not i/k, e.g. 2/4"),
])  # fmt: skip
def test_bag_regress_refuses_a_bad_selection(
    monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...], message: str
) -> None:
    got = _cli(monkeypatch, {}, *args)
    assert got.exit_code == 2 and message in got.output


def test_the_dimensions_of_a_corpus_config() -> None:
    """bridges_pulp: three PULP bridges (two to APB, one AXI4 to AXI4-Lite, 64 to 32 bits)
    and a 64-bit AXI4-Lite master on the 64-bit crossbar (a protocol adapter only)."""
    spec = load(CONFIGS / "p3" / "bridges_pulp.ir.yaml")
    assert dimensions(spec) == {
        "fabrics": 4, "masters": 3, "slaves": 5,
        "bridges": ["bag_axi2apb", "bag_axi2apb", "bag_axi2axil"],
        "topologies": ["APB3 decoder-mux", "APB4 decoder-mux", "AXI4 crossbar",
                       "AXI4-Lite crossbar"],
        "protocol_pairs": ["AXI4-Lite>APB3", "AXI4-Lite~AXI4", "AXI4>APB4", "AXI4>AXI4-Lite"],
        "width_ratios": ["64:32"],
        "domains": 2, "clock_crossings": 1, "map": "sparse", "aliases": 0, "secure": False,
        "access_rules": 0, "hierarchy_depth": 0, "user_ports": 0, "port_options": [],
        "external": False, "port_style": "flat",
    }  # fmt: skip
