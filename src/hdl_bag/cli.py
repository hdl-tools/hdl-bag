# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Command-line entry point: ``bag``."""

from pathlib import Path

import click

from hdl_bag import __version__, ir
from hdl_bag.checks import CheckResult, run_checks
from hdl_bag.flows import Report, compile_design, lint
from hdl_bag.gen import MissingRtl, Outputs, generate, load_outputs, sources
from hdl_bag.gen.render import GenerateError
from hdl_bag.reader import ReaderError, read_workbook
from hdl_bag.template import write_template


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
    """Write a blank spec workbook, or the demo_ss sample."""
    output.parent.mkdir(parents=True, exist_ok=True)
    write_template(output, sample=sample)
    click.echo(f"wrote {output}")


def _checked(spec: Path) -> CheckResult:
    """Read and check SPEC, printing every finding; exit 1 on a reader or check error."""
    try:
        raw = read_workbook(spec)
    except ReaderError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    result = run_checks(raw, spec.parent)
    for f in result.findings:
        click.echo(f"{f.severity}: {f.message}")
    click.echo(f"{spec.name}: {len(result.errors)} error(s), {len(result.warnings)} warning(s)")
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
    """SPEC (a workbook, checked and resolved, or a .ir.yaml) and its directory."""
    if spec.suffix in (".yaml", ".yml"):
        return ir.load(spec), spec.parent
    return ir.resolve(_checked(spec)), spec.parent


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
    """Lint a generated OUTDIR with every tool; exit 1 on any unwaived finding."""
    out = load_outputs(outdir)
    _report(lint(sources(out), out.top, outdir))


@main.command()
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_OUTPUT
@_STYLE
def flow(spec: Path, output: Path | None, style: str | None) -> None:
    """check -> generate -> lint -> compile. Simulation and formal come in Phases 4 and 5."""
    out = _generate(spec, output, style)
    _report(lint(sources(out), out.top, out.root))
    _report(compile_design(sources(out), out.top, out.root))


if __name__ == "__main__":
    main()
