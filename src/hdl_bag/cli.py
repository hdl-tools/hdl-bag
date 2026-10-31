# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Command-line entry point: ``bag``."""

from pathlib import Path

import click

from hdl_bag import __version__
from hdl_bag.checks import run_checks
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


@main.command()
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def check(spec: Path) -> None:
    """Read SPEC and run every check. Exit status 1 on any error; warnings are printed."""
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
