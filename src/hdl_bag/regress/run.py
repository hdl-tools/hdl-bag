# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""One seed of ``bag regress``: draw, check, resolve, generate, lint, compile, the generated
tests and formal, recording where it failed and how to reproduce it (plan PR 5).

Formal per seed (D96): the decode job, then the protocol, reset and route jobs, all within
the seed's formal budget. Each task runs on its own, so one task's verdict never waits on,
or is lost with, another's (PR 5 review). The order spends the budget where a bug is
cheapest to find: the decode proof, the reset proof, the route and protocol bmc tasks, then
the covers (which guard against vacuity). Every task after the decode job is capped at a
quarter of what is left (at least 30 s), so one slow task cannot starve the rest. A task
cut off is a timeout and a task the budget never reached is skipped; either makes the seed
a ``timeout``, which is reported but is not a failure. Full formal stays on the fixed corpus.

A step that raises (a bug in BAG or in this runner) fails the seed at that step with the
exception's text, so one seed never stops the rest of a nightly shard. A seed's config
depends on the generator and the template registry, so ``result.json`` records the BAG
version it was drawn with.
"""

import json
import shutil
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import monotonic
from typing import Literal

from hdl_bag import __version__
from hdl_bag.flows import Report, SimUnavailable, compile_design, lint, run_tests
from hdl_bag.flows.formal import job_tasks, run_sby
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
MIN_TASK_S = 30.0


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
    # step -> pass, fail, timeout or skipped, in the order run
    steps: dict[str, str] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dimensions: Dimensions = field(default_factory=dict)
    workbook: str | None = None  # paths relative to the run directory
    ir: str | None = None
    repro: list[str] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    budget_s: float = DEFAULT_BUDGET_S
    formal: bool = True
    bag_version: str = __version__

    def to_json(self) -> str:
        # Unsorted: the steps keep the order they ran in (field order is fixed).
        return json.dumps(asdict(self), indent=1) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "SeedResult":
        return cls(**json.loads(text))

    def formal_counts(self) -> dict[str, int]:
        """How many formal tasks passed, failed, timed out and were skipped."""
        got = [v for s, v in self.steps.items() if s.startswith("formal ")]
        return {k: got.count(k) for k in ("pass", "fail", "timeout", "skipped")}


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


def _skip(result: SeedResult, step: str, reason: str) -> None:
    result.steps[step] = "skipped"
    result.notes.append(f"{step}: skipped: {reason}")


def _rank(stem: str, task: str) -> int:
    """Where a task goes in the formal order (D96, PR 5 review): proofs and bmc first, from
    the cheapest kind (measured over the corpus), covers last."""
    kind = "route" if "_route_" in stem else stem.rsplit("_", 1)[-1]
    cover = task.startswith("cover")
    return {
        ("decode", False): 0, ("decode", True): 0, ("reset", False): 1, ("route", False): 2,
        ("protocol", False): 3, ("route", True): 4, ("reset", True): 5, ("protocol", True): 6,
    }.get((kind, cover), 7)  # fmt: skip


def formal_tasks(out: Outputs) -> list[tuple[str, str | None]]:
    """(job, task) in the order a seed runs them; task None for a job without tasks."""
    root = out.root.resolve()
    found: list[tuple[int, str, str | None]] = []
    for rel in out.formal:
        stem = Path(rel).stem
        names = [t.removeprefix(f"{stem}_") for t in job_tasks(root / rel)]
        for task in names if names != [stem] else [None]:
            found.append((_rank(stem, task or ""), rel, task))
    return [(rel, task) for _, rel, task in sorted(found, key=lambda f: f[0])]


def _formal(result: SeedResult, out: Outputs, budget: float) -> None:
    root = out.root.resolve()
    deadline = monotonic() + budget
    for rel, task in formal_tasks(out):
        stem = Path(rel).stem
        step = f"formal {stem}" + (f":{task}" if task else "")
        left = deadline - monotonic()
        if left <= 0:
            _skip(result, step, "the formal budget was spent")
            continue
        cap = left if stem.endswith("_decode") else min(left, max(MIN_TASK_S, left / 4))
        result.steps[step] = "error"  # until it reports
        start = monotonic()
        report = run_sby(root / rel, root, timeout=cap, task=task, hint="--formal-budget")
        result.seconds[step] = round(monotonic() - start, 2)
        late = [f for f in report.unwaived if f.rule == "FORMAL-TIMEOUT"]
        if late and len(late) == len(report.unwaived):
            result.steps[step] = "timeout"
            result.notes += [f"{step}: {f.message}" for f in late]
            result.notes += [f"{step}: {n}" for n in report.notes]
            continue
        # A failure, also one found by a task before another ran out of time.
        _record(result, step, report)


def _settle(result: SeedResult) -> None:
    """A seed that passed but left formal work undone is a timeout at the first such task."""
    if result.status != "pass":
        return
    undone = next((s for s, v in result.steps.items() if v in ("timeout", "skipped")
                   and s.startswith("formal ")), None)  # fmt: skip
    if undone is not None:
        result.status, result.step = "timeout", undone


def run_seed(seed: int, run_dir: Path, budget: float = DEFAULT_BUDGET_S,
             formal: bool = True) -> SeedResult:  # fmt: skip
    """Run SEED in RUN_DIR/<seed>/ (cleared first) and write its result.json there."""
    where = run_dir / str(seed)
    if where.exists():
        shutil.rmtree(where)  # never leave an earlier run's files beside this one's
    where.mkdir(parents=True)
    flags = f" --formal-budget {budget:g}" if formal else " --no-formal"
    result = SeedResult(seed, f"rnd{seed}", budget_s=budget, formal=formal)
    result.repro = [f"bag regress --seed {seed}{flags}"]
    result.steps["draw"] = "error"
    try:
        cfg = random_config(seed)
        result.steps["draw"] = "pass"
        _steps(result, where, cfg, budget, formal)
    except Exception as exc:
        step = next((s for s, v in result.steps.items() if v == "error"), "regress")
        result.steps[step] = "fail"
        # The draw and the generator's own workbook are the generator's, not BAG's.
        result.status = "generator" if step in ("draw", "check") else "fail"
        result.step = step
        result.findings.append(f"{type(exc).__name__}: {exc}")
        result.notes += traceback.format_exc().splitlines()
    _settle(result)
    (where / "result.json").write_text(result.to_json(), encoding="utf-8", newline="\n")
    return result


def _begin(result: SeedResult, step: str) -> float:
    result.steps[step] = "error"  # until the step records its outcome
    return monotonic()


def _timed(result: SeedResult, step: str, start: float) -> None:
    result.seconds[step] = round(monotonic() - start, 2)


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
    result.steps["resolve"] = "pass"

    t = _begin(result, "generate")
    try:
        out = generate(spec, where / "out", where)
    except (GenerateError, MissingRtl, OSError) as exc:
        _fail(result, "generate", str(exc))
        return
    result.steps["generate"] = "pass"
    _timed(result, "generate", t)
    with_formal = formal and bool(out.formal)
    result.repro.append(f"bag flow {result.ir}{' --formal' if with_formal else ''}")
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
    if out.tests is None:
        _skip(result, "sim", "no generated tests (a user-supplied fabric, D8)")
    else:
        t = _begin(result, "sim")
        try:
            # The sim seed follows the regress seed, so a rerun repeats the same traffic.
            ok = _record(result, "sim", run_tests(out, seed=seed_for_sim(result.seed)))
        except (SimUnavailable, ValueError, RuntimeError) as exc:
            _fail(result, "sim", str(exc))
            ok = False
        _timed(result, "sim", t)
        if not ok:
            return
    if not formal:
        _skip(result, "formal", "--no-formal")
    elif not out.formal:
        _skip(result, "formal", "no generated formal (a user-supplied fabric, D8)")
    else:
        t = monotonic()
        _formal(result, out, budget)
        _timed(result, "formal", t)


def seed_for_sim(seed: int) -> int:
    """The cocotb seed of a regress seed (cocotb takes 32 bits)."""
    return seed % (1 << 31)


def summary(results: list[SeedResult]) -> tuple[str, str]:
    """The run's summary.json and summary.md: one line per seed, failures first, with the
    first finding and the formal tasks per outcome. summary.json also keeps each seed's
    dimensions, for the nightly report's coverage over the nights (#59)."""
    order = {"generator": 0, "fail": 1, "timeout": 2, "pass": 3}
    rows = sorted(results, key=lambda r: (order[r.status], r.seed))
    counts = {s: sum(r.status == s for r in results) for s in order}
    data = {
        "counts": counts,
        "seeds": [{"seed": r.seed, "status": r.status, "step": r.step,
                   "finding": r.findings[0] if r.findings else None,
                   "formal": r.formal_counts(), "repro": r.repro,
                   "dimensions": r.dimensions} for r in rows],
    }  # fmt: skip
    lines = [
        "# bag regress",
        "",
        ", ".join(f"{n} {s}" for s, n in counts.items()) + f" of {len(results)} seed(s)",
        "",
        "Formal tasks per seed: passed / failed / timed out / skipped.",
        "",
        "| Seed | Status | Step | Formal | First finding | Reproduce |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        f = r.formal_counts()
        first = (r.findings[0] if r.findings else "").replace("|", "\\|")[:200]
        lines.append(f"| {r.seed} | {r.status} | {r.step or ''} | {f['pass']} / {f['fail']} / "
                     f"{f['timeout']} / {f['skipped']} | {first} | `{r.repro[0]}` |")  # fmt: skip
    return json.dumps(data, indent=1) + "\n", "\n".join(lines) + "\n"


def missing_seeds(run_dir: Path, expected: list[int]) -> list[int]:
    """The EXPECTED seeds that left no result.json in RUN_DIR: a job cut off by its time limit
    loses the seed it was running and every seed after it (PR 6 review)."""
    return [s for s in expected if not (run_dir / str(s) / "result.json").is_file()]


def write_summary(run_dir: Path, expected: list[int] | None = None) -> str:
    """Write RUN_DIR's summary from every seed's result.json in it (so a rerun of one seed
    updates its row, and a run cut short still has a summary); returns the counts line. Given
    the EXPECTED seeds, the summary also names those that left no result."""
    results = [SeedResult.from_json(p.read_text(encoding="utf-8"))
               for p in sorted(run_dir.glob("*/result.json"))]  # fmt: skip
    data, text = summary(results)
    if expected is not None:
        missing = missing_seeds(run_dir, expected)
        record = json.loads(data)
        record["missing"] = missing
        data = json.dumps(record, indent=1) + "\n"
        if missing:
            lines = text.splitlines()
            listed = ", ".join(str(s) for s in missing)
            lines[3:3] = [f"Missing: {len(missing)} of {len(expected)} expected seed(s) left "
                          f"no result: {listed}", ""]  # fmt: skip
            text = "\n".join(lines) + "\n"
    (run_dir / "summary.json").write_text(data, encoding="utf-8", newline="\n")
    (run_dir / "summary.md").write_text(text, encoding="utf-8", newline="\n")
    return text.splitlines()[2]
