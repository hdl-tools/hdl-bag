# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The formal corpus (Phase 5 PR 4b): every generated SymbiYosys task of every golden, Phase 2
and Phase 3 config holds, with every cover reached. This is the gate evidence "formal passes on
all golden and Phase 3 configs".

Each task of each job is a test of its own (D76), listed from formal_jobs without generating,
so a new job or config cannot be missed. A task that the generated-jobs test
(test_formal_eda.py) or a mutation control (test_decode_mutations.py) already runs is left out
here, so no task runs twice in one CI run; test_every_generated_task_runs_once pins that.

Placement by measured time (D76): a task whose duration in tests/eda_durations.json is over
DEEP_S, or that has none yet, carries ``deep`` and runs nightly only, with DEEP_TIMEOUT_S; the
rest run in every full run with TIMEOUT_S.
"""

import json
import os
from pathlib import Path

import pytest
from test_decode_mutations import DECODE_CONTROL_CONFIGS, ROUTE_CONTROL_CASES
from test_formal_eda import GENERATED_JOBS, IN_CORPUS

from hdl_bag.flows.formal import run_sby
from hdl_bag.formal_gen import PROTOCOL_DEPTH, formal_jobs
from hdl_bag.gen import generate
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.ir import Spec, load

TESTS = Path(__file__).parents[1]
CONFIGS = TESTS / "configs"
DURATIONS = TESTS / "eda_durations.json"
NODE = "tests/formal_gen/test_formal_corpus.py::test_the_formal_corpus_holds"
# D76: about 5 minutes. Full runs give a task TIMEOUT_S, four times that.
DEEP_S = 300
TIMEOUT_S = 1200
# CI may allow more to measure a deep test's cost (pr.yml suite=deep).
DEEP_TIMEOUT_S = int(os.environ.get("BAG_DEEP_TIMEOUT_S", "7200"))
# D84 (owner, 2026-11-13): CI may run the bmc tasks on another engine, to measure it against
# the generated one (pr.yml's bmc_engine input). Unset, the job runs as generated.
BMC_ENGINE = os.environ.get("BAG_BMC_ENGINE", "")
GENERATED_BMC_ENGINE = "bmc: smtbmc bitwuzla"


# D89 (owner, 2026-11-14): the protocol bmc's depth for configs it does not finish at
# PROTOCOL_DEPTH (16) within DEEP_TIMEOUT_S: the step bitwuzla had reached at 7200 s in
# nightly 43328525704 (mixed_adapters_bridges': 15, before the D88 fix), each recorded as that
# config's proof bound in the Phase 5 report. D84's engine trial found none faster.
BMC_DEPTHS = {
    "p3/axi_shared_rr": 10, "p3/axi_xbar_pow2": 10, "p3/axi_xbar_rules": 12,
    "p3/bridges_inhouse": 13, "p3/mixed_ahb_secure": 13, "p3/mixed_shared_chain": 14,
    "p3/mixed_adapters_bridges": 15,
}  # fmt: skip


def with_bmc_depth(job: str, depth: int) -> str:
    """JOB (a protocol .sby file's text) with its bmc task at DEPTH (D89)."""
    lines = job.split("\n")
    old = f"bmc: depth {PROTOCOL_DEPTH}"
    assert lines.count(old) == 1, "the job's bmc depth line moved"
    return "\n".join(f"bmc: depth {depth}" if line == old else line for line in lines)


def with_bmc_engine(job: str, engine: str) -> str:
    """JOB (a .sby file's text) with its bmc task on ENGINE, for an engine measurement (D84)."""
    lines = job.split("\n")
    assert lines.count(GENERATED_BMC_ENGINE) == 1, "the job's bmc engine line moved"
    return "\n".join(f"bmc: {engine}" if line == GENERATED_BMC_ENGINE else line for line in lines)


def _path(config: str) -> Path:
    """CONFIG's IR: golden/demo_ss, or p2/<name> and p3/<name> in tests/configs."""
    if config == "golden/demo_ss":
        return TESTS / "golden" / "demo_ss.ir.yaml"
    return CONFIGS / f"{config}.ir.yaml"


def _configs() -> list[str]:
    found = [f"{d}/{p.name.removesuffix('.ir.yaml')}" for d in ("p2", "p3")
             for p in sorted((CONFIGS / d).glob("*.ir.yaml"))]  # fmt: skip
    return ["golden/demo_ss", *found]


def _spec(config: str) -> Spec:
    return load(_path(config))


def all_tasks() -> list[tuple[str, str, str]]:
    """Every (config, job, task) the corpus configs generate."""
    out = []
    for config in _configs():
        spec = _spec(config)
        for job, tasks in formal_jobs(spec, build_netlist(spec)):
            out += [(config, job, t) for t in tasks]
    return out


def elsewhere() -> dict[tuple[str, str, str], str]:
    """The tasks another EDA test runs, and which one."""
    out: dict[tuple[str, str, str], str] = {}
    by_name = {c.split("/", 1)[1]: c for c in _configs()}
    generated = {by_name[c] for c, _ in GENERATED_JOBS}
    route = {(c, f"route_{u}") for c, u in ROUTE_CONTROL_CASES}
    for config, job, task in all_tasks():
        if config in generated and (config.split("/", 1)[1], job) not in IN_CORPUS:
            out[config, job, task] = "test_the_generated_jobs_hold"
        elif job == "decode" and config in DECODE_CONTROL_CONFIGS:
            out[config, job, task] = "test_the_decode_job_holds_unmutated"
        elif (config, job) in route:
            out[config, job, task] = "test_the_route_job_holds_unmutated"
    return out


def _measured() -> dict[str, float]:
    data: dict[str, float] = json.loads(DURATIONS.read_text(encoding="utf-8"))
    return data


def _id(config: str, job: str, task: str) -> str:
    return f"{config}-{job}-{task}"


def deep(config: str, job: str, task: str, measured: dict[str, float]) -> bool:
    """D76: over DEEP_S in CI, or not measured yet (the nightly measures it first)."""
    took = measured.get(f"{NODE}[{_id(config, job, task)}]")
    return took is None or took > DEEP_S


_SKIP = elsewhere()
_MEASURED = _measured()
CASES = [t for t in all_tasks() if t not in _SKIP]


@pytest.mark.eda
@pytest.mark.parametrize(("config", "job", "task"), [
    pytest.param(*t, id=_id(*t), marks=[pytest.mark.deep] if deep(*t, _MEASURED) else [])
    for t in CASES])  # fmt: skip
def test_the_formal_corpus_holds(tmp_path: Path, config: str, job: str, task: str) -> None:
    """The bmc (or prove) task is clean to its depth, and the cover task reaches every cover,
    so no assertion holds vacuously."""
    spec = _spec(config)
    generate(spec, tmp_path, _path(config).parent)
    timeout = DEEP_TIMEOUT_S if deep(config, job, task, _MEASURED) else TIMEOUT_S
    sby = tmp_path / "formal" / f"{spec.project.name}_{job}.sby"
    if task == "bmc" and job == "protocol" and (BMC_ENGINE or config in BMC_DEPTHS):
        text = sby.read_text(encoding="utf-8")
        if config in BMC_DEPTHS:
            text = with_bmc_depth(text, BMC_DEPTHS[config])
        if BMC_ENGINE:
            text = with_bmc_engine(text, BMC_ENGINE)
        sby.write_text(text, encoding="utf-8", newline="\n")
    report = run_sby(sby, tmp_path, timeout=timeout, task=task)
    assert report.ok, report.format()


def test_a_design_too_big_for_depth_16_has_its_bmc_bound_pinned(tmp_path: Path) -> None:
    """D89 (owner, 2026-11-14, after D84 found no faster engine): the protocol bmc of these
    configs gave no verdict at depth 16 within 7200 s; the corpus runs it to the step it had
    reached (bitwuzla, nightly 43328525704), recorded as its proof bound. The generated job
    keeps 16; only this test changes the depth."""
    assert BMC_DEPTHS == {
        "p3/axi_shared_rr": 10, "p3/axi_xbar_pow2": 10, "p3/axi_xbar_rules": 12,
        "p3/bridges_inhouse": 13, "p3/mixed_ahb_secure": 13, "p3/mixed_shared_chain": 14,
        "p3/mixed_adapters_bridges": 15,
    }  # fmt: skip
    spec = _spec("p3/axi_xbar_rules")
    generate(spec, tmp_path, _path("p3/axi_xbar_rules").parent)
    job = (tmp_path / "formal" / "axi_xbar_rules_protocol.sby").read_text(encoding="utf-8")
    pinned = with_bmc_depth(job, 12)
    assert "\nbmc: depth 12\n" in pinned and "\nbmc: depth 16\n" not in pinned
    assert pinned.replace("bmc: depth 12", "bmc: depth 16") == job
    assert all(config in {c for c, _, _ in all_tasks()} for config in BMC_DEPTHS)


def test_an_engine_measurement_swaps_only_the_bmc_engine(tmp_path: Path) -> None:
    """D84: a measurement run puts the protocol bmc on another engine and leaves the rest of
    the job, the cover tasks' engine included, as generated."""
    spec = _spec("p3/axi_xbar_rules")
    generate(spec, tmp_path, _path("p3/axi_xbar_rules").parent)
    job = (tmp_path / "formal" / "axi_xbar_rules_protocol.sby").read_text(encoding="utf-8")
    swapped = with_bmc_engine(job, "abc bmc3")
    assert GENERATED_BMC_ENGINE not in swapped and "\nbmc: abc bmc3\n" in swapped
    assert swapped.replace("bmc: abc bmc3", GENERATED_BMC_ENGINE) == job
    assert "cover_cpu: smtbmc yices" in swapped


def test_every_generated_task_runs_once() -> None:
    """Each task is either a corpus case or run by exactly the test named in elsewhere(): the
    generated-jobs configs run whole, the decode and route controls run their jobs. The two
    user-supplied fabric configs generate none (D8)."""
    tasks = all_tasks()
    assert len(tasks) == len(set(tasks))
    assert set(CASES) | set(_SKIP) == set(tasks) and not set(CASES) & set(_SKIP)
    configs = {c for c, _, _ in tasks}
    assert sorted(set(_configs()) - configs) == ["p3/external_fabric", "p3/external_shared"]
    assert {"golden/demo_ss", "p3/mixed_narrow_apb"} <= {c for c, _, _ in CASES}
