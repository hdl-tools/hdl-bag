# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
from pathlib import Path

import openpyxl
from click.testing import CliRunner

from hdl_bag import __version__
from hdl_bag.cli import main


def test_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output == f"bag, version {__version__}\n"


def test_template_writes_workbook(tmp_path: Path) -> None:
    out = tmp_path / "spec.xlsx"
    result = CliRunner().invoke(main, ["template", "--sample", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert openpyxl.load_workbook(out)["Project"]["B5"].value == "demo_ss"


def test_template_creates_output_directory(tmp_path: Path) -> None:
    out = tmp_path / "new" / "dir" / "spec.xlsx"
    result = CliRunner().invoke(main, ["template", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
