# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Command-line entry point: ``bag``."""

import tempfile
from pathlib import Path

import click

from hdl_bag import __version__, ir
from hdl_bag.checks import CheckResult, run_checks
from hdl_bag.flows import Report, SimUnavailable, compile_design, lint, run_formal, run_tests
from hdl_bag.gen import MissingRtl, Outputs, generate, include_dirs, load_outputs, sources
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.to_workbook import to_workbook
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
        workbook = Path(tmp) / f"{loaded.project.name}.xlsx"
        to_workbook(loaded).save(workbook)
        _checked(spec, workbook, " (checked as the workbook it converts to)")
    return loaded, spec.parent


def _generate(spec_path: Path, output: Path | None, style: str | None) -> Outputs:
    spec, spec_dir = _load(spec_path)
    outdir = output or spec_dir / spec.project.output_dir
    try:
        out = generate(spec, outdir, spec_dir, style)
    except MissingRtl as exc:
        click.echo(f"wrote collateral to {outdir}")
        click.echo(f"error: {exc} (AXI templates arrive in Phase 3)", err=True)
        raise SystemExit(1) from exc
    except (GenerateError, OSError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"wrote {len(out.files)} file(s) to {outdir} (top {out.top})")
    if spec.project.outputs.tests and out.tests is None:
        click.echo("no generated tests: BAG does not know how a user-supplied fabric behaves")
    return out


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
    out = load_outputs(outdir)
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
    _sim(load_outputs(outdir), test_filter, ready_shim)


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
    _formal(load_outputs(outdir), timeout)


@main.command()
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_OUTPUT
@_STYLE
def flow(spec: Path, output: Path | None, style: str | None) -> None:
    """check -> generate -> lint -> elaborate -> sim -> formal."""
    out = _generate(spec, output, style)
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
    if out.formal:
        _formal(out)
    else:
        click.echo("no generated formal (Generate formal is N, or a user-supplied fabric)")


if __name__ == "__main__":
    main()
