# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""bag regress per seed (PR 5), with the EDA steps stubbed: the status and step a seed
records, D96's formal budget per task, the seed arithmetic, the summary and the command
line. The EDA run of the same path is tests/regress/test_regress_eda.py."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main
from hdl_bag.flows import Finding, Report, SimUnavailable
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir import load
from hdl_bag.regress import run
from hdl_bag.regress.generate import random_config
from hdl_bag.regress.run import SeedResult, run_seed, seed_for_sim, seeds, summary, write_summary
from hdl_bag.regress.space import dimensions

CONFIGS = Path(__file__).parents[1] / "configs"
# A seed whose config has generated tests and formal (no user-supplied fabric).
SEED = 3
# A seed whose config has a user-supplied fabric: no generated tests or formal (D8).
EXTERNAL = next(s for s in range(200)
                if any(r[11] == "user RTL" for r in random_config(s).data.fabrics))  # fmt: skip

Sby = Callable[..., Report]


def _ok(flow: str) -> Report:
    return Report(flow, (), ("stub",))


def _bad(flow: str, rule: str = "X-RULE", message: str = "broken") -> Report:
    return Report(flow, (Finding("stub", "", 0, rule, "error", message),), ("stub",))


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub every EDA step to pass, recording the calls in order (formal as job:task)."""
    seen: list[str] = []

    def stub(name: str) -> Callable[..., Report]:
        def f(*args: Any, **kwargs: Any) -> Report:
            seen.append(name)
            return _ok(name)

        return f

    def sby(job: Path, root: Path, *, timeout: float | None = None, task: str | None = None,
            hint: str = "") -> Report:  # fmt: skip
        seen.append(f"formal {job.stem}:{task}")
        return _ok("formal")

    for name, attr in (("lint", "lint"), ("compile", "compile_design"), ("sim", "run_tests")):
        monkeypatch.setattr(run, attr, stub(name))
    monkeypatch.setattr(run, "run_sby", sby)
    return seen


def _sby(verdict: Callable[[str, str | None, float | None], Report]) -> Sby:
    def f(job: Path, root: Path, *, timeout: float | None = None, task: str | None = None,
          hint: str = "") -> Report:  # fmt: skip
        return verdict(job.stem, task, timeout)

    return f


def _formal_steps(r: SeedResult) -> dict[str, str]:
    return {s: v for s, v in r.steps.items() if s.startswith("formal ")}


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
    r = run_seed(SEED, tmp_path, budget=300)
    assert (r.status, r.step) == ("pass", None)
    tasks = [c for c in calls if c.startswith("formal ")]
    assert calls == ["lint", "compile", "sim", *tasks]
    assert list(r.steps) == ["draw", "check", "resolve", "generate", "lint", "compile", "sim",
                             *(t.replace(":None", "") for t in tasks)]  # fmt: skip
    assert set(r.steps.values()) == {"pass"}
    name = f"rnd{SEED}"
    assert r.workbook == f"{SEED}/{name}.xlsx" and (tmp_path / r.workbook).is_file()
    assert r.ir == f"{SEED}/{name}.ir.yaml"
    assert load(tmp_path / r.ir).project.name == name
    assert r.repro == [f"bag regress --seed {SEED} --formal-budget 300",
                       f"bag flow {SEED}/{name}.ir.yaml --formal"]  # fmt: skip
    assert (r.budget_s, r.formal) == (300, True)
    saved = (tmp_path / str(SEED) / "result.json").read_text(encoding="utf-8")
    assert SeedResult.from_json(saved) == r


def test_formal_runs_task_by_task_from_the_decode_proof_to_the_covers(
    tmp_path: Path, calls: list[str]
) -> None:
    """D96 and the PR 5 formal review: decode, the reset proof, route bmc, protocol bmc, then
    the covers (route, reset, protocol). Every task of every job runs once."""
    run_seed(SEED, tmp_path)
    tasks = [c.removeprefix("formal ") for c in calls if c.startswith("formal ")]
    kinds = []
    for t in tasks:
        job, task = t.split(":")
        kind = "route" if "_route_" in job else job.rsplit("_", 1)[-1]
        kinds.append(f"{kind} {'cover' if task.startswith('cover') else task}")
    want = ["decode prove", "reset prove", "route bmc", "protocol bmc", "route cover",
            "reset cover", "protocol cover"]  # fmt: skip
    assert set(kinds) == set(want)  # the seed has every kind
    assert kinds == sorted(kinds, key=want.index)
    assert len(tasks) == len(set(tasks))


def test_a_lint_finding_fails_the_seed_at_lint_and_stops_there(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "lint", lambda *a, **k: _bad("lint", message="unused signal"))
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step) == ("fail", "lint")
    assert r.findings == ["stub: error: unused signal [X-RULE]"]
    assert calls == []  # nothing after lint ran
    assert r.steps["lint"] == "fail" and "compile" not in r.steps


def test_a_task_cut_off_is_a_timeout_and_the_rest_still_run(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task over its cap is a timeout; the tasks after it still get their turn, and the
    seed is a timeout (reported, not a failure)."""

    def verdict(stem: str, task: str | None, timeout: float | None) -> Report:
        if stem.endswith("_protocol") and task == "bmc":
            return _bad("formal", "FORMAL-TIMEOUT", f"{stem}_bmc: no verdict within 9 s")
        return _ok("formal")

    monkeypatch.setattr(run, "run_sby", _sby(verdict))
    r = run_seed(SEED, tmp_path)
    late = f"formal rnd{SEED}_protocol:bmc"
    assert (r.status, r.step, r.findings) == ("timeout", late, [])
    assert {s for s, v in _formal_steps(r).items() if v != "pass"} == {late}
    assert f"{late}: rnd{SEED}_protocol_bmc: no verdict within 9 s" in r.notes


@pytest.mark.parametrize("mixed", [True, False])
def test_a_formal_failure_fails_the_seed_even_beside_a_timeout(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    """A failed task is a failure whether its report also holds a timeout (the review's
    mixed report) or a timeout came earlier in the seed: never a timeout or a pass."""
    first = True

    def verdict(stem: str, task: str | None, timeout: float | None) -> Report:
        nonlocal first
        if stem.endswith("_route_f0") and task == "bmc":
            fail = Finding("sby", "", 0, "FORMAL-ASSERT", "error", "x: assertion a fails")
            late = Finding("sby", "", 0, "FORMAL-TIMEOUT", "error", "x: no verdict")
            return Report("formal", (fail, late) if mixed else (fail,), ("sby",))
        if not mixed and first and stem.endswith("_reset"):
            first = False  # the reset proof, before the route bmc, times out
            return _bad("formal", "FORMAL-TIMEOUT", "y: no verdict")
        return _ok("formal")

    monkeypatch.setattr(run, "run_sby", _sby(verdict))
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step) == ("fail", f"formal rnd{SEED}_route_f0:bmc")
    assert "sby: error: x: assertion a fails [FORMAL-ASSERT]" in r.findings


def test_tasks_get_a_quarter_of_what_is_left_and_those_past_the_budget_are_skipped(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each stubbed task takes all of its cap. Decode may use all that is left; after it,
    each task gets a quarter of what is left, at least 30 s and never more than is left:
    with a 400 s budget and a 200 s decode proof, 50, 37.5, then 30 three times and the
    last 22.5 s. The rest are skipped, so the seed is a timeout."""
    clock = [0.0]
    caps: list[float] = []
    monkeypatch.setattr(run, "monotonic", lambda: clock[0])

    def verdict(stem: str, task: str | None, timeout: float | None) -> Report:
        assert timeout is not None
        caps.append(round(timeout, 2))
        clock[0] += 200 if stem.endswith("_decode") else timeout
        return _ok("formal")

    monkeypatch.setattr(run, "run_sby", _sby(verdict))
    r = run_seed(SEED, tmp_path, budget=400)
    assert caps == [400, 50, 37.5, 30, 30, 30, 22.5]
    formal = list(_formal_steps(r).values())
    ran = len(caps)
    assert formal == ["pass"] * ran + ["skipped"] * (len(formal) - ran)
    assert len(formal) > ran  # the seed has more tasks than the budget reaches
    skipped = [s for s, v in _formal_steps(r).items() if v == "skipped"]
    assert (r.status, r.step) == ("timeout", skipped[0])  # formal left undone: not a pass
    assert f"{skipped[0]}: skipped: the formal budget was spent" in r.notes


@pytest.mark.parametrize(("attr", "exc", "step"), [
    ("compile_design", RuntimeError("verilator crashed"), "compile"),
    ("run_tests", SimUnavailable("cocotb is not installed"), "sim"),
    ("generate", GenerateError("fabric f1: no"), "generate"),
])  # fmt: skip
def test_a_step_that_raises_fails_the_seed_at_that_step(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch, attr: str,
    exc: Exception, step: str
) -> None:  # fmt: skip
    """One broken seed must not stop a nightly shard: the exception becomes its finding."""

    def boom(*args: Any, **kwargs: Any) -> Report:
        raise exc

    monkeypatch.setattr(run, attr, boom)
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step, r.steps[step]) == ("fail", step, "fail")
    assert r.findings[-1].endswith(str(exc))
    assert (tmp_path / str(SEED) / "result.json").is_file()


@pytest.mark.parametrize("where", ["draw", "check"])
def test_a_crash_in_the_draw_or_its_workbook_is_a_generator_bug(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("sample larger than population")

    monkeypatch.setattr(run, "random_config" if where == "draw" else "check", boom)
    r = run_seed(SEED, tmp_path)
    assert (r.status, r.step) == ("generator", where)
    assert r.findings == ["ValueError: sample larger than population"]
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


def test_a_user_supplied_fabric_skips_sim_and_formal_and_says_why(
    tmp_path: Path, calls: list[str]
) -> None:
    r = run_seed(EXTERNAL, tmp_path)
    assert (r.status, r.steps["sim"], r.steps["formal"]) == ("pass", "skipped", "skipped")
    assert "sim: skipped: no generated tests (a user-supplied fabric, D8)" in r.notes
    assert "formal: skipped: no generated formal (a user-supplied fabric, D8)" in r.notes
    assert r.repro[1] == f"bag flow {EXTERNAL}/rnd{EXTERNAL}.ir.yaml"  # no --formal
    assert calls == ["lint", "compile"]


def test_no_formal_is_recorded_and_in_the_repro(tmp_path: Path, calls: list[str]) -> None:
    r = run_seed(SEED, tmp_path, formal=False)
    assert (r.status, r.steps["formal"], r.formal) == ("pass", "skipped", False)
    assert r.repro == [f"bag regress --seed {SEED} --no-formal",
                       f"bag flow {SEED}/rnd{SEED}.ir.yaml"]  # fmt: skip
    assert not any(c.startswith("formal") for c in calls)


def test_the_sim_seed_follows_the_regress_seed(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    got: list[int] = []

    def sim(out: Any, *, seed: int) -> Report:
        got.append(seed)
        return _ok("sim")

    monkeypatch.setattr(run, "run_tests", sim)
    run_seed(SEED, tmp_path, formal=False)
    assert got == [SEED]
    assert seed_for_sim(202611170003) == 202611170003 % (1 << 31)


def test_a_rerun_replaces_the_seed_directory(tmp_path: Path, calls: list[str]) -> None:
    stale = tmp_path / str(SEED) / "out" / "stale.sv"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    run_seed(SEED, tmp_path, formal=False)
    assert not stale.exists()


def test_the_summary_lists_failures_first_with_the_first_finding_and_formal_counts() -> None:
    rs = [SeedResult(1, "rnd1", repro=["bag regress --seed 1"],
                     steps={"formal a:bmc": "pass", "formal a:cover": "skipped"}),
          SeedResult(2, "rnd2", "fail", "lint", findings=["slang: error: x | y [R]"],
                     repro=["bag regress --seed 2"]),
          SeedResult(3, "rnd3", "timeout", "formal x", repro=["bag regress --seed 3"])]  # fmt: skip
    data, text = summary(rs)
    rows = json.loads(data)["seeds"]
    assert [s["seed"] for s in rows] == [2, 3, 1]
    assert rows[0]["finding"] == "slang: error: x | y [R]"
    assert rows[2]["formal"] == {"pass": 1, "fail": 0, "timeout": 0, "skipped": 1}
    assert json.loads(data)["counts"] == {"generator": 0, "fail": 1, "timeout": 1, "pass": 1}
    assert text.splitlines()[2] == "0 generator, 1 fail, 1 timeout, 1 pass of 3 seed(s)"
    assert ("| 2 | fail | lint | 0 / 0 / 0 / 0 | slang: error: x \\| y [R] | "
            "`bag regress --seed 2` |") in text  # fmt: skip


def _cli(monkeypatch: pytest.MonkeyPatch, statuses: dict[int, str], *args: str) -> Any:
    def fake(seed: int, run_dir: Path, budget: float = 600, formal: bool = True) -> SeedResult:
        r = SeedResult(seed, f"rnd{seed}", statuses.get(seed, "pass"),  # type: ignore[arg-type]
                       repro=[f"bag regress --seed {seed}"])  # fmt: skip
        (run_dir / str(seed)).mkdir(parents=True, exist_ok=True)
        (run_dir / str(seed) / "result.json").write_text(r.to_json(), encoding="utf-8")
        return r

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
    # A rerun of one seed into the run directory updates its row and keeps the others.
    got = _cli(monkeypatch, {10001: "fail"}, "--seed", "10001", "-o", str(out))
    assert got.exit_code == 1
    assert got.output.splitlines()[-1] == "0 generator, 1 fail, 0 timeout, 1 pass of 2 seed(s)"
    assert (out / "summary.md").read_text(encoding="utf-8") == summary(
        [SeedResult(10001, "rnd10001", "fail", repro=["bag regress --seed 10001"]),
         SeedResult(10003, "rnd10003", repro=["bag regress --seed 10003"])])[1]  # fmt: skip


def test_a_generator_crash_does_not_stop_the_shard(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR 5 review: the draw raising for one seed must leave the other seeds and the summary."""
    real = random_config

    def draw(seed: int) -> Any:
        if seed == 0:
            raise KeyError("f9")
        return real(seed)

    monkeypatch.setattr(run, "random_config", draw)
    got = CliRunner().invoke(main, ["regress", "--seeds", "2", "-o", str(tmp_path),
                                    "--no-formal"])  # fmt: skip
    assert got.exit_code == 1
    assert got.output.splitlines()[:2] == ["seed 0: generator (draw)", "seed 1: pass"]
    assert write_summary(tmp_path) == "1 generator, 0 fail, 0 timeout, 1 pass of 2 seed(s)"


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


def test_the_dimensions_of_a_nested_config_with_adapters() -> None:
    """nested_adapters: sub-wrappers two deep, a CDC adapter, FIFO and slice options, width
    adapters on cpu (32:64) and ram (128:64), and br_per narrowing 64 to 32."""
    dims = dimensions(load(CONFIGS / "p3" / "nested_adapters.ir.yaml"))
    assert (dims["hierarchy_depth"], dims["clock_crossings"]) == (2, 1)
    assert dims["port_options"] == ["fifo", "slice"]
    assert dims["width_ratios"] == ["128:64", "32:64", "64:32"]
