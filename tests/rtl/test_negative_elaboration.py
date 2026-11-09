# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Negative elaboration (Phase 3 known gap 6): every parameter check in the in-house RTL fires.

Each case elaborates one in-house module, as the top of a two-line wrapper, with its default
(legal) parameters and then with one illegal override. Each tool (slang and Verilator) must
report the check's message on the illegal one, and nothing of the kind on the legal one, so
the report comes from the check and not from a missing file. slang reports the check as an
error. Verilator reports an elaboration-time ``$error`` / ``$fatal`` as a USERERROR /
USERFATAL warning; ``bag lint`` fails on any unwaived warning, and no built-in waiver covers
those rules (checked below), so lint still stops there. Verilator stops at warnings of its
early passes before it elaborates, and the bare wrapper leaves every port open (PINMISSING),
so here it runs with -Wno-fatal to reach elaboration. A meta-test requires a
case for every ``$error`` / ``$fatal`` message in rtl/inhouse/, so a new check cannot go
untested.
"""

import re
from importlib.resources import files
from pathlib import Path

import pytest

from hdl_bag.flows.backends import BACKENDS, Verilator
from hdl_bag.flows.base import run_backend
from hdl_bag.templates import builtin_registry
from hdl_bag.templates.sources import resolve_sources

INHOUSE = Path(str(files("hdl_bag"))) / "rtl" / "inhouse"

# (module, illegal parameter overrides, the check's message)
CASES: list[tuple[str, str, str]] = [
    ("bag_sync", ".Stages(1)", "bag_sync: Stages must be at least 2"),
    ("bag_rst_sync", ".Stages(1)", "bag_rst_sync: Stages must be at least 2"),
    ("bag_ahb2apb", ".HDataWidth(32), .PDataWidth(64)",
     "bag_ahb2apb: need PDataWidth <= HDataWidth <= 8 * PDataWidth"),
    ("bag_ahb2apb", ".HDataWidth(128), .PDataWidth(8)",
     "bag_ahb2apb: need PDataWidth <= HDataWidth <= 8 * PDataWidth"),
    ("bag_ahb2apb", ".AddrOffset(32'h2)", "bag_ahb2apb: AddrOffset must be aligned to the AHB data width"),
    ("bag_apb2ahb", ".PDataWidth(64), .HDataWidth(32)", "bag_apb2ahb: need PDataWidth <= HDataWidth"),
    ("bag_apb2ahb", ".AddrOffset(32'h2)", "bag_apb2ahb: AddrOffset must be aligned to the AHB data width"),
    ("bag_apb2axi", ".PDataWidth(64), .ADataWidth(32)", "bag_apb2axi: need PDataWidth <= ADataWidth"),
    ("bag_apb2axi", ".AddrOffset(32'h2)", "bag_hs_axi_mst: AddrOffset must be aligned to both data widths"),
    ("bag_axi_cdc", ".MstDataWidth(64)", "bag_axi_cdc needs the same data width on both sides"),
    ("bag_axi_cdc", ".MstIdWidth(2)", "bag_axi_cdc needs the same ID width on both sides"),
    ("bag_axi_cut", ".MstDataWidth(64)", "bag_axi_cut needs the same data width on both sides"),
    ("bag_axi_cut", ".MstIdWidth(2)", "bag_axi_cut needs the same ID width on both sides"),
    ("bag_axi_dw", ".MstIdWidth(2)", "bag_axi_dw needs the same ID width on both sides"),
    ("bag_axi_fifo", ".MstDataWidth(64)", "bag_axi_fifo needs the same data width on both sides"),
    ("bag_axi_fifo", ".MstIdWidth(2)", "bag_axi_fifo needs the same ID width on both sides"),
    ("bag_axi_iw", ".MstDataWidth(64)", "bag_axi_iw needs the same data width on both sides"),
    ("bag_axi_shared_bus", ".NoMasters(2), .IdWidthSlv(1)", "IdWidthSlv must be IdWidthMst + clog2(NoMasters)"),
    ("bag_axi_shared_bus", ".NoMasters(2), .IdWidthSlv(2), .FixedPrio(1'b1)",
     "fixed-priority masters need distinct Priority values"),
    ("bag_axi_to_axil", ".MstDataWidth(64)", "bag_axi_to_axil needs the same data width on both sides"),
    ("bag_axi_xbar", ".NoMasters(2), .IdWidthSlv(1)", "IdWidthSlv must be IdWidthMst + clog2(NoMasters)"),
    ("bag_axi2ahb", ".AddrOffset(32'h2)", "bag_axi2ahb_core: AddrOffset must be aligned to the data width"),
    ("bag_axi2ahb", ".DownAddrWidth(9)", "bag_axi2ahb_core: HAddrWidth must be at least 10 (1 KB bursts)"),
    ("bag_axil_cdc", ".MstDataWidth(64)", "bag_axil_cdc needs the same data width on both sides"),
    ("bag_axil_cut", ".MstDataWidth(64)", "bag_axil_cut needs the same data width on both sides"),
    ("bag_axil_fifo", ".MstDataWidth(64)", "bag_axil_fifo needs the same data width on both sides"),
    ("bag_axil_shared_bus", ".NoMasters(2), .FixedPrio(1'b1)",
     "fixed-priority masters need distinct Priority values"),
    ("bag_axil_to_axi", ".MstDataWidth(64)", "bag_axil_to_axi needs the same data width on both sides"),
]  # fmt: skip
IDS = [f"{m}-{i}" for i, (m, _, _) in enumerate(CASES)]
TOOLS = ("slang", "verilator")
# How Verilator reports an elaboration-time $error and $fatal: as warnings.
USER_RULES = ("USERERROR", "USERFATAL")


def _checks_in_rtl() -> set[tuple[str, str]]:
    """(module, message) of every $error / $fatal in rtl/inhouse/. Each must be a literal
    message the pattern reads, so none can hide from the meta-test."""
    out = set()
    for path in INHOUSE.glob("*.sv"):
        text = path.read_text(encoding="utf-8")
        found = re.findall(r'\$(?:error|fatal)\((?:1, )?"([^"]+)"\)', text)
        assert len(found) == len(re.findall(r"\$(?:error|fatal)\b", text)), path.name
        out |= {(path.stem, msg) for msg in found}
    return out


# A check inside a module the case elaborates through its parent: (case module, check module).
INSIDE = {"bag_apb2axi": "bag_hs_axi_mst", "bag_axi2ahb": "bag_axi2ahb_core"}


def test_every_check_in_the_rtl_has_a_case() -> None:
    covered = set()
    for module, _, msg in CASES:
        inner = INSIDE.get(module)
        covered.add((inner if inner and msg.startswith(f"{inner}:") else module, msg))
    assert _checks_in_rtl() == covered


def test_no_builtin_waiver_hides_a_parameter_check() -> None:
    from hdl_bag.flows.lint import builtin_waivers

    for tool in TOOLS:
        assert not [w for w in builtin_waivers(tool) if w.rule in USER_RULES]


def _sources(module: str) -> tuple[list[Path], list[Path]]:
    """MODULE's files in compile order, and include directories: from the template that uses
    it, or the file alone (bag_sync, bag_rst_sync)."""
    rel = f"rtl/inhouse/{module}.sv"
    registry = builtin_registry()
    manifests = [m for n in registry.names() if (m := registry.get(n)) and rel in m.sources]
    refs = manifests[0].sources if manifests else [rel]
    src = resolve_sources(list(refs))
    root = Path(str(files("hdl_bag")))
    return [root / f for f in src.files], [root / d for d in src.incdirs]


def _elaborate(tmp_path: Path, module: str, params: str, tool: str) -> list[str]:
    """The error messages TOOL reports elaborating MODULE with PARAMS under a wrapper top."""
    top = tmp_path / "neg_top.sv"
    top.write_text(f"module neg_top;\n  {module} #({params}) u_dut ();\nendmodule\n")
    srcs, incdirs = _sources(module)
    backend = BACKENDS[tool] if tool == "slang" else _VerilatorToElaboration()
    found = run_backend(backend, [*srcs, top], "neg_top", tmp_path, incdirs)
    return [f.message for f in found if f.severity == "error" or f.rule in USER_RULES]


class _VerilatorToElaboration(Verilator):
    """Verilator's lint run, going on past warnings to elaboration (-Wno-fatal)."""

    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]:
        return [*super().command(files, top, incdirs), "-Wno-fatal"]


@pytest.mark.eda
@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(("module", "params", "message"), CASES, ids=IDS)
def test_illegal_parameters_stop_elaboration(tmp_path: Path, tool: str, module: str,
                                             params: str, message: str) -> None:  # fmt: skip
    legal = _elaborate(tmp_path, module, "", tool)
    assert legal == [], f"{module} with its defaults: {legal}"
    errors = _elaborate(tmp_path, module, params, tool)
    assert any(message in e for e in errors), f"{module} #({params}): {errors}"


# BAG's protocol checkers (formal/, PR 2b): the formal flow reads them with slang, so slang
# elaborates them here.
FORMAL = Path(str(files("hdl_bag"))) / "formal"
FORMAL_CASES: list[tuple[str, str, str]] = [
    ("bag_faxi4", ".MaxOutstanding(3)", "bag_faxi4: MaxOutstanding must be a power of two from 2 to 8"),
    ("bag_faxi4", ".MaxOutstanding(16)", "bag_faxi4: MaxOutstanding must be a power of two from 2 to 8"),
    ("bag_faxi4", ".AddrWidth(11)", "bag_faxi4: AddrWidth must be at least 12"),
    ("bag_faxi4", ".DataWidth(24)", "bag_faxi4: DataWidth must be a power of two from 8 to 1024"),
    ("bag_faxi4", ".DataWidth(2048)", "bag_faxi4: DataWidth must be a power of two from 8 to 1024"),
    ("bag_faxi4", ".IdWidth(0)", "bag_faxi4: IdWidth must be at least 1"),
    ("bag_fahb", ".DataWidth(4)", "bag_fahb: DataWidth must be a power of two from 8 to 1024"),
]  # fmt: skip


def test_every_check_in_the_protocol_checkers_has_a_case() -> None:
    found = set()
    for path in FORMAL.glob("*.sv"):
        text = path.read_text(encoding="utf-8")
        msgs = re.findall(r'\$(?:error|fatal)\((?:1, )?"([^"]+)"\)', text)
        assert len(msgs) == len(re.findall(r"\$(?:error|fatal)\b", text)), path.name
        found |= {(path.stem, msg) for msg in msgs}
    assert found == {(m, msg) for m, _, msg in FORMAL_CASES}


@pytest.mark.eda
@pytest.mark.parametrize(("module", "params", "message"), FORMAL_CASES,
                         ids=[f"{m}-{i}" for i, (m, _, _) in enumerate(FORMAL_CASES)])  # fmt: skip
def test_illegal_checker_parameters_stop_elaboration(tmp_path: Path, module: str, params: str,
                                                     message: str) -> None:  # fmt: skip
    def errors(p: str) -> list[str]:
        top = tmp_path / "neg_top.sv"
        top.write_text(f"module neg_top;\n  {module} #({p}) u_dut ();\nendmodule\n")
        found = run_backend(BACKENDS["slang"], [FORMAL / f"{module}.sv", top], "neg_top",
                            tmp_path, [])  # fmt: skip
        return [f.message for f in found if f.severity == "error"]

    legal = errors("")
    assert legal == [], f"{module} with its defaults: {legal}"
    illegal = errors(params)
    assert any(message in e for e in illegal), f"{module} #({params}): {illegal}"
