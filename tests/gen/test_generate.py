# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generator outputs without EDA tools: layout, determinism, header, tables, CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main
from hdl_bag.gen import MANIFEST, MissingRtl, generate, load_outputs, sources
from hdl_bag.gen.decode import decode_tables
from hdl_bag.gen.render import camel, ir_sha256, lit
from hdl_bag.ir import load

TESTS = Path(__file__).parents[1]
CONFIG = TESTS / "configs" / "p2" / "ahb2apb_pt.ir.yaml"
GOLDEN_IR = TESTS / "golden" / "demo_ss.ir.yaml"


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("flat", ["rtl/pkg/demo_ss_bus_pkg.sv", *(f"rtl/inhouse/{m}.sv" for m in (
            "bag_addr_decode", "bag_ahb2apb", "bag_ahb_lite_bus", "bag_apb_mux",
            "bag_rst_sync", "bag_sync")), "rtl/demo_ss_bus_top.sv"]),
        ("sv-interface", ["rtl/pkg/demo_ss_bus_pkg.sv", "rtl/demo_ss_ahb_if.sv",
                          "rtl/demo_ss_apb4_if.sv", *(f"rtl/inhouse/{m}.sv" for m in (
            "bag_addr_decode", "bag_ahb2apb", "bag_ahb_lite_bus", "bag_apb_mux",
            "bag_rst_sync", "bag_sync")), "rtl/demo_ss_bus_top.sv",
            "rtl/demo_ss_bus_top_flat.sv"]),
    ],
)  # fmt: skip
def test_layout_and_filelist(tmp_path: Path, style: str, expected: list[str]) -> None:
    out = generate(load(CONFIG), tmp_path, CONFIG.parent, style)
    assert [p.as_posix() for p in sources(out)] == expected
    assert load_outputs(tmp_path) == out
    manifest = json.loads((tmp_path / MANIFEST).read_text())
    assert sorted(manifest["files"]) == sorted(out.files)
    assert out.top == ("demo_ss_bus_top" if style == "flat" else "demo_ss_bus_top_flat")


def test_header_is_user_text_then_banner(tmp_path: Path) -> None:
    spec = load(CONFIG)
    generate(spec, tmp_path, CONFIG.parent)
    user = (CONFIG.parent / "header.txt").read_text()
    for rel in ("rtl/pkg/demo_ss_bus_pkg.sv", "rtl/demo_ss_bus_top.sv"):
        text = (tmp_path / rel).read_text()
        assert text.startswith(user)
        banner = text[len(user) :].splitlines()[:5]
        assert "DO NOT EDIT" in banner[1] and banner[3].endswith(ir_sha256(spec))
    # In-house IP is copied verbatim, with its own licence header.
    assert (tmp_path / "rtl/inhouse/bag_sync.sv").read_text().startswith("// SPDX-License-")


@pytest.mark.parametrize("style", ["flat", "sv-interface"])
def test_byte_identical_and_lf(tmp_path: Path, style: str) -> None:
    generate(load(CONFIG), tmp_path / "a", CONFIG.parent, style)
    generate(load(CONFIG), tmp_path / "b", CONFIG.parent, style)
    a = _tree(tmp_path / "a")
    assert a == _tree(tmp_path / "b")
    assert not any(b"\r" in v for v in a.values())


def test_byte_identical_across_processes(tmp_path: Path) -> None:
    trees = []
    for seed in ("0", "12345"):
        out = tmp_path / seed
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run(
            [sys.executable, "-m", "hdl_bag.cli", "generate", str(CONFIG), "-o", str(out),
             "--style", "sv-interface"],
            check=True, env=env, capture_output=True,
        )  # fmt: skip
        trees.append(_tree(out))
    assert trees[0] == trees[1]


def test_golden_writes_collateral_then_stops(tmp_path: Path) -> None:
    with pytest.raises(MissingRtl) as exc:
        generate(load(GOLDEN_IR), tmp_path, GOLDEN_IR.parent)
    # Phase 3 PRs 3 and 4 gave the AXI fabrics and adapters RTL; the bridges follow.
    assert exc.value.templates == ["bag_ahb2axi", "bag_axi2apb", "bag_axi2axil"]
    assert (tmp_path / "rtl/pkg/demo_ss_bus_pkg.sv").is_file()
    assert json.loads((tmp_path / MANIFEST).read_text())["top"] is None
    with pytest.raises(ValueError, match="no RTL was generated"):
        load_outputs(tmp_path)


def test_package_tables(tmp_path: Path) -> None:
    generate(load(CONFIG), tmp_path, CONFIG.parent)
    pkg = (tmp_path / "rtl/pkg/demo_ss_bus_pkg.sv").read_text()
    # aon_apb: wdt0 (secure) at 0x5000_0000, rtc0 at 0x5000_1000; highest index first.
    assert "localparam logic [1:0][31:0] AonApbRuleBase = '{32'h5000_1000, 32'h5000_0000};" in pkg
    assert "localparam logic [1:0] AonApbRuleSecure = 2'b01;" in pkg
    assert "localparam logic [63:0] Wdt0RegsBase = 64'h0000_0000_5000_0000;" in pkg
    assert "localparam logic [63:0] Wdt0RegsSize = 64'h0000_0000_0000_1000;" in pkg
    assert max(len(line) for line in pkg.splitlines()) <= 100


def test_decode_tables_from_golden() -> None:
    spec = load(GOLDEN_IR)
    xbar = next(f for f in spec.fabrics if f.name == "main_xbar")
    t = decode_tables(xbar)
    masters = {p.port: p.index for p in xbar.master_ports}
    slaves = {p.port: p.index for p in xbar.slave_ports}
    # Access_Rules: dma0 has no access to br_main2cfg (D6).
    assert not t.connect[masters["dma0"]][slaves["br_main2cfg"]]
    assert sum(not c for row in t.connect for c in row) == len(
        [a for a in xbar.access_rules if a.access == "none"]
    )
    # cpu0's boot alias is enabled by the boot_remap_i pin.
    assert [(a.master, a.enable) for a in t.aliases] == [(masters["cpu0"], "boot_remap_i")]
    assert t.alias_enables == ("boot_remap_i",)
    assert len(t.rules) == len(xbar.addr_rules)


def test_render_helpers() -> None:
    assert lit(32, 0x1000_3000) == "32'h1000_3000"
    assert lit(36, 0x1_2345_6789) == "36'h1_2345_6789"
    assert lit(8, -1) == "8'hFF"
    assert lit(3, 5) == "3'b101"
    assert camel("br_ahb2aon") == "BrAhb2aon"


def test_cli_generate_and_missing_rtl(tmp_path: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(main, ["generate", str(CONFIG), "-o", str(tmp_path / "ok")])
    assert ok.exit_code == 0, ok.output
    assert "top demo_ss_bus_top" in ok.output
    bad = runner.invoke(main, ["generate", str(GOLDEN_IR), "-o", str(tmp_path / "bad")])
    assert bad.exit_code == 1
    assert "no RTL yet for template(s): bag_ahb2axi" in bad.output
