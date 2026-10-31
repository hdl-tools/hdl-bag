# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Command-line entry point: ``bag``."""

from pathlib import Path

import click

from hdl_bag import __version__
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
    write_template(output, sample=sample)
    click.echo(f"wrote {output}")
