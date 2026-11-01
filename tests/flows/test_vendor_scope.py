# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Vendored RTL in the flows: include directories, and lint scope P3-2.

Vendored files (``rtl/vendor/``) are compiled by every tool, but their warnings go to
``Report.third_party`` and never fail lint. Errors anywhere still fail. Verible skips them.
"""

from importlib import import_module
from importlib.resources import files
from pathlib import Path

import pytest

from hdl_bag.flows import Finding, compile_design, lint
from hdl_bag.flows.backends import Slang, Verible, Verilator
from hdl_bag.flows.base import Report, is_third_party
from hdl_bag.flows.compile import VerilatorBuild
from hdl_bag.flows.lint import split_third_party
from hdl_bag.templates.sources import resolve_sources

VENDOR_SV = "rtl/vendor/pulp/axi/src/axi_cut.sv"
OWN_SV = "rtl/top.sv"
# The module (hdl_bag.flows.lint the attribute is the re-exported function).
lint_module = import_module("hdl_bag.flows.lint")


def _f(file: str, severity: str = "warning", tool: str = "slang") -> Finding:
    return Finding(tool, file, 3, "rule", severity, "msg")  # type: ignore[arg-type]


def test_is_third_party() -> None:
    assert is_third_party(VENDOR_SV)
    assert is_third_party("src/hdl_bag/rtl/vendor/pulp/axi/src/axi_cut.sv")
    assert not is_third_party(OWN_SV)
    assert not is_third_party("rtl/inhouse/bag_vendor_shim.sv")
    assert not is_third_party("")


def test_split_judges_errors_and_own_warnings() -> None:
    raw = [_f(VENDOR_SV), _f(VENDOR_SV, "error"), _f(OWN_SV), _f("")]
    ours, theirs = split_third_party(raw)
    assert theirs == [_f(VENDOR_SV)]
    assert ours == [_f(VENDOR_SV, "error"), _f(OWN_SV), _f("")]


def test_report_counts_third_party() -> None:
    report = Report("lint", (), ("slang",), (_f(VENDOR_SV),))
    assert report.ok
    assert "1 third-party warning(s) in rtl/vendor/ not judged" in report.format()
    assert "third-party" not in Report("lint", (), ("slang",)).format()


@pytest.mark.parametrize("backend", [Slang(), Verilator(), VerilatorBuild("obj")])
def test_include_dirs_reach_the_command(backend: Slang | Verilator) -> None:
    cmd = backend.command([Path("a.sv")], "a", [Path("rtl/vendor/pulp/axi/include")])
    assert "-Irtl/vendor/pulp/axi/include" in cmd
    assert cmd.index("-Irtl/vendor/pulp/axi/include") < cmd.index("a.sv")


def test_verible_ignores_include_dirs() -> None:
    cmd = Verible().command([Path("a.sv")], "a", [Path("inc")])
    assert not any("inc" in c for c in cmd)


def test_lint_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, list[str]] = {}

    def fake(backend, files, top, root, incdirs):  # type: ignore[no-untyped-def]
        seen[backend.name] = [f.as_posix() for f in files]
        assert incdirs == [Path("inc")]
        return [_f(VENDOR_SV, tool=backend.name)]

    monkeypatch.setattr(lint_module, "run_backend", fake)
    files = [Path(VENDOR_SV), Path(OWN_SV)]
    report = lint(files, "top", Path("/run"), incdirs=[Path("inc")])
    assert report.ok, report.format()
    assert len(report.third_party) == 3
    assert seen["verible"] == [OWN_SV]
    assert seen["slang"] == seen["verilator"] == [VENDOR_SV, OWN_SV]


def test_own_warning_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(backend, files, top, root, incdirs):  # type: ignore[no-untyped-def]
        return [_f(OWN_SV, tool=backend.name)]

    monkeypatch.setattr(lint_module, "run_backend", fake)
    report = lint([Path(OWN_SV)], "top", Path("/run"), tools=["slang"])
    assert not report.ok


# ----------------------------------------------------------------- real tools (eda)

# AXI-Lite structs through PULP's generic axi_cut: proves the vendored closure, the
# include directories and the lint scope end to end (and that axi_cut takes Lite types).
TOP = """\
`include "axi/typedef.svh"

module vendor_top (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic [31:0] addr_i,
    input  logic        valid_i,
    output logic        ready_o
);
  typedef logic [31:0] addr_t;
  typedef logic [31:0] data_t;
  typedef logic [3:0] strb_t;
  `AXI_LITE_TYPEDEF_ALL(lite, addr_t, data_t, strb_t)

  lite_req_t  slv_req, mst_req;
  lite_resp_t slv_resp, mst_resp;

  always_comb begin
    slv_req          = '0;
    slv_req.ar.addr  = addr_i;
    slv_req.ar_valid = valid_i;
    mst_resp         = '0;
    mst_resp.ar_ready = 1'b1;
  end
  assign ready_o = slv_resp.ar_ready;

  axi_cut #(
      .aw_chan_t (lite_aw_chan_t),
      .w_chan_t  (lite_w_chan_t),
      .b_chan_t  (lite_b_chan_t),
      .ar_chan_t (lite_ar_chan_t),
      .r_chan_t  (lite_r_chan_t),
      .axi_req_t (lite_req_t),
      .axi_resp_t(lite_resp_t)
  ) u_cut (
      .clk_i,
      .rst_ni,
      .slv_req_i (slv_req),
      .slv_resp_o(slv_resp),
      .mst_req_o (mst_req),
      .mst_resp_i(mst_resp)
  );

  // Only the AR handshake is used here.
  logic unused;
  assign unused = ^{mst_req, slv_resp};
endmodule
"""


def _stage(root: Path) -> tuple[list[Path], list[Path]]:
    src = resolve_sources(["bender:axi:axi_cut"])
    for rel in (*src.files, *src.headers):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text((files("hdl_bag") / rel).read_text(encoding="utf-8"))
    (root / "rtl").mkdir(exist_ok=True)
    (root / "rtl" / "vendor_top.sv").write_text(TOP)
    return [*map(Path, src.files), Path("rtl/vendor_top.sv")], [*map(Path, src.incdirs)]


@pytest.mark.eda
def test_vendored_design_lints_and_compiles(tmp_path: Path) -> None:
    sources, incdirs = _stage(tmp_path)
    report = lint(sources, "vendor_top", tmp_path, incdirs=incdirs)
    assert report.ok, report.format()
    assert all(is_third_party(f.file) for f in report.third_party)
    built = compile_design(sources, "vendor_top", tmp_path, incdirs=incdirs)
    assert built.ok, built.format()
