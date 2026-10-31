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


GOLDEN = Path(__file__).parent / "golden" / "demo_ss.xlsx"


def test_check_golden_passes() -> None:
    result = CliRunner().invoke(main, ["check", str(GOLDEN)])
    assert result.exit_code == 0, result.output
    assert result.output == "demo_ss.xlsx: 0 error(s), 0 warning(s)\n"


def test_check_reports_errors_and_fails(tmp_path: Path) -> None:
    wb = openpyxl.load_workbook(GOLDEN)
    wb["Clocks"]["B5"].value = None
    wb["Fabrics"]["H7"].value = "CUT_ALL_AX"  # warning only
    path = tmp_path / "spec.xlsx"
    wb.save(path)
    (tmp_path / "header.txt").write_text("// header\n")
    result = CliRunner().invoke(main, ["check", str(path)])
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "error: Clocks row 5: Freq (MHz) is required (VALUE-MISSING)",
        "warning: Fabrics row 7: Latency mode 'CUT_ALL_AX' does not apply outside a crossbar; set n/a (FAB-NA-SETTING)",
        "spec.xlsx: 1 error(s), 1 warning(s)",
    ]


def test_check_reader_error(tmp_path: Path) -> None:
    wb = openpyxl.load_workbook(GOLDEN)
    del wb["Topology"]
    path = tmp_path / "spec.xlsx"
    wb.save(path)
    result = CliRunner().invoke(main, ["check", str(path)])
    assert result.exit_code == 1
    assert "Topology: sheet missing (READ-SHEET)" in result.output
