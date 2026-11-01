# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""bag_external, user-supplied fabrics (Phase 3 PR 7).

BAG instantiates <project>_<fabric> with the port contract of hdl_bag.gen.external and
writes a stub of it. These tests check the contract and that the generated wiring works:
the eda test swaps the stubs for pass-through fabrics built from the same contract and
sends traffic through the adapters, the sub-wrapper and a bridge.
"""

import re
from pathlib import Path

import pytest
from simrun import REPO, run_cocotb

from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.gen.external import opaque_module, opaque_ports
from hdl_bag.gen.netlist import Instance, build_netlist
from hdl_bag.ir import Spec, load

CONFIG = REPO / "tests" / "configs" / "p3" / "external_fabric.ir.yaml"
HERE = Path(__file__).parent


def _opaque(spec: Spec) -> list[Instance]:
    return [i for i in build_netlist(spec).instances if i.template == "bag_external"]


@pytest.mark.parametrize("style", ["flat", "sv-interface"])
def test_instance_matches_the_stub(tmp_path: Path, style: str) -> None:
    spec = load(CONFIG)
    out = generate(spec, tmp_path, CONFIG.parent, style)
    files = [f.as_posix() for f in sources(out)]
    for inst in _opaque(spec):
        assert inst.fabric is not None
        module = opaque_module(spec, inst.fabric)
        stub = f"rtl/ext/{module}.sv"
        assert stub in files and files.index(stub) < files.index(f"rtl/{spec.hierarchy.top}.sv")
        text = (tmp_path / stub).read_text()
        stub_ports = re.findall(r"^\s+(input|output)\s+logic\s+(?:\[\d+:0\]\s+)?(\w+),?$", text,
                                re.M)  # fmt: skip
        contract = [("input", "clk_i"), ("input", "rst_ni")]
        contract += [(p.direction, p.name) for p in opaque_ports(spec, inst)]
        contract += [(u.direction, u.port) for u in spec.user_ports if u.instance == inst.name]
        assert stub_ports == contract
        # The wrapper (or sub-wrapper) instantiates it with exactly those ports.
        wrapper = tmp_path / "rtl" / f"{inst.wrapper}.sv"
        body = wrapper.read_text()
        block = body[body.index(f"  {module} {inst.rtl_name} (") :]
        block = block[: block.index("\n  );")]
        assert re.findall(r"^\s+\.(\w+)\s*\(", block, re.M) == [n for _, n in contract]


def test_contract_names_and_directions() -> None:
    spec = load(CONFIG)
    noc = next(i for i in _opaque(spec) if i.name == "noc")
    ports = {p.name: p for p in opaque_ports(spec, noc)}
    # cpu (a master): the fabric is the slave end; ram (a slave): the master end.
    assert ports["noc_cpu_axi_awvalid"].direction == "input"
    assert ports["noc_cpu_axi_rdata"].direction == "output"
    assert ports["noc_ram_axi_awvalid"].direction == "output"
    assert ports["noc_ram_axi_rdata"].direction == "input"
    # Widths at the fabric, after cpu's adapters (64 -> 32 bits).
    assert ports["noc_cpu_axi_wdata"].width == 32
    assert noc.wrapper == "ss_noc"


# What the test fabrics drive on their output user ports: observable at the top.
USER_LOGIC = {
    ("noc", "cfg_o"): "cfg_i",  # an exposed input back out
    ("noc", "qos_o"): "qos_i",  # the 0xA tie
    ("noc", "irq_o"): "test_i & lock_i",  # the extra input, through the tie-1
    ("noc", "err_o"): "2'b11",  # left open
    ("peri", "irq_o"): "1'b1",
}


def test_user_ports_in_the_contract() -> None:
    spec = load(CONFIG)
    noc = next(i for i in _opaque(spec) if i.name == "noc")
    assert {u.port: u.connection for u in spec.user_ports if u.instance == "noc"} == {
        "cfg_i": "expose", "cfg_o": "expose", "clk_cfg_i": "clock", "err_o": "open",
        "irq_o": "expose", "lock_i": "tie", "qos_i": "tie", "qos_o": "expose",
        "rst_cfg_ni": "reset", "test_i": "extra-input",
    }  # fmt: skip
    assert noc.wrapper == "ss_noc"  # its exposed ports cross the sub-wrapper


def _passthrough(spec: Spec, inst: Instance) -> str:
    """A 1:1 fabric: the master attachment's requests straight to the slave attachment,
    the responses straight back (widths fitted)."""
    assert inst.fabric is not None
    ports = opaque_ports(spec, inst)
    mst = {p.signal: p for p in ports if not p.bus.is_slave}
    slv = {p.signal: p for p in ports if p.bus.is_slave}
    decl = ["    input logic clk_i", "    input logic rst_ni"]
    decl += [f"    {p.direction} logic [{p.width - 1}:0] {p.name}" for p in ports]
    users = [u for u in spec.user_ports if u.instance == inst.name]
    decl += [f"    {u.direction} logic [{u.width - 1}:0] {u.port}" for u in users]
    body = [f"  assign {u.port} = {USER_LOGIC[(inst.name, u.port)]};"
            for u in users if u.direction == "output"]  # fmt: skip
    for out_side, in_side in ((slv, mst), (mst, slv)):
        for sig, p in out_side.items():
            if p.direction != "output":
                continue
            src = in_side[sig]
            if src.width > p.width:
                value = f"{src.name}[{p.width - 1}:0]"
            elif src.width < p.width:
                value = f"{{{p.width - src.width}'b0, {src.name}}}"
            else:
                value = src.name
            body.append(f"  assign {p.name} = {value};")
    module = opaque_module(spec, inst.fabric)
    return (f"module {module} (\n" + ",\n".join(decl) + "\n);\n" + "\n".join(body)
            + f"\nendmodule : {module}\n")  # fmt: skip


@pytest.mark.eda
def test_wiring_with_user_fabrics() -> None:
    spec = load(CONFIG)
    root = REPO / "out" / "corpus" / "external_fabric_user"
    out = generate(spec, root, CONFIG.parent)
    user = root / "user"
    user.mkdir(exist_ok=True)
    swap = {}
    for inst in _opaque(spec):
        assert inst.fabric is not None
        module = opaque_module(spec, inst.fabric)
        (user / f"{module}.sv").write_text(_passthrough(spec, inst))
        swap[f"rtl/ext/{module}.sv"] = user / f"{module}.sv"
    files = [swap.get(f.as_posix(), root / f) for f in sources(out)]
    assert len(swap) == 2 and all(f in files for f in swap.values())
    run_cocotb(files, out.top, HERE / "cocotb_external.py", "external_user",
               env={"BAG_IR": str(CONFIG)},
               incdirs=[root / d for d in include_dirs(out)])  # fmt: skip
