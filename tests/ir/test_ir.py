# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hdl_bag import __version__
from hdl_bag.checks import run_checks
from hdl_bag.ir import ResolveError, Spec, dumps, loads, resolve
from hdl_bag.ir.to_workbook import to_spec_data, to_workbook
from hdl_bag.reader import read_workbook
from hdl_bag.template.builder import build_workbook

GOLDEN_DIR = Path(__file__).parents[1] / "golden"
GOLDEN = GOLDEN_DIR / "demo_ss.xlsx"
SNAPSHOT = GOLDEN_DIR / "demo_ss.ir.yaml"


def _resolve(path: Path) -> Spec:
    return resolve(run_checks(read_workbook(path), path.parent))


def _no_provenance(spec: Spec) -> Spec:
    return spec.model_copy(update={"provenance": None})


@pytest.fixture(scope="module")
def golden() -> Spec:
    return _resolve(GOLDEN)


def test_golden_snapshot(golden: Spec) -> None:
    # The BAG version is the only line expected to change between releases.
    expected = SNAPSHOT.read_text(encoding="utf-8").replace(
        "bag_version: 0.0.1", f"bag_version: {__version__}"
    )
    assert dumps(golden) == expected


def test_dump_is_lf_and_loads_back(golden: Spec) -> None:
    text = dumps(golden)
    assert "\r" not in text and text.endswith("\n")
    assert loads(text) == golden
    assert dumps(loads(text)) == text


@pytest.mark.parametrize("seed", ["0", "12345"])
def test_byte_identical_across_processes(tmp_path: Path, seed: str) -> None:
    out = tmp_path / "a.ir.yaml"
    env = {**os.environ, "PYTHONHASHSEED": seed}
    subprocess.run(
        [sys.executable, "-m", "hdl_bag.cli", "ir", str(GOLDEN), "-o", str(out)],
        check=True, env=env, capture_output=True,
    )  # fmt: skip
    assert out.read_bytes() == SNAPSHOT.read_bytes().replace(
        b"bag_version: 0.0.1", f"bag_version: {__version__}".encode()
    )


def test_derived_values_match_workbook_cache(golden: Spec) -> None:
    """The workbook's recalculated derived columns agree with the resolved IR."""
    wb = openpyxl.load_workbook(GOLDEN, data_only=True)
    fab = {f.name: f for f in golden.fabrics}
    ws = wb["Fabrics"]
    for r in range(5, 10):
        f = fab[ws[f"A{r}"].value]
        ids = (f.id_width.master_side, f.id_width.slave_side) if f.id_width else ("n/a", "n/a")
        cached = [ws.cell(r, c).value for c in range(15, 20)]
        assert cached == [len(f.master_ports), len(f.slave_ports), *ids, f.max_parallel], f.name
    assert (fab["main_xbar"].id_width.master_side, fab["main_xbar"].id_width.slave_side) == (6, 8)  # type: ignore[union-attr]

    ports = {p.name: p.fabric for p in golden.masters} | {p.name: p.fabric for p in golden.slaves}
    adapters: dict[str, list[str]] = {}
    for a in golden.adapters:
        if a.type in ("pc", "dw", "cdc"):  # the workbook column shows these three types
            adapters.setdefault(a.port, []).append(a.name)
    for sheet in ("Masters", "Slaves"):
        ws = wb[sheet]
        for r in range(5, 20):
            name = ws[f"A{r}"].value
            if not name:
                continue
            assert ws[f"C{r}"].value == ports[name], name
            assert sorted((ws[f"S{r}"].value or "").split()) == sorted(adapters.get(name, [])), name
    bridges = {b.name: b for b in golden.bridges}
    ws = wb["Bridges"]
    for r in range(5, 9):
        b = bridges[ws[f"A{r}"].value]
        assert (ws[f"C{r}"].value, ws[f"D{r}"].value) == (b.upstream, b.downstream)


def test_placement_overrides(golden: Spec) -> None:
    by_name = {a.name: a for a in golden.adapters}
    assert by_name["main_xbar_dw_2_ddr0"].rtl_name == "u_ddr0_upsizer"
    assert by_name["cpu0_cdc_2_main_xbar"].placed_in == "demo_ss_bus_top"
    assert by_name["cpu0_dw_2_main_xbar"].placed_in == "main_wrapper"  # with fabric
    # Port side first (D9): ddr0 <- dw <- fifo <- cut <- main_xbar
    ddr0 = sorted((a for a in golden.adapters if a.port == "ddr0"), key=lambda a: a.chain_index)
    assert [a.type for a in ddr0] == ["dw", "fifo", "cut"]


def test_resolve_refuses_errors(tmp_path: Path) -> None:
    wb = openpyxl.load_workbook(GOLDEN)
    wb["Clocks"]["B5"].value = None
    wb.save(tmp_path / "bad.xlsx")
    shutil.copy(GOLDEN_DIR / "header.txt", tmp_path)
    with pytest.raises(ResolveError, match="1 check error"):
        _resolve(tmp_path / "bad.xlsx")


def test_ir_workbook_ir_round_trip(golden: Spec, tmp_path: Path) -> None:
    path = tmp_path / "rt.xlsx"
    to_workbook(golden).save(path)
    shutil.copy(GOLDEN_DIR / "header.txt", tmp_path)
    result = run_checks(read_workbook(path), tmp_path)
    assert result.findings == ()
    assert _no_provenance(resolve(result)) == _no_provenance(golden)


@settings(
    max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_row_order_never_matters(golden: Spec, tmp_path: Path, seed: int) -> None:
    """Shuffling every table's rows gives the same IR (plan section 2: row order never matters)."""
    data = to_spec_data(golden)
    rng = random.Random(seed)
    for table in ("clocks", "resets", "fabrics", "masters", "slaves", "bridges", "topology",
                  "access_rules", "memory_map", "address_decode", "port_options",
                  "sub_wrappers", "placement", "exposed_ports"):  # fmt: skip
        rng.shuffle(getattr(data, table))
    path = tmp_path / f"shuffled_{seed}.xlsx"
    build_workbook(data=data).save(path)
    shutil.copy(GOLDEN_DIR / "header.txt", tmp_path)
    assert _no_provenance(_resolve(path)) == _no_provenance(golden)
