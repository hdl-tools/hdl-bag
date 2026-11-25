# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Turns a `SpecData` candidate into a yes/no answer for `bag shrink`: write the workbook,
check, resolve and generate it, run one EDA step, and compare its failure with the baseline.

A candidate runs only the failing test or formal task, not the whole step, which is what makes
shrinking a formal failure affordable. A target that has vanished does not reproduce.
"""

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hdl_bag import ir
from hdl_bag.checks import run_checks
from hdl_bag.flows import Report, SimUnavailable, lint, run_sby, run_tests
from hdl_bag.flows.sim import SEED, cocotb_tests
from hdl_bag.gen import MissingRtl, Outputs, generate, include_dirs, sources
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.model import Provenance
from hdl_bag.reader import read_workbook
from hdl_bag.regress.run import formal_tasks
from hdl_bag.shrink.engine import Oracle
from hdl_bag.template.builder import build_workbook
from hdl_bag.template.data import SpecData


@dataclass(frozen=True)
class Signature:
    rules: frozenset[str]  # unwaived rule IDs of the step's report
    target: str | None  # sim: test name; formal: "<job stem>:<task>"; lint: None
    # What the findings say, with what moves as a spec shrinks taken out (_shape): the same
    # rule and test can fail another way, and a shrink must not drift to it (seed 202611250040).
    shape: frozenset[str] = frozenset()


def _shape(message: str) -> str:
    """MESSAGE without what varies with the spec: the cocotb seed, Verilator's generated names
    and loop paths, lists, sets and dicts (addresses read, pins; one a report cut short leaves
    open too), and addresses and multi-digit numbers (lines, sizes). Single digits stay: they
    are names (m0, s5), responses (2, 3) and the route test's counts, (4, 0) or (0, 0)."""
    text = re.sub(r" \(seed \d+\)", "", message)
    text = re.sub(r"__Vdfg\w+|\(loop through .*?\)", "#", text)
    text = re.sub(r"\[[^\]]*\]", "[…]", text)
    text = re.sub(r"\{[^}]*\}", "{…}", text)
    text = re.sub(r"[\[{][^\]}]*$", "…", text)  # cut short inside one
    text = re.sub(r"\.\.\.$", "…", text)  # cut short: what is left is a prefix
    return re.sub(r"0x[0-9a-fA-F_]+|\d\d+", "#", text)


def _matches(got: str, base: str) -> bool:
    """A message cut short (ends with …) matches any that starts the same."""
    return got == base or (base.endswith("…") and got.startswith(base[:-1]))


def same_failure(got: Signature | None, base: Signature) -> bool:
    """Whether GOT reproduces BASE: the same rules and target, and still at least one of its
    messages (a shrink may lose a second finding of a rule, but not drift to another)."""
    return (
        got is not None and got.rules == base.rules and got.target == base.target
        and (not base.shape or any(_matches(g, b) for g in got.shape for b in base.shape))
    )  # fmt: skip


# None: the step passed, or its target is gone.
Step = Callable[[ir.Spec, Outputs], Signature | None]


class ShrinkError(Exception):
    """The failure cannot be shrunk; the message says why and what to do."""


class NoFailure(ShrinkError):
    """The step passes on the original spec: the caller may try another step."""


class ToolUnavailable(ShrinkError):
    """The step cannot be assessed on this machine, as distinct from `NoFailure`, where it was
    assessed and passed. The caller may try another step."""


def build(data: SpecData, work: Path, spec_dir: Path) -> tuple[ir.Spec, Outputs] | str:
    """Write, check, resolve and generate DATA under WORK. A short reason string if the
    candidate is not a valid design: "check", "resolve", "generate" or "crash"."""
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    stage = "crash"
    try:
        # A fixed name, as in cli.py: the project name is spec data, and the checks that reject
        # a name which is a path run after this write.
        path = work / "spec.xlsx"
        build_workbook(data=data).save(path)
        checked = run_checks(read_workbook(path), spec_dir)
        stage = "check"
        if checked.errors:
            return stage
        stage = "resolve"
        spec = ir.resolve(checked)
        stage = "generate"
        return spec, generate(spec, work / "out", spec_dir)
    except (ir.ResolveError, GenerateError, MissingRtl, OSError):
        return stage
    except Exception:
        return "crash"


def _signature(report: Report, target: str | None) -> Signature | None:
    if report.ok:
        return None
    return Signature(frozenset(f.rule for f in report.unwaived), target,
                     frozenset(_shape(f.message) for f in report.unwaived))  # fmt: skip


def lint_step() -> Step:
    def step(_spec: ir.Spec, out: Outputs) -> Signature | None:
        root = out.root.resolve()
        report = lint(
            [root / f for f in sources(out)], out.top, root,
            tools=out.lint_tools, incdirs=include_dirs(out),
        )  # fmt: skip
        return _signature(report, None)

    return step


def _run_sim(out: Outputs, test: str | None, seed: int | None) -> Report:
    flt = rf"\.{re.escape(test)}$" if test else None
    return (
        run_tests(out, test_filter=flt)
        if seed is None
        else run_tests(out, test_filter=flt, seed=seed)
    )


def sim_step(test: str | None, seed: int | None = None) -> Step:
    def step(_spec: ir.Spec, out: Outputs) -> Signature | None:
        try:
            return _signature(_run_sim(out, test, seed), test)
        except (ValueError, SimUnavailable):
            return None  # the test, or the generated tests, are gone

    return step


def _find_job(out: Outputs, stem: str, task: str | None) -> str | None:
    return next((rel for rel, t in formal_tasks(out) if Path(rel).stem == stem and t == task), None)


def formal_step(stem: str, task: str | None, timeout: float | None) -> Step:
    def step(_spec: ir.Spec, out: Outputs) -> Signature | None:
        rel = _find_job(out, stem, task)
        if rel is None:
            return None
        root = out.root.resolve()
        try:
            report = run_sby(root / rel, root, timeout=timeout, task=task)
        except ValueError:
            return None
        return _signature(report, f"{stem}:{task}" if task else stem)

    return step


def _vet(sig: Signature | None, what: str) -> Signature:
    """The baseline failure SIG, or a ShrinkError if it cannot be shrunk."""
    if sig is None:
        raise NoFailure(f"{what} does not fail on the original spec, so there is nothing to shrink")
    if "TOOL-MISSING" in sig.rules:
        raise ShrinkError(
            f"{what} reports TOOL-MISSING: a tool is not installed, so every candidate would "
            "'reproduce'. Install it (or run in the tools image) and try again"
        )
    if sig.rules == {"FORMAL-TIMEOUT"}:
        raise ShrinkError(
            f"{what} only ran out of time (FORMAL-TIMEOUT), which is not deterministic and so "
            "cannot be shrunk. Raise --timeout or pick a task that fails"
        )
    return sig


def _discover_sim(out: Outputs, seed: int | None) -> tuple[Step, Signature]:
    used = SEED if seed is None else seed
    report = _run_sim(out, None, seed)
    if report.ok:
        raise NoFailure("sim does not fail on the original spec, so there is nothing to shrink")
    failed = {f.message for f in report.unwaived if f.rule == "TEST-FAILED"}
    target = next(
        (n for n in cocotb_tests(out.root.resolve() / str(out.tests))
         if f"{n} failed (seed {used})" in failed), None,
    )  # fmt: skip
    if target is None:
        return sim_step(None, seed), _vet(_signature(report, None), "sim")
    step = sim_step(target, seed)
    return step, _vet(_signature(_run_sim(out, target, seed), target), f"sim test {target}")


def _discover_formal(out: Outputs, timeout: float | None) -> tuple[Step, Signature]:
    root = out.root.resolve()
    for rel, task in formal_tasks(out):
        report = run_sby(root / rel, root, timeout=timeout, task=task)
        if not report.ok:
            stem = Path(rel).stem
            name = f"{stem}:{task}" if task else stem
            return formal_step(stem, task, timeout), _vet(
                _signature(report, name), f"formal {name}"
            )
    raise NoFailure("formal does not fail on the original spec, so there is nothing to shrink")


def discover(
    step: str, spec: ir.Spec, out: Outputs, timeout: float | None, seed: int | None = None
) -> tuple[Step, Signature]:
    """Identify the failing target once, on the baseline: STEP is "lint", "sim", "formal" or
    "test=<name>". Returns the step to run per candidate and the failure to match."""
    try:
        if step == "lint":
            fn = lint_step()
            return fn, _vet(fn(spec, out), "lint")
        if step == "sim":
            return _discover_sim(out, seed)
        if step == "formal":
            return _discover_formal(out, timeout)
        if step.startswith("test="):
            name = step.removeprefix("test=")
            report = _run_sim(out, name, seed)
            return sim_step(name, seed), _vet(_signature(report, name), f"sim test {name}")
    except SimUnavailable as exc:
        raise ToolUnavailable(f"cannot run the simulation: {exc}. Install cocotb") from exc
    except ValueError as exc:
        raise ShrinkError(str(exc)) from exc
    raise ShrinkError(f"unknown step {step!r}: use lint, sim, formal or test=<name>")


def _key(spec: ir.Spec) -> str:
    """The memo key: the IR text without provenance, whose workbook hash and path vary."""
    return ir.dumps(spec.model_copy(update={"provenance": Provenance(
        bag_version="", source="", sha256="")}))  # fmt: skip


def make_oracle(
    work: Path, spec_dir: Path, step: Step, baseline: Signature, counts: dict[str, int]
) -> Oracle:
    """An oracle for `shrink`. COUNTS tallies the candidates refused, by reason."""
    memo: dict[str, bool] = {}

    def oracle(data: SpecData) -> bool | None:
        built = build(data, work, spec_dir)
        if isinstance(built, str):
            counts[built] = counts.get(built, 0) + 1
            return None
        spec, out = built
        key = _key(spec)
        if key not in memo:
            memo[key] = same_failure(step(spec, out), baseline)
        return memo[key]

    return oracle
