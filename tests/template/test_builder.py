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
    """True if a cell difference is part of the approved schema 0.2 -> 0.3 delta."""
    if isinstance(old, str) and isinstance(new, str):
        renamed = old.replace("BusGen", "BAG").replace("busgen_", "bag_")
        if renamed == new:
            return True
    if (sheet, coord) == ("Project", "B6"):
        return (old, new) == ("0.2", "0.3")
    if (sheet, coord) == ("Wrapper", "C13"):
        return isinstance(old, str) and new == old.replace("cdc, fifo", "cdc, iw (ID width), fifo")
    if sheet == "Access_Rules":
        # New row 8; derived formulas extend one spare row further (to row 18).
        return coord in {"A8", "B8", "C8", "D8", "E18", "F18", "G18"} and old is None
    return False


@pytest.mark.skipif(not REFERENCE.exists(), reason="private reference workbook not present")
def test_port_matches_reference_except_schema_delta(sample: Workbook) -> None:
    ref = signature(openpyxl.load_workbook(REFERENCE))
    new = signature(sample)
    assert ref["order"] == new["order"]
    unexplained = []
    for name, a in ref["sheets"].items():
        b = new["sheets"][name]
        for key in ("validations", "conditional", "freeze", "widths"):
            if a[key] != b[key]:
                unexplained.append(f"{name}.{key}")
        for key in ("cells", "comments"):
            for coord in sorted(set(a[key]) | set(b[key])):
                old, cur = a[key].get(coord), b[key].get(coord)
                if old != cur and not _expected_delta(name, coord, old, cur):
                    unexplained.append(f"{name}.{key}.{coord}: {old!r} -> {cur!r}")
    assert unexplained == []
