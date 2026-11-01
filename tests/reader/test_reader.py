# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
from pathlib import Path

import pytest
from openpyxl.worksheet.worksheet import Worksheet

from hdl_bag.reader import ReaderError, read_workbook
from hdl_bag.reader.values import is_identifier, parse_hex, parse_int, parse_yes_no
from hdl_bag.reader.workbook import TABLES
from hdl_bag.template import build_workbook
from hdl_bag.template.builder import FILL_HDR

GOLDEN = Path(__file__).parents[1] / "golden" / "demo_ss.xlsx"


def _input_headers(ws: Worksheet, row: int) -> tuple[str, ...]:
    return tuple(
        str(c.value)
        for c in ws[row]
        if c.value is not None and c.fill.fgColor.rgb.endswith(FILL_HDR.fgColor.rgb[-6:])
    )


def test_reader_columns_match_builder_input_columns() -> None:
    wb = build_workbook(sample=False)
    header_rows = {"Hierarchy": (4, 15), "Wrapper": (17,)}
    expected = []
    for name in dict.fromkeys(t.sheet for t in TABLES):
        for row in header_rows.get(name, (4,)):
            expected.append((name, _input_headers(wb[name], row)))
    assert [(t.sheet, t.headers) for t in TABLES] == expected


def test_golden_tables() -> None:
    s = read_workbook(GOLDEN)
    counts = {
        "clocks": 4, "resets": 4, "fabrics": 5, "masters": 5, "slaves": 11, "bridges": 4,
        "topology": 24, "access_rules": 4, "memory_map": 16, "address_decode": 9,
        "port_options": 13, "sub_wrappers": 4, "placement": 11, "exposed_ports": 20,
    }  # fmt: skip
    assert {k: len(getattr(s, k)) for k in counts} == counts
    assert s.source == "demo_ss.xlsx"
    assert len(s.sha256) == 64
    assert s.project["Spec schema version"].value == "0.4"
    assert s.project["Spec schema version"].row == 6
    assert s.wrapper["File header"].value == "header.txt"
    # Locations follow the sheet rows.
    assert s.fabrics[0].row == 5
    assert s.placement[0].row == 16
    assert s.exposed_ports[0].row == 18
    # Raw values: no interpretation, blanks are None.
    link = s.topology[8]
    assert (link.value("Source"), link.value("Destination")) == ("br_main2cfg", "cfg_bus")
    assert (link.value("Port index"), link.value("Priority")) == (0, 1)
    assert s.masters[2].value("ID width (bits)") is None
    assert s.slaves[0].value("ID width (bits)") == "auto"
    assert s.memory_map[0].value("Base (hex)") == "0x1000_0000"
    assert s.topology[8]["Priority"].where == "Topology row 13"


def test_top_wrapper_name_comes_from_wrapper_sheet() -> None:
    top = read_workbook(GOLDEN).sub_wrappers[0]
    assert (top.row, top.value("Sub-wrapper"), top.value("Parent")) == (5, "demo_ss_bus_top", "-")


def test_blank_template_reads_empty_tables(tmp_path: Path) -> None:
    path = tmp_path / "blank.xlsx"
    build_workbook(sample=False).save(path)
    s = read_workbook(path)
    assert s.fabrics == () and s.topology == () and s.exposed_ports == ()
    assert len(s.sub_wrappers) == 1
    assert s.sub_wrappers[0].value("Sub-wrapper") is None


def test_columns_found_by_name_not_position(tmp_path: Path) -> None:
    wb = build_workbook(sample=True)
    ws = wb["Clocks"]
    for r in range(4, 9):  # swap columns B (Freq) and C (Source)
        b, c = ws.cell(r, 2).value, ws.cell(r, 3).value
        ws.cell(r, 2).value, ws.cell(r, 3).value = c, b
    path = tmp_path / "swapped.xlsx"
    wb.save(path)
    clk = read_workbook(path).clocks[0]
    assert (clk.value("Freq (MHz)"), clk.value("Source")) == (1000, "top port (CPU PLL)")


def test_missing_column_is_reported(tmp_path: Path) -> None:
    wb = build_workbook(sample=True)
    wb["Memory_Map"]["D4"] = "Size"
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ReaderError, match=r"Memory_Map: missing column 'Size \(hex\)'"):
        read_workbook(path)


def test_missing_sheet_is_reported(tmp_path: Path) -> None:
    wb = build_workbook(sample=True)
    del wb["Topology"]
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ReaderError, match="Topology: sheet missing"):
        read_workbook(path)


def test_strings_are_stripped_and_blank_is_none(tmp_path: Path) -> None:
    wb = build_workbook(sample=True)
    wb["Clocks"]["C5"] = "   "
    wb["Clocks"]["A5"] = "  clk_cpu "
    path = tmp_path / "ws.xlsx"
    wb.save(path)
    clk = read_workbook(path).clocks[0]
    assert (clk.value("Clock"), clk.value("Source")) == ("clk_cpu", None)


@pytest.mark.parametrize(
    ("text", "value"),
    [("0x4000_0000", 0x4000_0000), ("0x0", 0), ("0X1F", 0x1F), ("0xdead_beef", 0xDEADBEEF)],
)
def test_parse_hex(text: str, value: int) -> None:
    assert parse_hex(text) == value


@pytest.mark.parametrize("text", ["4000", "0x", "0x_10", "0x10_", "0x1__0", "0xG", 16, None])
def test_parse_hex_rejects(text: object) -> None:
    with pytest.raises(ValueError, match="not hex"):
        parse_hex(text)  # type: ignore[arg-type]


def test_parse_int() -> None:
    assert parse_int(3) == 3
    assert parse_int(3.0) == 3
    assert parse_int("12") == 12
    for bad in (1.5, "1.5", "x", None, True):
        with pytest.raises(ValueError):
            parse_int(bad)
    with pytest.raises(ValueError, match="less than 0"):
        parse_int(-1, minimum=0)


def test_parse_yes_no_and_identifier() -> None:
    assert parse_yes_no("Y") is True and parse_yes_no("N") is False
    with pytest.raises(ValueError):
        parse_yes_no("y")
    assert is_identifier("main_xbar") and not is_identifier("Main") and not is_identifier("1a")
