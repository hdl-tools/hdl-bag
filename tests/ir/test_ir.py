# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest
from dsl import AXIL_NA, SAMPLE_WRAP, Cfg, resolve_cfg, unexpected
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hdl_bag import __version__
from hdl_bag.checks import run_checks
from hdl_bag.ir import IrError, ResolveError, Spec, dumps, loads, resolve
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
    # The BAG version is the only line expected to change between releases. A pattern, not the
    # old literal, so the test keeps biting after the next bump.
    expected = re.sub(
        r"bag_version: .*", f"bag_version: {__version__}", SNAPSHOT.read_text(encoding="utf-8")
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
    assert out.read_bytes() == re.sub(
        rb"bag_version: .*", f"bag_version: {__version__}".encode(), SNAPSHOT.read_bytes()
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
    assert unexpected((f"{x.severity}: {x.message}" for x in result.findings), SAMPLE_WRAP) == []
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


def test_a_master_with_two_aliases_is_not_a_valid_ir() -> None:
    """One alias per master, as the workbook has (one Address_Decode row per master, one
    alias per row): with two, bag_addr_decode (the last match wins) and the route model and
    formal reference (the first wins) would disagree where they overlap (owner, PR 3)."""
    ir = Path(__file__).parents[1] / "configs" / "p2" / "ahb_fixed.ir.yaml"
    text = ir.read_text(encoding="utf-8")
    one = ("          - base: '0x0000_0000'\n            size: '0x0000_1000'\n"
           "            to: '0x2000_0000'\n            enable: always\n")  # fmt: skip
    assert text.count(one) == 1
    with pytest.raises(IrError) as exc:
        loads(text.replace(one, one + one.replace("0x2000_0000", "0x2000_0800")), ir.name)
    assert str(exc.value) == (
        "ahb_fixed.ir.yaml is not a valid IR: fabrics.0.decode.0.aliases: Value error, master "
        "m0 has 2 aliases; a master has at most one (the workbook's Address_Decode row)"
    )


def _bridge_in_a_sub_wrapper(placement: str) -> Cfg:
    """An AXI4 crossbar x in sub-wrapper ss1 with an AXI4-Lite master (adapter m0_pc_2_x), and
    a bag_axi2axil bridge in sub-wrapper ss0 with a FIFO on its downstream end (adapter
    br_fifo_2_l) into an AXI4-Lite fabric l in the top. Each placement gives each adapter a
    different home."""
    c = Cfg("wp", phase=3)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("x", "AXI4", 32, 32, "D0", "round-robin", latency="CUT_ALL_AX")
    c.fabric("l", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 32, 32, "D0").link("m0", "x", 0).decode("m0", "DECERR")
    c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("l", "s0", 0)
    c.bridge("br", 0x4000_0000, template="bag_axi2axil")
    c.link("x", "br", 0).link("br", "l", 0).decode("br", "DECERR")
    c.region("br", "win", 0x4000_0000, 0x1_0000).region("s0", "regs", 0x4000_0000, 0x1000)
    c.option("m0", **AXIL_NA).option("s0", **AXIL_NA).option("br", fifo=2, **AXIL_NA)
    c.sub("ss0", "wp_top").place("br", "ss0").sub("ss1", "wp_top").place("x", "ss1")
    c.data.wrapper["Adapter placement"] = placement
    return c


@pytest.mark.parametrize(("placement", "fifo_home", "pc_home"), [
    ("with port", "ss0", "wp_top"), ("with fabric", "wp_top", "ss1"), ("top", "wp_top", "wp_top"),
])  # fmt: skip
def test_adapter_placement_with_port_puts_a_bridge_end_adapter_beside_its_bridge(
    tmp_path: Path, placement: str, fifo_home: str, pc_home: str
) -> None:
    """D102: 'with port' places an adapter beside the port it serves. A master or slave is
    exposed at the top, so its adapter stays in the top wrapper; a bridge end's adapter goes
    in the bridge's sub-wrapper. 'with fabric' follows the fabric (here in the top), 'top'
    is the top."""
    spec, findings = resolve_cfg(_bridge_in_a_sub_wrapper(placement), tmp_path)
    assert findings == [] and spec is not None
    assert {a.name: a.placed_in for a in spec.adapters} == {
        "br_fifo_2_l": fifo_home, "m0_pc_2_x": pc_home}  # fmt: skip
    # Written back, an adapter in its default home needs no Placement row.
    assert [row[0] for row in to_spec_data(spec).placement] == ["br", "x"]


def test_an_adapter_placed_away_from_with_port_is_written_back(tmp_path: Path) -> None:
    """The other direction of the round trip: an adapter the Hierarchy sheet moves out of
    its 'with port' home keeps its Placement row."""
    c = _bridge_in_a_sub_wrapper("with port").place("br_fifo_2_l", "wp_top")
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    assert [row[:2] for row in to_spec_data(spec).placement] == [
        ["br", "ss0"], ["br_fifo_2_l", "wp_top"], ["x", "ss1"]]  # fmt: skip


def test_a_bridge_end_adapter_in_its_bridges_sub_wrapper_is_wired_through_the_hierarchy(
    tmp_path: Path,
) -> None:
    """D102, generated: the hierarchy oracle finds every link between br_fifo_2_l (in ss0),
    the bridge and fabric l (in the top) connected through the sub-wrapper ports."""
    from hier_oracle import check as oracle

    from hdl_bag.gen import generate

    spec, findings = resolve_cfg(_bridge_in_a_sub_wrapper("with port"), tmp_path)
    assert findings == [] and spec is not None
    generate(spec, tmp_path / "out", tmp_path)
    assert (tmp_path / "out" / "rtl" / "ss0.sv").is_file()
    assert oracle(spec, tmp_path / "out" / "rtl") == []


@pytest.mark.eda
def test_a_bridge_end_adapter_in_its_bridges_sub_wrapper_lints_clean(tmp_path: Path) -> None:
    """D102: the same design through slang, Verilator and Verible."""
    from hdl_bag.flows import lint
    from hdl_bag.gen import generate, include_dirs, sources

    spec, findings = resolve_cfg(_bridge_in_a_sub_wrapper("with port"), tmp_path)
    assert findings == [] and spec is not None
    out = generate(spec, tmp_path / "out", tmp_path)
    report = lint(sources(out), out.top, out.root, tools=out.lint_tools, incdirs=include_dirs(out))
    assert [f.format() for f in report.unwaived] == []
