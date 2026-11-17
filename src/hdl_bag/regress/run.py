# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""One seed of ``bag regress``: draw, check, resolve, generate, lint, compile, the generated
tests and formal, recording where it failed and how to reproduce it (plan PR 5).

Formal per seed (D96): the decode job runs first, then the protocol, reset and route jobs,
all within the seed's formal budget. A job cut off by the budget is a timeout, which is
reported but is not a failure; jobs the budget never reached are listed as skipped. Full
formal stays on the fixed corpus.

A step that raises (a bug in BAG or in this runner) fails the seed at that step with the
exception's text, so one seed never stops the rest of a nightly shard.
"""

import json
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import monotonic
from typing import Literal

from hdl_bag.flows import Report, SimUnavailable, compile_design, lint, run_tests
from hdl_bag.flows.formal import run_sby
from hdl_bag.gen import MissingRtl, Outputs, generate, include_dirs, sources
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir import ResolveError, dump, resolve
from hdl_bag.regress.dsl import Cfg
from hdl_bag.regress.generate import check, random_config
from hdl_bag.regress.space import Dimensions, dimensions

Status = Literal["pass", "fail", "timeout", "generator"]
# Seed j of a run is BASE * SPAN + j, so runs with consecutive bases (dates) never overlap.
SPAN = 10_000
DEFAULT_BUDGET_S = 600.0


def seeds(base: int, count: int, shard: int = 1, shards: int = 1) -> list[int]:
    """The seeds of shard SHARD (1-based) of SHARDS, of a run of COUNT seeds from BASE."""
    if not 0 < count <= SPAN:
        raise ValueError(f"--seeds must be 1 to {SPAN}, not {count}")
    if not 1 <= shard <= shards:
        raise ValueError(f"--shard {shard}/{shards}: the shard must be 1 to {shards}")
    if base < 0:
        raise ValueError(f"--seed-base must be 0 or more, not {base}")
    return [base * SPAN + j for j in range(count) if j % shards == shard - 1]


@dataclass
class SeedResult:
    seed: int
    name: str
    status: Status = "pass"
    step: str | None = None  # the step that failed or timed out
    steps: dict[str, str] = field(default_factory=dict)  # step -> pass, fail, timeout, skipped
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dimensions: Dimensions = field(default_factory=dict)
    workbook: str | None = None  # paths relative to the run directory
    ir: str | None = None
    repro: list[str] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, sort_keys=True) + "\n"


def _record(result: SeedResult, step: str, report: Report) -> bool:
    """Record STEP's REPORT; True when it passed."""
    result.notes += [f"{step}: {n}" for n in report.notes]
    if report.ok:
        result.steps[step] = "pass"
        return True
    result.steps[step] = "fail"
    result.status, result.step = "fail", step
    result.findings += [f.format() for f in report.unwaived]
    return False


def _fail(result: SeedResult, step: str, message: str) -> None:
    result.steps[step] = "fail"
    result.status, result.step = "fail", step
    result.findings.append(message)


def _formal_jobs(out: Outputs) -> list[str]:
    """The decode job first (D96), then the rest in their generated order."""
    return sorted(out.formal, key=lambda rel: not rel.endswith("_decode.sby"))


def _formal(result: SeedResult, out: Outputs, budget: float) -> None:
    root = out.root.resolve()
    deadline = monotonic() + budget
    for rel in _formal_jobs(out):
        step = f"formal {Path(rel).stem}"
        left = deadline - monotonic()
        if left <= 0:
            result.steps[step] = "skipped"
            continue
        result.steps[step] = "error"  # until it reports
        report = run_sby(root / rel, root, timeout=max(left, 1.0))
        late = [f for f in report.unwaived if f.rule == "FORMAL-TIMEOUT"]
        if late and len(late) == len(report.unwaived):
            result.steps[step] = "timeout"
            result.notes += [f"{step}: {f.message}" for f in late]
            result.notes += [f"{step}: {n}" for n in report.notes]
            if result.status == "pass":
                result.status, result.step = "timeout", step
            continue
        if not _record(result, step, report):
            return


def run_seed(seed: int, run_dir: Path, budget: float = DEFAULT_BUDGET_S,
             formal: bool = True) -> SeedResult:  # fmt: skip
    """Run SEED in RUN_DIR/<seed>/ and write its result.json there."""
    where = run_dir / str(seed)
    cfg = random_config(seed)
    result = SeedResult(seed, cfg.name)
    result.repro = [f"bag regress --seed {seed}"]
    where.mkdir(parents=True, exist_ok=True)
    try:
        _steps(result, where, cfg, budget, formal)
    except Exception as exc:
        step = next((s for s, v in result.steps.items() if v == "error"), "regress")
        result.steps[step] = "fail"
        result.status, result.step = "fail", step
        result.findings.append(f"{type(exc).__name__}: {exc}")
        result.notes += traceback.format_exc().splitlines()
    (where / "result.json").write_text(result.to_json(), encoding="utf-8", newline="\n")
    return result


def _timed(result: SeedResult, step: str, start: float) -> None:
    result.seconds[step] = round(monotonic() - start, 2)


def _begin(result: SeedResult, step: str) -> float:
    result.steps[step] = "error"  # until the step records its outcome
    return monotonic()


def _steps(result: SeedResult, where: Path, cfg: Cfg, budget: float, formal: bool) -> None:
    t = _begin(result, "check")
    workbook, checked = check(cfg, where)
    result.workbook = f"{where.name}/{workbook.name}"
    _timed(result, "check", t)
    if checked.errors:
        # The draw is illegal: a generator bug, not a BAG one (plan PR 5).
        result.findings += [f"{f.severity}: {f.message}" for f in checked.errors]
        result.steps["check"] = "fail"
        result.status, result.step = "generator", "check"
        return
    result.steps["check"] = "pass"
    result.notes += [f"check: {f.severity}: {f.message}" for f in checked.warnings]
    _begin(result, "resolve")
    try:
        spec = resolve(checked)
    except ResolveError as exc:
        _fail(result, "resolve", str(exc))
        return
    ir = where / f"{cfg.name}.ir.yaml"
    dump(spec, ir)
    result.ir = f"{where.name}/{ir.name}"
    result.dimensions = dimensions(spec)
    result.repro.append(f"bag flow {where.name}/{ir.name}{' --formal' if formal else ''}")
    result.steps["resolve"] = "pass"

    t = _begin(result, "generate")
    try:
        out = generate(spec, where / "out", where)
    except (GenerateError, MissingRtl, OSError) as exc:
        _fail(result, "generate", str(exc))
        return
    result.steps["generate"] = "pass"
    _timed(result, "generate", t)
    files, incdirs = sources(out), include_dirs(out)
    t = _begin(result, "lint")
    ok = _record(result, "lint", lint(files, out.top, out.root, tools=out.lint_tools,
                                      incdirs=incdirs))  # fmt: skip
    _timed(result, "lint", t)
    if not ok:
        return
    t = _begin(result, "compile")
    ok = _record(result, "compile", compile_design(files, out.top, out.root, incdirs=incdirs,
                                                   build=out.tests is None))  # fmt: skip
    _timed(result, "compile", t)
    if not ok:
        return
    if out.tests is not None:
        t = _begin(result, "sim")
        try:
            ok = _record(result, "sim", run_tests(out))
        except (SimUnavailable, ValueError, RuntimeError) as exc:
            _fail(result, "sim", str(exc))
            ok = False
        _timed(result, "sim", t)
        if not ok:
            return
    if formal and out.formal:
        t = monotonic()
        _formal(result, out, budget)
        _timed(result, "formal", t)


def summary(results: list[SeedResult]) -> tuple[str, str]:
    """The run's summary.json and summary.md: one line per seed, failures first."""
    order = {"generator": 0, "fail": 1, "timeout": 2, "pass": 3}
    rows = sorted(results, key=lambda r: (order[r.status], r.seed))
    counts = {s: sum(r.status == s for r in results) for s in order}
    data = {
        "counts": counts,
        "seeds": [{"seed": r.seed, "status": r.status, "step": r.step, "repro": r.repro}
                  for r in rows],
    }  # fmt: skip
    lines = [
        "# bag regress",
        "",
        ", ".join(f"{n} {s}" for s, n in counts.items()) + f" of {len(results)} seed(s)",
        "",
        "| Seed | Status | Step | Reproduce |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {r.seed} | {r.status} | {r.step or ''} | `{r.repro[0]}` |" for r in rows]
    return json.dumps(data, indent=1) + "\n", "\n".join(lines) + "\n"
