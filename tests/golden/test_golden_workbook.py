# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The committed golden workbook: recalculated demo_ss sample (schema 0.5).

Refresh it after a schema change from the ``golden-demo_ss`` CI artifact (see the
``golden`` job in .github/workflows/pr.yml).
"""

import io
import os
from pathlib import Path

import openpyxl
import pytest
from openpyxl import Workbook

from hdl_bag.template import build_workbook
from hdl_bag.template.signature import signature

GOLDEN = Path(__file__).parent / "demo_ss.xlsx"

# (sheet, check column, first row, last row)
CHECK_COLUMNS = [
    ("Topology", "L", 5, 200),
    ("Access_Rules", "G", 5, 60),
    ("Memory_Map", "M", 5, 60),
    ("User_Ports", "H", 5, 60),
    ("Hierarchy", "D", 5, 12),
    ("Hierarchy", "F", 16, 55),
]


def _values(path: Path) -> Workbook:
    return openpyxl.load_workbook(path, data_only=True)


def _recalculated() -> Path:
    env = os.environ.get("BAG_RECALC_WORKBOOK")
    if not env:
        pytest.skip("BAG_RECALC_WORKBOOK not set (runs in the CI golden job)")
    return Path(env)


def _bad_checks(wb: Workbook) -> list[str]:
    bad = []
    for sheet, col, first, last in CHECK_COLUMNS:
        ws = wb[sheet]
        for r in range(first, last + 1):
            v = ws[f"{col}{r}"].value
            if v not in (None, "") and not str(v).startswith("ok"):
                bad.append(f"{sheet}!{col}{r} = {v!r}")
    return bad


def test_golden_is_committed() -> None:
    assert GOLDEN.exists(), "tests/golden/demo_ss.xlsx missing: commit the CI artifact"


def test_golden_checks_all_ok() -> None:
    assert _bad_checks(_values(GOLDEN)) == []


def test_golden_matches_builder() -> None:
    buf = io.BytesIO()
    build_workbook(sample=True).save(buf)
    buf.seek(0)
    assert signature(openpyxl.load_workbook(GOLDEN)) == signature(openpyxl.load_workbook(buf))


def test_golden_derived_values() -> None:
    wb = _values(GOLDEN)
    fabrics = {
        wb["Fabrics"][f"A{r}"].value: [wb["Fabrics"].cell(r, c).value for c in range(15, 20)]
        for r in range(5, 10)
    }
    # # master ports, # slave ports, master-side ID, slave-side ID, max parallel transfers
    assert fabrics == {
        "main_xbar": [4, 4, 6, 8, 4],
        "cfg_bus": [2, 2, "n/a", "n/a", 1],
        "periph_apb": [1, 4, "n/a", "n/a", 1],
        "ahb_bus": [1, 3, "n/a", "n/a", 1],
        "aon_apb": [1, 2, "n/a", "n/a", 1],
    }
    adapters = {
        ws[f"A{r}"].value: ws[f"S{r}"].value
        for ws in (wb["Masters"], wb["Slaves"])
        for r in range(5, 20)
        if ws[f"A{r}"].value and ws[f"S{r}"].value
    }
    assert adapters == {
        "cpu0": "cpu0_dw_2_main_xbar cpu0_cdc_2_main_xbar",
        "dbg0": "dbg0_pc_2_main_xbar dbg0_dw_2_main_xbar",
        "ddr0": "main_xbar_dw_2_ddr0",
    }


def test_recalculated_checks_all_ok() -> None:
    assert _bad_checks(_values(_recalculated())) == []


def test_recalculated_matches_golden() -> None:
    fresh = openpyxl.load_workbook(_recalculated())
    assert signature(fresh) == signature(openpyxl.load_workbook(GOLDEN))
