# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Command-line entry point: ``bag``."""

import click

from hdl_bag import __version__


@click.group()
@click.version_option(__version__, prog_name="bag")
def main() -> None:
    """BAG - Bus Architecture Generator."""
