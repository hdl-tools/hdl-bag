# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""D40: templates cannot silently lose a bus signal, and what BAG ties is reported.

Lint cannot see these mistakes: an unconnected output goes to an ``unused_*`` net (exempt
from the unused checks) and a missing input gets a constant.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hdl_bag.gen import generate
from hdl_bag.gen import wrapper as wrapper_module
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.gen.render import GenerateError
from hdl_bag.gen.wrapper import InstanceContext, Scope
from hdl_bag.ir import load

TESTS = Path(__file__).parents[1]
BRIDGE = TESTS / "configs" / "p2" / "ahb2apb_pt.ir.yaml"
GOLDEN = TESTS / "golden" / "demo_ss.ir.yaml"
LITE = TESTS / "configs" / "p3" / "bridges_inhouse.ir.yaml"


def _bridge_context() -> InstanceContext:
    spec = load(BRIDGE)
    inst = next(i for i in build_netlist(spec).instances if i.kind == "bridge")
    return InstanceContext(Scope(spec, "flat", {}), inst)


@pytest.mark.parametrize("call", ["i", "o"])
def test_a_misspelled_signal_is_an_error(call: str) -> None:
    ctx = _bridge_context()
    assert ctx.inst.up is not None
    with pytest.raises(GenerateError, match="hrdtaa"):
        getattr(ctx, call)(ctx.inst.up, "hrdtaa")


def test_a_signal_of_the_protocol_that_this_bus_lacks_is_fine() -> None:
    """hsel exists on AHB slave ports only; asking for it on a master port takes the default."""
    spec = load(BRIDGE)
    inst = next(i for i in build_netlist(spec).instances if i.template == "bag_ahb_lite_bus")
    ctx = InstanceContext(Scope(spec, "flat", {}), inst)
    assert "hsel" not in inst.masters[0].signals
    assert ctx.i(inst.masters[0], "hsel", 0, 1) == "1'b0"


def test_a_template_that_forgets_a_bus_signal_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "builtin"
    shutil.copytree(wrapper_module.builtin_root(), root)
    path = root / "bag_ahb2apb" / "instance.sv.j2"
    text = path.read_text()
    edited = re.sub(r"^\s+\.hnonsec_i\s*\(.*\n", "", text, flags=re.M)
    assert edited != text
    path.write_text(edited)
    monkeypatch.setattr(wrapper_module, "builtin_root", lambda: root)
    with pytest.raises(GenerateError, match=r"bag_ahb2apb.*hnonsec"):
        generate(load(BRIDGE), tmp_path / "out", BRIDGE.parent)


@pytest.mark.parametrize(("template", "pin", "signal"), [
    ("bag_ahb2apb", "hrdata_o", "hrdata"),  # an output of the component
    ("bag_ahb2apb", "pwrite_o", "pwrite"),
])  # fmt: skip
def test_forgetting_an_output_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str, pin: str, signal: str
) -> None:
    root = tmp_path / "builtin"
    shutil.copytree(wrapper_module.builtin_root(), root)
    path = root / template / "instance.sv.j2"
    text = path.read_text()
    edited = re.sub(rf"^\s+\.{pin}\s*\(.*\n", "", text, flags=re.M)
    assert edited != text
    path.write_text(edited)
    monkeypatch.setattr(wrapper_module, "builtin_root", lambda: root)
    with pytest.raises(GenerateError, match=rf"{template}.*{signal}"):
        generate(load(BRIDGE), tmp_path / "out", BRIDGE.parent)


def test_a_value_the_template_drops_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking for a signal is not connecting it: the result must reach the rendered text."""
    root = tmp_path / "builtin"
    shutil.copytree(wrapper_module.builtin_root(), root)
    path = root / "bag_ahb2apb" / "instance.sv.j2"
    text = path.read_text()
    edited = text.replace(
        '({{ ctx.i(up, "hnonsec", 1) }})', '(1\'b1{% set x = ctx.i(up, "hnonsec", 1) %})'
    )
    assert edited != text
    path.write_text(edited)
    monkeypatch.setattr(wrapper_module, "builtin_root", lambda: root)
    with pytest.raises(GenerateError, match=r"bag_ahb2apb.*hnonsec"):
        generate(load(BRIDGE), tmp_path / "out", BRIDGE.parent)


def _tieoffs(tmp_path: Path, config: Path) -> list[list[str]]:
    generate(load(config), tmp_path, config.parent)
    rows = []
    for line in (tmp_path / "doc" / "tieoffs.md").read_text().splitlines():
        if line.startswith("| ") and not line.startswith(("| Instance", "| ---")):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def test_tieoff_report_lists_what_a_port_option_ties(tmp_path: Path) -> None:
    rows = _tieoffs(tmp_path, GOLDEN)
    # dma0's PROT is tied (Port_Options): the receiving side sees the constant, non-secure.
    tied = [r for r in rows if r[1] == "dma0" and r[2] == "awprot"]
    assert tied, "dma0's tied AxPROT is not reported"
    assert all(r[3] == "input" and r[5] == "tied (Port_Options)" for r in tied)
    assert all(r[4] in ("3'b010", "3'b011") for r in tied), tied


def test_tieoff_report_lists_defaults_and_sinks(tmp_path: Path) -> None:
    rows = _tieoffs(tmp_path, LITE)
    # br_lp: bag_axi2ahb with an AXI4-Lite upstream: AXI4-only inputs take their defaults...
    lp = [r for r in rows if r[0] == "br_lp"]
    awlen = next(r for r in lp if r[2] == "awlen")
    assert awlen[3:] == ["input", "8'h00", "not on this bus"]
    wlast = next(r for r in lp if r[2] == "wlast")
    assert wlast[3:] == ["input", "1'b1", "not on this bus"]
    # ...and its outputs without a place on the bus go to a sink.
    bid = next(r for r in lp if r[2] == "bid")
    assert bid[3:5] == ["output", "unused"]


def test_tieoff_report_lists_user_ports(tmp_path: Path) -> None:
    rows = _tieoffs(tmp_path, TESTS / "configs" / "p3" / "external_fabric.ir.yaml")
    noc = {r[2]: r for r in rows if r[0] == "noc"}
    assert noc["qos_i"][3:] == ["input", "4'b1010", "User_Ports"]
    assert noc["lock_i"][3:] == ["input", "1'b1", "User_Ports"]
    assert noc["err_o"][3:] == ["output", "unused", "User_Ports"]


def test_tieoff_report_is_sorted_and_stable(tmp_path: Path) -> None:
    first = _tieoffs(tmp_path / "a", GOLDEN)
    assert first == _tieoffs(tmp_path / "b", GOLDEN)
    assert first == sorted(first, key=lambda r: r[:3])


AHB_FIXED = TESTS / "configs" / "p2" / "ahb_fixed.ir.yaml"


def test_tieoff_report_lists_constant_alias_enables(tmp_path: Path) -> None:
    """ahb_fixed has three aliases, sorted by (master, base): m0's is always on, m1's and m2's
    are top-level inputs. Alias i is bit i of alias_en_i, so only bit 0 is a constant."""
    rows = [r for r in _tieoffs(tmp_path, AHB_FIXED) if r[2].startswith("alias_en_i")]
    assert rows == [
        ["ahb", "-", "alias_en_i[0]", "input", "1'b1", "alias always on (Address_Decode)"]
    ]


def test_tieoff_report_lists_a_vector_position_with_no_alias(tmp_path: Path) -> None:
    """A fabric without aliases still gets a one-bit alias_en_i, held at 0."""
    rows = [r for r in _tieoffs(tmp_path, LITE) if r[2].startswith("alias_en_i")]
    assert rows, "no placeholder alias enable is reported"
    assert all(
        r[3:] == ["input", "1'b0", "no alias at this position (Address_Decode)"] for r in rows
    )


def test_tieoff_report_with_alias_enables_is_hash_seed_independent(tmp_path: Path) -> None:
    out = []
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        cmd = [sys.executable, "-m", "hdl_bag.cli", "generate", str(AHB_FIXED)]
        subprocess.run([*cmd, "-o", str(tmp_path / seed)], check=True, env=env,
                       capture_output=True)  # fmt: skip
        out.append((tmp_path / seed / "doc" / "tieoffs.md").read_bytes())
    assert out[0] == out[1]
    assert b"alias_en_i[0]" in out[0]
