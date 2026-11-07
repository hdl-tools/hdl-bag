# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The vendored PULP tree: index, licences, source resolution and the PULP API BAG uses.

``scripts/vendor_pulp.py --check`` (CI job ``vendor``) proves the tree matches Bender.lock.
These tests prove the tree is usable and that every PULP name BAG relies on exists at the
pinned commit. A failure in the API tests is a stop-and-ask (design/plan.md §10).
"""

import json
import re
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from hdl_bag.templates import builtin_registry
from hdl_bag.templates.sources import VENDOR, SourceError, resolve_sources, vendored_units

sys.path.insert(0, str(Path(__file__).parent))
from pulp_api import parameters, ports, struct_fields

REPO = Path(__file__).parents[2]
INDEX = json.loads((files("hdl_bag") / VENDOR / "index.json").read_text(encoding="utf-8"))
PERMISSIVE = {"SHL-0.51", "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause"}


def test_index_matches_bender_lock() -> None:
    lock = (REPO / "Bender.lock").read_text(encoding="utf-8")
    for name, pkg in INDEX["packages"].items():
        block = re.search(rf"^  {name}:\n    revision: (\w+)\n    version: (\S+)$", lock, re.M)
        assert block, f"{name} missing from Bender.lock"
        assert (block[1], block[2]) == (pkg["revision"], pkg["version"])


def test_every_package_has_a_permissive_licence_file() -> None:
    for name, pkg in INDEX["packages"].items():
        assert pkg["licence"] in PERMISSIVE
        text = (files("hdl_bag") / VENDOR / name / "LICENSE").read_text(encoding="utf-8")
        assert text.startswith("SOLDERPAD HARDWARE LICENSE version 0.51")


def test_every_bender_source_resolves() -> None:
    reg = builtin_registry()
    refs = [s for n in reg.names() if (m := reg.get(n)) for s in m.sources if s[:7] == "bender:"]
    assert refs, "no template names a vendored module"
    for ref in refs:
        assert resolve_sources([ref]).files, ref


def test_closure_is_ordered_and_brings_its_headers() -> None:
    src = resolve_sources(["rtl/inhouse/bag_sync.sv", "bender:axi:axi_xbar"])
    names = [f.rsplit("/", 1)[1] for f in src.files]
    assert names.index("axi_pkg.sv") < names.index("axi_xbar.sv")
    assert names.index("cf_math_pkg.sv") < names.index("axi_xbar.sv")
    assert src.files[-1] == "rtl/inhouse/bag_sync.sv"  # in-house after vendored files
    assert f"{VENDOR}/axi/include/axi/typedef.svh" in src.headers
    assert src.incdirs == (f"{VENDOR}/axi/include", f"{VENDOR}/common_cells/include")
    for path in (*src.files, *src.headers):
        assert (files("hdl_bag") / path).is_file(), path


def test_in_house_only_needs_no_include_dirs() -> None:
    src = resolve_sources(["rtl/inhouse/bag_rst_sync.sv"])
    assert src == type(src)(("rtl/inhouse/bag_rst_sync.sv",), (), ())


@pytest.mark.parametrize("ref", ["bender:axi:no_such_module", "bender:common_cells:axi_xbar",
                                 "bender:axi_xbar"])  # fmt: skip
def test_bad_bender_reference(ref: str) -> None:
    with pytest.raises(SourceError, match="no such module"):
        resolve_sources([ref])


# --- PULP API at the pinned commit (names BAG's wrappers use) ---------------------------

XBAR_CFG_FIELDS = {
    "NoSlvPorts", "NoMstPorts", "MaxMstTrans", "MaxSlvTrans", "FallThrough", "LatencyMode",
    "PipelineStages", "AxiIdWidthSlvPorts", "AxiIdUsedSlvPorts", "UniqueIds", "AxiAddrWidth",
    "AxiDataWidth", "NoAddrRules",
}  # fmt: skip
XBAR_PORTS = {
    "clk_i", "rst_ni", "test_i", "slv_ports_req_i", "slv_ports_resp_o", "mst_ports_req_o",
    "mst_ports_resp_i", "addr_map_i", "en_default_mst_port_i", "default_mst_port_i",
}  # fmt: skip
API = {
    "axi_xbar": (
        {"Cfg", "ATOPs", "Connectivity", "slv_aw_chan_t", "mst_aw_chan_t", "w_chan_t",
         "slv_b_chan_t", "mst_b_chan_t", "slv_ar_chan_t", "mst_ar_chan_t", "slv_r_chan_t",
         "mst_r_chan_t", "slv_req_t", "slv_resp_t", "mst_req_t", "mst_resp_t", "rule_t"},
        XBAR_PORTS,
    ),
    "axi_lite_xbar": (
        {"Cfg", "aw_chan_t", "w_chan_t", "b_chan_t", "ar_chan_t", "r_chan_t", "axi_req_t",
         "axi_resp_t", "rule_t"},
        XBAR_PORTS,
    ),
    "axi_cut": (
        {"Bypass", "aw_chan_t", "w_chan_t", "b_chan_t", "ar_chan_t", "r_chan_t", "axi_req_t",
         "axi_resp_t"},
        {"clk_i", "rst_ni", "slv_req_i", "slv_resp_o", "mst_req_o", "mst_resp_i"},
    ),
    "rr_arb_tree": (
        {"NumIn", "DataWidth", "DataType", "ExtPrio", "AxiVldRdy", "LockIn", "FairArb"},
        {"clk_i", "rst_ni", "flush_i", "rr_i", "req_i", "gnt_o", "data_i", "req_o", "gnt_i",
         "data_o", "idx_o"},
    ),
}  # fmt: skip


def test_xbar_cfg_fields() -> None:
    assert struct_fields("axi_pkg", "xbar_cfg_t") == XBAR_CFG_FIELDS


@pytest.mark.parametrize("module", sorted(API))
def test_module_api(module: str) -> None:
    params, port_names = API[module]
    assert params <= parameters(module), params - parameters(module)
    assert port_names <= ports(module), port_names - ports(module)


def test_every_assumed_module_is_vendored() -> None:
    assumed = {
        "axi_cdc", "axi_cut", "axi_demux", "axi_dw_converter", "axi_err_slv", "axi_fifo",
        "axi_iw_converter", "axi_lite_demux", "axi_lite_dw_converter", "axi_lite_mux",
        "axi_lite_to_apb", "axi_lite_to_axi", "axi_lite_xbar", "axi_modify_address",
        "axi_multicut", "axi_mux", "axi_pkg", "axi_to_axi_lite", "axi_xbar", "rr_arb_tree",
    }  # fmt: skip
    assert assumed <= vendored_units().keys(), assumed - vendored_units().keys()


def test_file_list_include_dirs(tmp_path: Path) -> None:
    from hdl_bag.gen import Outputs, include_dirs, sources

    (tmp_path / "p.f").write_text("+incdir+rtl/vendor/pulp/axi/include\nrtl/pkg/p.sv\nrtl/top.sv\n")
    out = Outputs(tmp_path, (), "top", "p.f")
    assert sources(out) == [Path("rtl/pkg/p.sv"), Path("rtl/top.sv")]
    assert include_dirs(out) == [Path("rtl/vendor/pulp/axi/include")]


def test_every_template_brings_the_headers_its_rtl_includes() -> None:
    """An in-house source's `include must come with the template's vendored closure."""
    reg = builtin_registry()
    for name in reg.names():
        m = reg.get(name)
        assert m is not None
        if not m.rtl:
            continue
        src = resolve_sources(list(m.sources))
        have = {h.split("/include/", 1)[1] for h in src.headers}
        for rel in src.files:
            if "/vendor/pulp/" in rel:
                continue
            text = (files("hdl_bag") / rel).read_text(encoding="utf-8")
            for inc in re.findall(r'`include\s+"([^"]+)"', text):
                assert inc in have, f"{name}: {rel} includes {inc}, which no source brings"


def test_notice_points_at_files_that_exist() -> None:
    """NOTICE names where the third-party licences are; every path it names exists."""
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    named = re.findall(r"\b(?:[\w.-]+/)+[\w.*-]+", notice)
    assert "docs/third-party.md" in named
    for path in named:
        assert list(REPO.glob(path)), f"NOTICE names {path}, which does not exist"
