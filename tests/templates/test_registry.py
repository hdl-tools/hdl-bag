# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
import pytest

from hdl_bag.protocols import AXI_FAMILY, DATA_WIDTHS, PROTO_TOKEN, Protocol, port_signals
from hdl_bag.template.builder import LISTS
from hdl_bag.templates import Manifest, Registry, builtin_registry


def test_every_workbook_template_has_a_manifest() -> None:
    reg = builtin_registry()
    names = set(reg.names("fabric")) | set(reg.names("bridge"))
    assert names == set(LISTS["Template"])


def test_adapters_cover_every_type_and_only_axi() -> None:
    reg = builtin_registry()
    for t in ("pc", "dw", "cdc", "iw", "fifo", "cut"):
        m = reg.adapter(t)
        assert m is not None and m.kind == "adapter"
        assert set(m.protocols) <= AXI_FAMILY  # D24


def test_fabric_capabilities() -> None:
    reg = builtin_registry()
    names = ("bag_axi_xbar", "bag_axil_shared_bus", "bag_ahb_lite_bus", "bag_apb_mux")
    xbar, shared, ahb, apb = (reg.get(n) for n in names)
    assert xbar and shared and ahb and apb
    assert xbar.arbitration == ("round-robin",)  # D25
    assert "fixed-priority" in shared.arbitration
    assert set(ahb.arbitration) == {"single-master", "round-robin", "fixed-priority"}  # D20
    assert apb.arbitration == ("single-master",)
    assert apb.protocols == (Protocol.APB4, Protocol.APB3)


def test_bridge_catalogue() -> None:
    reg = builtin_registry()
    m = reg.get("bag_ahb2axi")
    assert m and m.upstream == (Protocol.AHB_LITE,) and set(m.downstream) == AXI_FAMILY


def test_manifest_validation() -> None:
    with pytest.raises(ValueError, match="fabric needs"):
        Manifest(name="x", kind="fabric", api_version=1, description="", licence="MIT")
    m = Manifest(name="x", kind="adapter", api_version=1, description="", licence="MIT",
                 protocols=(Protocol.AXI4,), adapter_type="cut")  # fmt: skip
    with pytest.raises(ValueError, match="duplicate"):
        Registry([m, m])


def test_protocol_tables() -> None:
    assert set(PROTO_TOKEN.values()) == {"axi", "axil", "ahb", "apb4", "apb3"}
    assert DATA_WIDTHS[Protocol.AXI4_LITE] == {32, 64}
    assert min(DATA_WIDTHS[Protocol.AXI4]) == 32 and max(DATA_WIDTHS[Protocol.AXI4]) == 1024
    assert "pprot" not in port_signals(Protocol.APB3, is_slave=True)
    assert "hsel" in port_signals(Protocol.AHB_LITE, is_slave=True)
    assert "hsel" not in port_signals(Protocol.AHB_LITE, is_slave=False)
    axi = port_signals(Protocol.AXI4, is_slave=False, has_id=False, removed=frozenset({"awqos"}))
    assert "awid" not in axi and "awuser" not in axi and "awqos" not in axi and "arqos" in axi
