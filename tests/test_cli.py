# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
import dataclasses
import json
from pathlib import Path

import openpyxl
import pytest
from click.testing import CliRunner
from dsl import SAMPLE_WRAP, unexpected

from hdl_bag import __version__
from hdl_bag.cli import main
from hdl_bag.template import build_workbook
from hdl_bag.template.sample_demo_ss import SAMPLE


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
    # Two WRAP refusals, and two PSTRB ties on 32-bit APB4 slaves (D106).
    assert summary == "demo_ss.xlsx: 0 error(s), 4 warning(s)"
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


@pytest.mark.parametrize("command", ["lint", "sim", "formal"])
@pytest.mark.parametrize(("manifest", "message"), [
    (None, "no bag_outputs.json here: give the output directory that bag generate wrote"),
    ("{not json", "bag_outputs.json cannot be read: run bag generate again"),
    ('{"top": null, "files": [], "filelist": ""}', "no RTL was generated (see bag_outputs.json)"),
    ('{"top": "x"}', "bag_outputs.json cannot be read: run bag generate again"),
    ('{"top": "x", "files": {}, "filelist": 3}', "bag_outputs.json cannot be read: run bag generate again"),
    # tuple("slang") is five one-character tool names, which reached lint() as unknown tools.
    ('{"top": "x", "files": {}, "filelist": "x.f", "lint_tools": "slang"}', "bag_outputs.json cannot be read: run bag generate again"),
])  # fmt: skip
def test_a_folder_that_is_not_an_output_directory_is_an_error_not_a_traceback(
    tmp_path: Path, command: str, manifest: str | None, message: str
) -> None:
    """PR 109 review: bag lint, sim and formal raised a raw FileNotFoundError or ValueError
    on such a folder. Each gives one error line and exit status 1."""
    if manifest is not None:
        (tmp_path / "bag_outputs.json").write_text(manifest, encoding="utf-8")
    result = CliRunner().invoke(main, [command, str(tmp_path)])
    assert (result.exit_code, result.output) == (1, f"error: {tmp_path}: {message}\n")
    assert isinstance(result.exception, SystemExit)


def test_generate_refuses_a_spec_with_no_fabric_and_writes_nothing(tmp_path: Path) -> None:
    """The issue: such a spec checked clean and generate wrote 17 files that do nothing."""
    path = tmp_path / "spec.xlsx"
    build_workbook(data=dataclasses.replace(SAMPLE, fabrics=[])).save(path)
    (tmp_path / "header.txt").write_text("// header\n")
    out = tmp_path / "out"
    result = CliRunner().invoke(main, ["generate", str(path), "-o", str(out)])
    assert result.exit_code == 1
    assert "(FAB-NONE)" in result.output
    assert not out.exists()


def test_an_ir_project_name_cannot_choose_where_the_checked_workbook_is_written(
    tmp_path: Path,
) -> None:
    """An IR is converted to a workbook so the checks can run on it. The project name is the
    spec's own data, so it must reach no path: NAME-IDENT rejects it only after the file is
    written, and a name pointing at an existing file destroyed it."""
    victim = tmp_path / "victim.xlsx"
    victim.write_bytes(b"a workbook of the user's own")
    ir_path = _ir_copy(
        tmp_path, "  name: demo_ss\n", f"  name: '{(tmp_path / 'victim').as_posix()}'\n"
    )
    result = CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "NAME-IDENT" in result.output
    assert victim.read_bytes() == b"a workbook of the user's own"


def test_check_on_an_ir_file_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    """bag ir and bag generate take a .ir.yaml and bag check does not, so this is the mistake
    users make. It showed openpyxl's InvalidFileException as a traceback."""
    ir_path = _ir_copy(tmp_path)
    result = CliRunner().invoke(main, ["check", str(ir_path)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert result.output.startswith("error: demo_ss.ir.yaml: not a readable .xlsx workbook")
    assert "READ-FILE" in result.output


def test_a_lint_tool_this_release_does_not_know_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    """A manifest another hdl-bag wrote can name a tool this one has no backend for. lint()
    raises ValueError for it, and bag lint had no handler, unlike bag sim."""
    out = tmp_path / "out"
    ir_path = _ir_copy(tmp_path)
    assert CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(out)]).exit_code == 0
    manifest = out / "bag_outputs.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["lint_tools"] = ["spyglass"]
    manifest.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    result = CliRunner().invoke(main, ["lint", str(out)])
    assert (result.exit_code, result.output) == (1, "error: unknown lint tool(s): spyglass\n")
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("command", ["lint", "sim"])
def test_a_missing_file_list_is_an_error_not_a_traceback(tmp_path: Path, command: str) -> None:
    """lint, sim and flow read the file list after load_outputs returns, and each gave a raw
    FileNotFoundError when the manifest named one that is not there."""
    out = tmp_path / "out"
    ir_path = _ir_copy(tmp_path)
    assert CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(out)]).exit_code == 0
    (out / "demo_ss.f").unlink()
    result = CliRunner().invoke(main, [command, str(out)])
    assert (result.exit_code, result.output) == (
        1, f"error: {out}: the file list demo_ss.f cannot be read: run bag generate again\n"
    )  # fmt: skip
    assert "Traceback" not in result.output


@pytest.mark.parametrize("command", ["lint", "sim"])
def test_a_file_list_entry_that_looks_like_a_flag_is_refused(tmp_path: Path, command: str) -> None:
    """Each file list entry becomes one argv element of the EDA tool, so `--command-file=x`
    reached slang as a flag (#193). No file BAG writes starts with '-', so such an entry is
    refused; absolute and `..` paths a user adds by hand keep working (D122)."""
    out = tmp_path / "out"
    ir_path = _ir_copy(tmp_path)
    assert CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(out)]).exit_code == 0
    with (out / "demo_ss.f").open("a", encoding="utf-8", newline="\n") as f:
        f.write("--command-file=/etc/shadow\n")
    result = CliRunner().invoke(main, [command, str(out)])
    assert (result.exit_code, result.output) == (
        1, f"error: {out}: the file list demo_ss.f has an entry that is a flag, not a file: "
        "--command-file=/etc/shadow. Remove it, or rename the file\n"
    )  # fmt: skip


def test_formal_still_runs_when_the_file_list_is_gone(tmp_path: Path) -> None:
    """`bag formal` never reads the file list: each generated job carries its own sources. So
    the check belongs where the list is consumed, not in load_outputs, which refused a design
    formal could still prove -- and which forced a fixture in test_formal.py to be rewritten."""
    out = tmp_path / "out"
    ir_path = _ir_copy(tmp_path)
    assert CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(out)]).exit_code == 0
    (out / "demo_ss.f").unlink()
    result = CliRunner().invoke(main, ["formal", str(out)])
    assert "the file list" not in result.output
    assert "Traceback" not in result.output
