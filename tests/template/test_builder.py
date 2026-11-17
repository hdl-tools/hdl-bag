# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
import io
from pathlib import Path

import openpyxl
import pytest
from openpyxl import Workbook

from hdl_bag.template import SCHEMA_VERSION, build_workbook
from hdl_bag.template.builder import SHEET_ORDER
from hdl_bag.template.signature import signature

REFERENCE = Path(__file__).parents[2] / "reference" / "BusGen_spec_template_demo_ss.xlsx"


def _saved(wb: Workbook) -> Workbook:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return openpyxl.load_workbook(buf)


@pytest.fixture(scope="module")
def sample() -> Workbook:
    return _saved(build_workbook(sample=True))


@pytest.fixture(scope="module")
def blank() -> Workbook:
    return _saved(build_workbook(sample=False))


@pytest.mark.parametrize("variant", ["sample", "blank"])
def test_sheet_order_and_schema_version(variant: str, request: pytest.FixtureRequest) -> None:
    wb: Workbook = request.getfixturevalue(variant)
    assert tuple(wb.sheetnames) == SHEET_ORDER
    assert wb["Project"]["A6"].value == "Spec schema version"
    assert wb["Project"]["B6"].value == SCHEMA_VERSION


def test_build_is_deterministic() -> None:
    assert signature(_saved(build_workbook(sample=True))) == signature(
        _saved(build_workbook(sample=True))
    )


def test_blank_has_sample_structure_without_data(sample: Workbook, blank: Workbook) -> None:
    s, b = signature(sample)["sheets"], signature(blank)["sheets"]
    for name in SHEET_ORDER:
        # Same headers, header comments, validations, conditional formats and layout.
        for key in ("comments", "validations", "conditional", "freeze", "widths"):
            assert s[name][key] == b[name][key], (name, key)
        # Every formula cell of the sample exists in the blank template, unchanged.
        s_formulas = {k: v for k, v in s[name]["cells"].items() if str(v).startswith("=")}
        b_formulas = {k: v for k, v in b[name]["cells"].items() if str(v).startswith("=")}
        assert s_formulas == b_formulas, name
    # No sample data in the blank template.
    for name in ("Clocks", "Fabrics", "Masters", "Slaves", "Topology", "Memory_Map"):
        assert blank[name]["A5"].value is None, name
    assert blank["Hierarchy"]["A5"].value == "=Wrapper!B5"
    assert blank["Hierarchy"]["A6"].value is None
    assert blank["Wrapper"]["B5"].value is None  # Top module name
    assert blank["Wrapper"]["A18"].value is None  # first Exposed ports row


def _expected_delta(sheet: str, coord: str, old: object, new: object) -> bool:
    """True if a cell difference is part of the approved delta: schema 0.2 -> 0.3, then
    0.3 -> 0.4 (the User_Ports sheet, D41: a README row and a Lists column), then 0.4 -> 0.5
    (notes, and br_ahb2main's FIFO depth, which its bridge ignores), then 0.5 -> 0.6 (D45: the
    External module column, last on Fabrics), then the Phase 6 cleanup (D103, PR 110): notes
    and formulas that match the code."""
    if isinstance(old, str) and isinstance(new, str):
        renamed = old.replace("BusGen", "BAG").replace("busgen_", "bag_")
        if renamed == new:
            return True
    if (sheet, coord) == ("Project", "B6"):
        return (old, new) == ("0.2", "0.6")
    if sheet == "Fabrics" and coord == "U4" and old is None:
        return isinstance(new, str) and new.startswith(("External module", "bag_external only"))
    if (sheet, coord, old, new) == ("Bridges", "G7", 2, 0):
        return True
    if isinstance(new, str) and (sheet, coord) in {("Fabrics", "I4"), ("Fabrics", "J4")}:
        return new.startswith("Limits transactions")
    if isinstance(new, str) and (sheet, coord) == ("Bridges", "G4"):
        return new.startswith("Requests buffered in the bridge.")
    if sheet == "README" and old is None:
        return coord in {"A27", "B27"}  # the User_Ports row, after the last legend row
    if sheet == "Lists" and old is None:
        return True  # the Direction list (a new column)
    if (sheet, coord) == ("Wrapper", "C13"):
        return isinstance(old, str) and new == old.replace("cdc, fifo", "cdc, iw (ID width), fifo")
    if sheet == "Fabrics" and coord[0] in "IJ" and coord[1:] in {"7", "8", "9"}:
        # Max mst/slv trans do not apply to the APB and AHB fabrics (D16).
        return (old, new) == (1, "n/a")
    if _cleanup_delta(sheet, coord, old, new):
        return True
    if sheet == "Access_Rules":
        # New row 8; derived formulas extend one spare row further (to row 18).
        return coord in {"A8", "B8", "C8", "D8", "E18", "F18", "G18"} and old is None
    return False


def _cleanup_delta(sheet: str, coord: str, old: object, new: object) -> bool:
    """The Phase 6 cleanup (D103): each note and formula that now says what the code does."""
    if not (isinstance(old, str) and isinstance(new, str)):
        return False
    row = coord.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if sheet in ("Masters", "Slaves") and coord[0] == "S" and row != "4":
        # Adapter instances: from the Wrapper naming pattern, not a hard-coded one.
        return "_pc_2_" in old and "SUBSTITUTE(" in new and "_pc_2_" not in new
    if sheet == "Topology" and coord[0] == "J" and row != "4":
        # Resolved index: the Port index; a blank is an error, not row order.
        return new == f'=IF($A{row}="","",IF(C{row}<>"",C{row},""))'
    if sheet == "Topology" and coord[0] == "L" and row != "4":
        # Check: INDEX MISSING before the range test, one IF (and one parenthesis) more.
        missing = f'IF(C{row}="","INDEX MISSING",'
        return new.count(missing) == 1 and new.replace(missing, "", 1)[:-1] + ")" == old + ")"
    notes = {
        ("Project", "C17"): "verilator: the only simulator",
        ("Masters", "S4"): "Instance names per Wrapper > Adapter instance naming, for the",
        ("Slaves", "S4"): "Instance names per Wrapper > Adapter instance naming, for the",
        ("Topology", "C4"): "Required. Port number on the fabric side",
        ("Topology", "J4"): "Port index used for generation: the Port index",
        ("Topology", "L4"): "ok | UNKNOWN NAME | ILLEGAL LINK | DUPLICATE | MULTI-ATTACH | "
        "INDEX MISSING",
        ("Wrapper", "C12"): "Default home for inserted adapters: with fabric (the fabric's",
    }
    return (sheet, coord) in notes and new.startswith(notes[sheet, coord])


@pytest.mark.skipif(not REFERENCE.exists(), reason="private reference workbook not present")
def test_port_matches_reference_except_schema_delta(sample: Workbook) -> None:
    ref = signature(openpyxl.load_workbook(REFERENCE))
    new = signature(sample)
    assert ref["order"] == [n for n in new["order"] if n != "User_Ports"]  # 0.4 adds it
    unexplained = []
    for name, a in ref["sheets"].items():
        b = new["sheets"][name]
        for key in ("validations", "conditional", "freeze", "widths"):
            if a[key] == b[key]:
                continue
            if (
                name in ("Lists", "Fabrics")
                and key == "widths"
                and all(  # 0.4: one more list column
                    b[key].get(k) == v for k, v in a[key].items()
                )
            ):
                continue
            unexplained.append(f"{name}.{key}")
        for key in ("cells", "comments"):
            for coord in sorted(set(a[key]) | set(b[key])):
                old, cur = a[key].get(coord), b[key].get(coord)
                if old != cur and not _expected_delta(name, coord, old, cur):
                    unexplained.append(f"{name}.{key}.{coord}: {old!r} -> {cur!r}")
    assert unexplained == []


def test_the_adapter_instances_formulas_read_the_naming_setting(tmp_path: Path) -> None:
    """The Adapter instances formulas on Masters and Slaves read the Wrapper cell of Adapter
    instance naming by its row, so that row must hold that setting (cleanup review)."""
    from hdl_bag.template.builder import WRAPPER_NAMING_ROW

    path = tmp_path / "blank.xlsx"
    build_workbook(sample=False).save(path)
    wb = openpyxl.load_workbook(path)
    assert wb["Wrapper"].cell(WRAPPER_NAMING_ROW, 1).value == "Adapter instance naming"
    formulas = [c.value for row in wb["Masters"].iter_rows() for c in row
                if isinstance(c.value, str) and "SUBSTITUTE" in c.value]  # fmt: skip
    assert formulas and all(f"Wrapper!$B${WRAPPER_NAMING_ROW}" in f for f in formulas)
