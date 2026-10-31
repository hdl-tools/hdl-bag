# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
from click.testing import CliRunner

from hdl_bag import __version__
from hdl_bag.cli import main


def test_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output == f"bag, version {__version__}\n"
