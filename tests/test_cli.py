# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
from pathlib import Path

import openpyxl
import pytest
from click.testing import CliRunner
from dsl import SAMPLE_WRAP, unexpected

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
    *warnings, summary = result.output.splitlines()
    assert summary == "demo_ss.xlsx: 0 error(s), 2 warning(s)"
    assert unexpected(warnings, SAMPLE_WRAP) == []


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


def test_sample_then_check_from_an_empty_directory(tmp_path: Path) -> None:
    """The quick start: the sample names header.txt as its File header, so it is written too."""
    out = tmp_path / "spec.xlsx"
    assert CliRunner().invoke(main, ["template", "--sample", "-o", str(out)]).exit_code == 0
    golden = (GOLDEN.parent / "header.txt").read_text(encoding="utf-8")
    assert (tmp_path / "header.txt").read_text(encoding="utf-8") == golden
    result = CliRunner().invoke(main, ["check", str(out)])
    assert result.exit_code == 0, result.output


def test_sample_keeps_an_existing_header(tmp_path: Path) -> None:
    (tmp_path / "header.txt").write_text("// mine\n")
    out = tmp_path / "spec.xlsx"
    result = CliRunner().invoke(main, ["template", "--sample", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "header.txt").read_text() == "// mine\n"
    assert "kept the existing header.txt" in result.output


GOLDEN_IR = Path(__file__).parent / "golden" / "demo_ss.ir.yaml"


def _ir_copy(tmp_path: Path, old: str = "", new: str = "") -> Path:
    text = GOLDEN_IR.read_text(encoding="utf-8")
    if old:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "demo_ss.ir.yaml"
    path.write_text(text, encoding="utf-8")
    (tmp_path / "header.txt").write_text("// header\n")
    return path


def test_generate_refuses_an_ir_of_another_schema(tmp_path: Path) -> None:
    from hdl_bag.template import SCHEMA_VERSION

    ir_path = _ir_copy(tmp_path, f"schema: '{SCHEMA_VERSION}'", "schema: '0.4'")
    result = CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert f"schema 0.4; this bag reads schema {SCHEMA_VERSION}" in result.output
    assert "Traceback" not in result.output


def test_generate_refuses_an_ir_it_cannot_read(tmp_path: Path) -> None:
    ir_path = _ir_copy(tmp_path, "  sv_std: IEEE 1800-2017\n", "")
    result = CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "is not a valid IR" in result.output and "sv_std" in result.output


def test_generate_from_an_ir_runs_the_checks(tmp_path: Path) -> None:
    """An IR edited by hand is checked like a workbook before anything is generated."""
    ir_path = _ir_copy(tmp_path, "  mhz: 100\n", "  mhz: 0\n")
    result = CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "error: Clocks row" in result.output
    assert not (tmp_path / "out").exists()


def test_lint_uses_the_project_lint_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hdl_bag.cli as cli
    from hdl_bag.flows import Report

    ir_path = _ir_copy(tmp_path, "  lint_tools:\n    - slang\n    - verilator\n    - verible\n",
                       "  lint_tools:\n    - verilator\n")  # fmt: skip
    seen: list[tuple[str, ...]] = []

    def fake_lint(*args: object, tools: tuple[str, ...], **kw: object) -> Report:
        seen.append(tuple(tools))
        return Report("lint", (), tuple(tools))

    monkeypatch.setattr(cli, "lint", fake_lint)
    out = tmp_path / "out"
    assert CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(out)]).exit_code == 0
    result = CliRunner().invoke(main, ["lint", str(out)])
    assert result.exit_code == 0, result.output
    assert seen == [("verilator",)]
