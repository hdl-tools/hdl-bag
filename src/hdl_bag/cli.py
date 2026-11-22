# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Command-line entry point: ``bag``."""

import hashlib
import re
import tempfile
from pathlib import Path

import click

from hdl_bag import __version__, ir
from hdl_bag.checks import CheckResult, run_checks
from hdl_bag.flows import Report, SimUnavailable, compile_design, lint, run_formal, run_tests
from hdl_bag.gen import MissingRtl, Outputs, generate, include_dirs, load_outputs, sources
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.to_workbook import to_spec_data, to_workbook
from hdl_bag.reader import ReaderError, read_workbook
from hdl_bag.template import write_template
from hdl_bag.template.sample_demo_ss import SAMPLE_HEADER, WRAPPER


@click.group()
@click.version_option(__version__, prog_name="bag")
def main() -> None:
    """BAG - Bus Architecture Generator."""


@main.command()
@click.option(
    "--sample", is_flag=True, help="Write the demo_ss sample instead of a blank template."
)
@click.option(
    "-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path),
    help="Workbook to write (.xlsx).",
)  # fmt: skip
def template(sample: bool, output: Path) -> None:
    """Write a blank spec workbook, or the demo_ss sample (with the File header it names)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    write_template(output, sample=sample)
    click.echo(f"wrote {output}")
    if sample:
        header = output.parent / WRAPPER["File header"]
        if header.exists():
            click.echo(f"kept the existing {header.name} (the sample's File header)")
        else:
            header.write_text(SAMPLE_HEADER, encoding="utf-8", newline="\n")
            click.echo(f"wrote {header}")


def _checked(spec: Path, workbook: Path | None = None, label: str = "") -> CheckResult:
    """Read and check SPEC (or WORKBOOK, written from it), printing every finding; exit 1 on
    a reader or check error. File header paths are relative to SPEC's directory."""
    try:
        raw = read_workbook(workbook or spec)
    except ReaderError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    result = run_checks(raw, spec.parent)
    for f in result.findings:
        click.echo(f"{f.severity}: {f.message}")
    errors, warnings = len(result.errors), len(result.warnings)
    click.echo(f"{spec.name}{label}: {errors} error(s), {warnings} warning(s)")
    if result.errors:
        raise SystemExit(1)
    return result


@main.command()
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def check(spec: Path) -> None:
    """Read SPEC and run every check. Exit status 1 on any error; warnings are printed."""
    _checked(spec)


@main.command(name="ir")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path),
    help="IR file to write (.ir.yaml).",
)  # fmt: skip
def ir_command(spec: Path, output: Path) -> None:
    """Check SPEC, then write its canonical YAML IR."""
    result = _checked(spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    ir.dump(ir.resolve(result), output)
    click.echo(f"wrote {output}")


def _load(spec: Path) -> tuple[ir.Spec, Path]:
    """SPEC (a workbook or a .ir.yaml), checked, and its directory. An IR, which may have been
    edited by hand, gets every check a workbook does: through the workbook it converts to."""
    if spec.suffix not in (".yaml", ".yml"):
        return ir.resolve(_checked(spec)), spec.parent
    try:
        loaded = ir.load(spec)
    except ir.IrError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    with tempfile.TemporaryDirectory() as tmp:
        # A fixed name: the project name is spec data, and NAME-IDENT rejects a name that is a
        # path only after this write, which would already have overwritten whatever it named.
        workbook = Path(tmp) / "spec.xlsx"
        to_workbook(loaded).save(workbook)
        _checked(spec, workbook, " (checked as the workbook it converts to)")
    return loaded, spec.parent


NO_RTL = "no RTL generated (Project > Generate RTL is N): nothing to lint, simulate or prove"


def _generate(spec_path: Path, output: Path | None, style: str | None) -> Outputs:
    spec, spec_dir = _load(spec_path)
    outdir = output or spec_dir / spec.project.output_dir
    try:
        out = generate(spec, outdir, spec_dir, style)
    except MissingRtl as exc:
        click.echo(f"wrote collateral to {outdir}")
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    except (GenerateError, OSError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if not out.top:
        click.echo(f"wrote {len(out.files)} file(s) to {outdir}")
        click.echo(NO_RTL)
        return out
    click.echo(f"wrote {len(out.files)} file(s) to {outdir} (top {out.top})")
    if spec.project.outputs.tests and out.tests is None:
        click.echo("no generated tests: BAG does not know how a user-supplied fabric behaves")
    return out


def _outputs(outdir: Path) -> Outputs:
    """OUTDIR's recorded outputs, or an error line and exit 1 when it is not an output
    directory of bag generate."""
    try:
        return load_outputs(outdir)
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc


def _report(report: Report) -> None:
    click.echo(report.format(), nl=False)
    if not report.ok:
        raise SystemExit(1)


_STYLE = click.option(
    "--style", type=click.Choice(["flat", "sv-interface"]),
    help="Override Wrapper > Port style.",
)  # fmt: skip
_OUTPUT = click.option(
    "-o", "--output", type=click.Path(file_okay=False, path_type=Path),
    help="Output directory (default: Project > Output directory, next to the spec).",
)  # fmt: skip


@main.command(name="generate")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_OUTPUT
@_STYLE
def generate_command(spec: Path, output: Path | None, style: str | None) -> None:
    """Generate RTL, wrapper and collateral from SPEC (.xlsx or .ir.yaml)."""
    _generate(spec, output, style)


@main.command(name="lint")
@click.argument("outdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def lint_command(outdir: Path) -> None:
    """Lint a generated OUTDIR with Project > Lint tools; exit 1 on any unwaived finding."""
    out = _outputs(outdir)
    _report(lint(sources(out), out.top, outdir, tools=out.lint_tools, incdirs=include_dirs(out)))


def _sim(out: Outputs, test_filter: str | None = None, ready_shim: bool = False) -> None:
    try:
        _report(run_tests(out, test_filter=test_filter, ready_shim=ready_shim))
    except (SimUnavailable, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc


@main.command(name="sim")
@click.argument("outdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--filter", "test_filter", help="Run only the tests this regex finds.")
@click.option("--ready-shim", is_flag=True,
              help="Run traffic with each AXI READY driven from its VALID.")  # fmt: skip
def sim_command(outdir: Path, test_filter: str | None, ready_shim: bool) -> None:
    """Run a generated OUTDIR's cocotb tests; exit 1 on any failure. Needs hdl-bag[sim]."""
    _sim(_outputs(outdir), test_filter, ready_shim)


def _formal(out: Outputs, timeout: float | None = None) -> None:
    try:
        _report(run_formal(out, timeout=timeout))
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc


@main.command(name="formal")
@click.argument("outdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--timeout", type=click.FloatRange(min=1), help="Seconds allowed per job.")
def formal_command(outdir: Path, timeout: float | None) -> None:
    """Run a generated OUTDIR's SymbiYosys jobs; exit 1 on any finding (a failed assertion, an
    unreached cover, an error or a timeout)."""
    _formal(_outputs(outdir), timeout)


@main.command()
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_OUTPUT
@_STYLE
@click.option(
    "--formal",
    "with_formal",
    is_flag=True,
    help="Also run the generated SymbiYosys jobs after sim (as bag formal).",
)
@click.option("--formal-timeout", type=click.FloatRange(min=1),
              help="Seconds allowed per formal job (with --formal).")  # fmt: skip
def flow(spec: Path, output: Path | None, style: str | None, with_formal: bool,
         formal_timeout: float | None) -> None:  # fmt: skip
    """check -> generate -> lint -> elaborate -> sim, then formal with --formal. Formal is
    opt-in here because a large design's proofs can take many minutes; bag formal runs it on
    its own."""
    if formal_timeout is not None and not with_formal:
        raise click.UsageError("--formal-timeout needs --formal")
    out = _generate(spec, output, style)
    if not out.top:
        return  # D100: _generate said why
    _report(lint(sources(out), out.top, out.root, tools=out.lint_tools, incdirs=include_dirs(out)))
    # Simulation builds the C++ model itself, so build it here only if nothing will.
    _report(compile_design(sources(out), out.top, out.root, incdirs=include_dirs(out),
                           build=out.tests is None))  # fmt: skip
    if out.tests is None:
        click.echo(
            "no generated tests (Generate cocotb unit tests is N, or a user-supplied fabric)"
        )
    else:
        _sim(out)
    if not with_formal:
        return
    if out.formal:
        _formal(out, formal_timeout)
    else:
        click.echo("no generated formal (Generate formal is N, or a user-supplied fabric)")


def _shard(_ctx: click.Context, _param: click.Parameter, value: str) -> tuple[int, int]:
    i, sep, k = value.partition("/")
    if not sep or not i.isdigit() or not k.isdigit():
        raise click.BadParameter(f"'{value}' is not i/k, e.g. 2/4")
    return int(i), int(k)


@main.command()
@click.option(
    "--seeds",
    "count",
    type=click.IntRange(min=1),
    help="Run this many seeds: BASE * 10000 + j for j below the count.",
)
@click.option(
    "--seed-base",
    "base",
    type=click.IntRange(min=0),
    default=0,
    show_default=True,
    help="The run's base, e.g. the date (20261117).",
)
@click.option(
    "--shard",
    default="1/1",
    callback=_shard,
    show_default=True,
    help="Run shard i of k: the seeds whose j is i-1 modulo k.",
)
@click.option("--seed", "one", type=click.IntRange(min=0), help="Rerun one seed.")
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("out/regress"),
    show_default=True,
    help="Run directory.",
)
@click.option(
    "--formal-budget",
    type=click.FloatRange(min=1),
    default=600,
    show_default=True,
    help="Seconds of formal per seed: the decode job, then the rest in turn (D96).",
)
@click.option("--no-formal", is_flag=True, help="Skip formal.")
def regress(
    count: int | None,
    base: int,
    shard: tuple[int, int],
    one: int | None,
    output: Path,
    formal_budget: float,
    no_formal: bool,
) -> None:
    """Random configurations, each through check, generate, lint, compile, the generated
    tests and formal. Writes OUTPUT/<seed>/result.json (with the workbook, the IR and the
    repro commands), and after each seed OUTPUT/summary.json and summary.md over every seed
    in OUTPUT. Exit 1 on any failure or generator bug among the seeds run; formal work cut
    off by the budget is a timeout, reported but not a failure."""
    from hdl_bag.regress.run import run_seed, seeds, write_summary

    if (count is None) == (one is None):
        raise click.UsageError("give --seeds N or --seed S")
    try:
        todo = [one] if one is not None else seeds(base, count or 0, *shard)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    output.mkdir(parents=True, exist_ok=True)
    failed = False
    line = ""
    for seed in todo:
        r = run_seed(seed, output, budget=formal_budget, formal=not no_formal)
        click.echo(f"seed {seed}: {r.status}{f' ({r.step})' if r.step else ''}")
        failed |= r.status in ("fail", "generator")
        line = write_summary(output)  # a run cut short still leaves a summary
    click.echo(line)
    if failed:
        raise SystemExit(1)


STEP_NAMES = ("lint", "sim", "formal")


def _step(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if (value is not None and value not in STEP_NAMES and not value.startswith("test=")) \
            or value == "test=":  # fmt: skip
        raise click.BadParameter(f"'{value}' is not lint, sim, formal or test=<name>")
    return value


@main.command(name="shrink")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--step", callback=_step, metavar="STEP",
              help="The failing step: lint, sim, formal or test=<name>. Default: the first of "
                   "lint, sim and formal that fails.")  # fmt: skip
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path),
              help="Where to write the shrunk IR. Default: SPEC with .shrunk.ir.yaml.")  # fmt: skip
@click.option("--budget", type=click.IntRange(min=1), default=200, show_default=True,
              help="The most step runs to spend.")  # fmt: skip
@click.option("--timeout", type=click.FloatRange(min=1), help="Seconds allowed per formal task.")
def shrink_command(spec: Path, step: str | None, output: Path | None, budget: int,
           timeout: float | None) -> None:  # fmt: skip
    """Delta-debug a failing SPEC (an .ir.yaml) down to a small IR that still fails the same
    way, and write it. Runs the EDA tools of the step many times; exit 1 if SPEC cannot be
    shrunk (it does not fail, a tool is missing, ...)."""
    from hdl_bag.shrink import ShrinkError

    if spec.suffix not in (".yaml", ".yml"):
        raise click.UsageError(f"{spec} is not an IR: run bag ir on the workbook first")
    if output is None:
        output = spec.with_name(re.sub(r"(\.ir)?\.ya?ml$", "", spec.name) + ".shrunk.ir.yaml")
    try:
        _shrink(spec, step, output, budget, timeout)
    except ShrinkError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc


def _shrink(spec_path: Path, step: str | None, output: Path, budget: int,
            timeout: float | None) -> None:  # fmt: skip
    from hdl_bag.ir.model import Provenance
    from hdl_bag.shrink import (
        NoFailure,
        ShrinkError,
        Step,
        ToolUnavailable,
        build,
        discover,
        make_oracle,
        shrink,
    )
    from hdl_bag.shrink import Signature as Sig

    try:
        loaded = ir.load(spec_path)
    except ir.IrError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    spec_dir = spec_path.parent
    data = to_spec_data(loaded)  # the baseline is the round trip: to_spec_data keeps one alias
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        base = build(data, work / "base", spec_dir)
        if isinstance(base, str):
            raise ShrinkError(f"the spec does not get past {base}: run bag flow on it first")
        spec, out = base
        found: tuple[str, Step, Sig] | None = None
        passed: list[str] = []
        unassessed: list[tuple[str, str]] = []
        for name in (step,) if step else STEP_NAMES:
            try:
                run, sig = discover(name, spec, out, timeout)
                found = (name, run, sig)
                break
            except NoFailure:
                if step:
                    raise
                passed.append(name)
            except ToolUnavailable as exc:
                if step:
                    raise
                unassessed.append((name, str(exc)))
        if found is None:
            if not unassessed:
                raise ShrinkError(
                    "lint, sim and formal all pass on the spec, so there is nothing to shrink"
                )
            why = "; ".join(f"{n} could not be assessed ({m})" for n, m in unassessed)
            ok = f"{' and '.join(passed)} pass on the spec, but " if passed else ""
            raise ShrinkError(
                f"{ok}{why}: nothing to shrink in what ran, so install what is missing "
                "and run again"
            )
        name, run, sig = found
        kind = "sim" if name.startswith("test=") else name
        target = f" {sig.target}" if sig.target else ""
        click.echo(f"baseline: {kind}{target} fails with {', '.join(sorted(sig.rules))}")
        counts: dict[str, int] = {}

        def kept(line: str) -> None:
            if line.startswith("kept "):
                click.echo(f"kept: {line[9:]}")

        result = shrink(
            data, make_oracle(work / "cand", spec_dir, run, sig, counts), budget=budget, log=kept,
        )  # fmt: skip
        final = build(result.data, work / "final", spec_dir)
        if isinstance(final, str):
            raise ShrinkError(f"the shrunk spec does not get past {final}: this is a bag bug")
    click.echo(f"{result.passes} pass(es), {result.runs} run(s); kept {len(result.kept)} "
               f"edit(s); {counts.get('check', 0)} candidate(s) failed bag check")  # fmt: skip
    unbuilt = sum(n for why, n in counts.items() if why != "check")
    if unbuilt:
        click.echo(f"note: {unbuilt} candidate(s) passed bag check but did not resolve or generate")
    if result.exhausted:
        click.echo(f"stopped at the budget of {budget} runs; the result may shrink further "
                   "(--budget)")  # fmt: skip
    # The candidate's provenance names a temp workbook; the truth is the file this came from.
    prov = Provenance(bag_version=__version__, source=spec_path.name,
                      sha256=hashlib.sha256(spec_path.read_bytes()).hexdigest())  # fmt: skip
    output.parent.mkdir(parents=True, exist_ok=True)
    ir.dump(final[0].model_copy(update={"provenance": prov}), output)
    click.echo(f"wrote {output}")
    click.echo(f"reproduce: bag flow {output}")


if __name__ == "__main__":
    main()
