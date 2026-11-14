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
from hdl_bag.formal_gen import PROTOCOL_DEPTH, checkers, formal_jobs
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
# axi_xbar_rules: 12 timed out at step 11 in nightly 43533410868 on a slower runner, so 11.
BMC_DEPTHS = {
    "p3/axi_shared_rr": 10, "p3/axi_xbar_pow2": 10, "p3/axi_xbar_rules": 11,
    "p3/bridges_inhouse": 13, "p3/mixed_ahb_secure": 13, "p3/mixed_shared_chain": 14,
    "p3/mixed_adapters_bridges": 15,
}  # fmt: skip


# D92 (owner, 2026-11-14): the cover tasks still short of their covers at DEEP_TIMEOUT_S in
# nightly 43533410868, each run as two tasks, cover_<port>_writes and cover_<port>_reads: the
# property file's BAG_WRITES_<port> and BAG_READS_<port> hold the active masters to one
# direction and keep that direction's covers, and the port's ZipCPU checker drops the other
# direction's with its F_OPT_WRITE_ONLY or F_OPT_READ_ONLY. The generated job keeps one task.
SPLIT = {
    "golden/demo_ss": ("cpu0", "ddr0"),
    "p3/axi_adapters": ("cpu", "ddr", "dma", "per", "pmu"),
    "p3/bridges_inhouse_cdc": ("wdt",), "p3/mixed_wide_to_narrow": ("mem", "rom"),
}  # fmt: skip
DIRECTIONS = {"writes": "F_OPT_WRITE_ONLY", "reads": "F_OPT_READ_ONLY"}


def with_direction_split(job: str, port: str, zipcpu: str | None) -> str:
    """JOB (a protocol .sby file's text) with PORT's cover task as two (D92), one per
    direction; ZIPCPU names the port's ZipCPU checker copy, if it has one."""
    task = f"cover_{port}"
    lines = job.split("\n")
    assert lines.count(task) == 1, "the job's task list moved"
    out = []
    for line in lines:
        if line == task:
            out += [f"{task}_{d}" for d in DIRECTIONS]
        elif line.startswith(f"{task}: "):
            body = line.removeprefix(f"{task}: ")
            for d in DIRECTIONS:
                macro = f"-D BAG_IDLE_{port} -D BAG_{d.upper()}_{port}"
                out.append(f"{task}_{d}: " + body.replace(f"-D BAG_IDLE_{port}", macro))
        else:
            out.append(line)
            if zipcpu and line.startswith("chparam ") and line.endswith(f" {zipcpu}"):
                out += [f"{task}_{d}: chparam -set {o} 1 {zipcpu}" for d, o in DIRECTIONS.items()]
    return "\n".join(out)


def split_tasks(config: str, task: str) -> list[str]:
    """TASK as the corpus runs it: itself, or its two direction tasks (D92)."""
    ports = SPLIT.get(config, ())
    if task.removeprefix("cover_") in ports:
        return [f"{task}_{d}" for d in DIRECTIONS]
    return [task]


def _split_port(config: str, task: str) -> str | None:
    """The port whose direction task (D92) TASK is, if it is one."""
    for d in DIRECTIONS:
        port = task.removeprefix("cover_").removesuffix(f"_{d}")
        if task.endswith(f"_{d}") and port in SPLIT.get(config, ()):
            return port
    return None


def _zipcpu(spec: Spec, port: str) -> str | None:
    """PORT's ZipCPU AXI4-Lite checker copy in the protocol job, if it has one: those take
    F_OPT_WRITE_ONLY and F_OPT_READ_ONLY. The APB ones have neither, nor covers of their own."""
    found = checkers(spec, build_netlist(spec))
    return {c.port: c.module for c in found if c.vendor.startswith("faxil")}.get(port)


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


# D93 (owner, 2026-11-14): the D92 tasks that did not pass in nightly 43566745036 (timed out
# at DEEP_TIMEOUT_S, an engine that died beside another solver, or a lost runner) run alone,
# one per runner (pr.yml's eda-alone job, marked alone), with ALONE_TIMEOUT_S.
ALONE = {
    ("golden/demo_ss", "cover_cpu0_writes"), ("golden/demo_ss", "cover_cpu0_reads"),
    ("golden/demo_ss", "cover_ddr0_writes"),
    ("p3/axi_adapters", "cover_cpu_writes"), ("p3/axi_adapters", "cover_cpu_reads"),
    ("p3/axi_adapters", "cover_ddr_writes"), ("p3/axi_adapters", "cover_ddr_reads"),
    ("p3/axi_adapters", "cover_per_writes"), ("p3/axi_adapters", "cover_pmu_writes"),
    ("p3/mixed_wide_to_narrow", "cover_mem_writes"),
    ("p3/mixed_wide_to_narrow", "cover_rom_writes"),
}  # fmt: skip
ALONE_TIMEOUT_S = int(os.environ.get("BAG_ALONE_TIMEOUT_S", "14400"))

_SKIP = elsewhere()
_MEASURED = _measured()
CASES = [(c, j, s) for c, j, t in all_tasks() if (c, j, t) not in _SKIP
         for s in (split_tasks(c, t) if j == "protocol" else [t])]  # fmt: skip


def marks(case: tuple[str, str, str]) -> list[pytest.MarkDecorator]:
    """CASE's marks: deep by its measured time (D76), alone and deep if it is in ALONE (D93)."""
    config, _, task = case
    if (config, task) in ALONE:
        return [pytest.mark.deep, pytest.mark.alone]
    return [pytest.mark.deep] if deep(*case, _MEASURED) else []


@pytest.mark.eda
@pytest.mark.parametrize(("config", "job", "task"), [
    pytest.param(*t, id=_id(*t), marks=marks(t)) for t in CASES])  # fmt: skip
def test_the_formal_corpus_holds(tmp_path: Path, config: str, job: str, task: str) -> None:
    """The bmc (or prove) task is clean to its depth, and the cover task reaches every cover,
    so no assertion holds vacuously."""
    spec = _spec(config)
    generate(spec, tmp_path, _path(config).parent)
    timeout = DEEP_TIMEOUT_S if deep(config, job, task, _MEASURED) else TIMEOUT_S
    if (config, task) in ALONE:
        timeout = ALONE_TIMEOUT_S
    sby = tmp_path / "formal" / f"{spec.project.name}_{job}.sby"
    if task == "bmc" and job == "protocol" and (BMC_ENGINE or config in BMC_DEPTHS):
        text = sby.read_text(encoding="utf-8")
        if config in BMC_DEPTHS:
            text = with_bmc_depth(text, BMC_DEPTHS[config])
        if BMC_ENGINE:
            text = with_bmc_engine(text, BMC_ENGINE)
        sby.write_text(text, encoding="utf-8", newline="\n")
    if job == "protocol" and (port := _split_port(config, task)):
        text = with_direction_split(sby.read_text(encoding="utf-8"), port, _zipcpu(spec, port))
        sby.write_text(text, encoding="utf-8", newline="\n")
    report = run_sby(sby, tmp_path, timeout=timeout, task=task)
    assert report.ok, report.format()


def test_a_design_too_big_for_depth_16_has_its_bmc_bound_pinned(tmp_path: Path) -> None:
    """D89 (owner, 2026-11-14, after D84 found no faster engine): the protocol bmc of these
    configs gave no verdict at depth 16 within 7200 s; the corpus runs it to the step it had
    reached (bitwuzla, nightly 43328525704), recorded as its proof bound. The generated job
    keeps 16; only this test changes the depth."""
    assert BMC_DEPTHS == {
        "p3/axi_shared_rr": 10, "p3/axi_xbar_pow2": 10, "p3/axi_xbar_rules": 11,
        "p3/bridges_inhouse": 13, "p3/mixed_ahb_secure": 13, "p3/mixed_shared_chain": 14,
        "p3/mixed_adapters_bridges": 15,
    }  # fmt: skip
    spec = _spec("p3/axi_xbar_rules")
    generate(spec, tmp_path, _path("p3/axi_xbar_rules").parent)
    job = (tmp_path / "formal" / "axi_xbar_rules_protocol.sby").read_text(encoding="utf-8")
    pinned = with_bmc_depth(job, 11)
    assert "\nbmc: depth 11\n" in pinned and "\nbmc: depth 16\n" not in pinned
    assert pinned.replace("bmc: depth 11", "bmc: depth 16") == job
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


def test_a_deep_cover_task_runs_as_a_write_task_and_a_read_task(tmp_path: Path) -> None:
    """D92 (owner, 2026-11-14): the ten cover tasks short of their covers at 7200 s in nightly
    43533410868 run split by direction; each split port has covers both ways, and a ZipCPU
    port's checker drops the other direction's covers itself. The generated job is unchanged
    but for those two tasks."""
    assert SPLIT == {
        "golden/demo_ss": ("cpu0", "ddr0"),
        "p3/axi_adapters": ("cpu", "ddr", "dma", "per", "pmu"),
        "p3/bridges_inhouse_cdc": ("wdt",), "p3/mixed_wide_to_narrow": ("mem", "rom"),
    }  # fmt: skip
    for config, ports in SPLIT.items():
        spec = _spec(config)
        generate(spec, tmp_path / config, _path(config).parent)
        props = (tmp_path / config / "formal" / f"{spec.project.name}_protocol.sv").read_text(
            encoding="utf-8"
        )
        for p in ports:
            assert f"`ifdef BAG_WRITES_{p}\n" in props and f"`ifdef BAG_READS_{p}\n" in props, p
            assert (config, "protocol", f"cover_{p}_writes") in CASES, p
            assert (config, "protocol", f"cover_{p}") not in CASES, p
    spec = _spec("p3/axi_adapters")
    generate(spec, tmp_path / "a", _path("p3/axi_adapters").parent)
    job = (tmp_path / "a" / "formal" / "axi_adapters_protocol.sby").read_text(encoding="utf-8")
    zipcpu = _zipcpu(spec, "pmu")
    assert zipcpu == "bag_axi_adapters_faxil_master_pmu" and _zipcpu(spec, "ddr") is None
    # ZipCPU's APB checkers have no direction options and no covers to drop.
    # (bridges_inhouse_cdc dbg: an APB master port, fapb_slave.)
    assert _zipcpu(_spec("p3/bridges_inhouse_cdc"), "dbg") is None
    split = with_direction_split(job, "pmu", zipcpu)
    assert "\ncover_pmu_writes\ncover_pmu_reads\ncover_cpu\n" in split
    assert ("\ncover_pmu_writes: mode cover\ncover_pmu_reads: mode cover\n"
            "cover_pmu_writes: depth 52\ncover_pmu_reads: depth 52\n") in split  # fmt: skip
    assert "\ncover_pmu_writes: smtbmc yices\ncover_pmu_reads: smtbmc yices\n" in split
    assert (
        f" {zipcpu}\ncover_pmu_writes: chparam -set F_OPT_WRITE_ONLY 1 {zipcpu}\n"
        f"cover_pmu_reads: chparam -set F_OPT_READ_ONLY 1 {zipcpu}\n"
    ) in split
    assert ("\ncover_pmu_reads: read_slang -D BAG_COVER_pmu -D BAG_IDLE_pmu -D BAG_READS_pmu "
            "--extern-modules ") in split  # fmt: skip
    assert (
        "\ncover_pmu_writes: chformal -remove -cover bag_axi_adapters_faxil_slave_dbg\n"
        "cover_pmu_reads: chformal -remove -cover bag_axi_adapters_faxil_slave_dbg\n"
    ) in split
    assert "\ncover_pmu\n" not in split and "\ncover_pmu:" not in split
    # Only pmu's lines changed: every other line is as generated, in order.
    kept = [ln for ln in split.split("\n") if not ln.startswith("cover_pmu_")]
    assert kept == [ln for ln in job.split("\n") if ln != "cover_pmu"
                    and not ln.startswith("cover_pmu:")]  # fmt: skip
    assert _split_port("p3/axi_adapters", "cover_pmu_reads") == "pmu"
    assert _split_port("p3/axi_adapters", "cover_dbg") is None
    assert split_tasks("p3/axi_adapters", "cover_dbg") == ["cover_dbg"]


def test_a_split_task_still_short_runs_alone_for_four_hours() -> None:
    """D93 (owner, 2026-11-14): the D92 tasks that did not pass in nightly 43566745036 (eight
    timed out at 7200 s, demo_ss cpu0_reads' engine died with another solver on the runner,
    and shard 12's lost runner left cpu0_writes and rom_writes unmeasured) get ALONE_TIMEOUT_S
    and the alone mark: the nightly runs them in a job of their own, one test per runner, and
    leaves them out of its two-at-a-time shards."""
    assert {
        ("golden/demo_ss", "cover_cpu0_writes"), ("golden/demo_ss", "cover_cpu0_reads"),
        ("golden/demo_ss", "cover_ddr0_writes"),
        ("p3/axi_adapters", "cover_cpu_writes"), ("p3/axi_adapters", "cover_cpu_reads"),
        ("p3/axi_adapters", "cover_ddr_writes"), ("p3/axi_adapters", "cover_ddr_reads"),
        ("p3/axi_adapters", "cover_per_writes"), ("p3/axi_adapters", "cover_pmu_writes"),
        ("p3/mixed_wide_to_narrow", "cover_mem_writes"),
        ("p3/mixed_wide_to_narrow", "cover_rom_writes"),
    } == ALONE  # fmt: skip
    assert ALONE_TIMEOUT_S == 14400 or "BAG_ALONE_TIMEOUT_S" in os.environ
    split = {(c, t) for c, j, t in CASES if _split_port(c, t)}
    assert split >= ALONE
    for c, j, t in CASES:
        names = {m.name for m in marks((c, j, t))}
        assert ("alone" in names) == ((c, t) in ALONE), (c, t)
        assert "alone" not in names or "deep" in names, (c, t)
    flow = (TESTS.parent / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
    alone = flow.split("\n  eda-alone:\n", 1)[1].split("\n\n", 1)[0]
    assert "marks: eda and alone\n" in alone and f"shards: {len(ALONE)}\n" in alone
    assert "workers: '1'\n" in alone and "timeout-minutes: 300\n" in alone
    # The two-at-a-time shards leave them out, unless a dispatch selects tests by name.
    assert "&& 'eda' || 'eda and not alone') || 'eda and not deep') }}" in flow


def test_every_generated_task_runs_once() -> None:
    """Each task is either a corpus case or run by exactly the test named in elsewhere(): the
    generated-jobs configs run whole, the decode and route controls run their jobs. The two
    user-supplied fabric configs generate none (D8)."""
    tasks = all_tasks()
    assert len(tasks) == len(set(tasks)) and len(CASES) == len(set(CASES))
    # A split task (D92) stands for the generated one it came from.
    run = {(c, j, f"cover_{p}" if (p := _split_port(c, t)) else t) for c, j, t in CASES}
    assert run | set(_SKIP) == set(tasks) and not run & set(_SKIP)
    configs = {c for c, _, _ in tasks}
    assert sorted(set(_configs()) - configs) == ["p3/external_fabric", "p3/external_shared"]
    assert {"golden/demo_ss", "p3/mixed_narrow_apb"} <= {c for c, _, _ in CASES}
