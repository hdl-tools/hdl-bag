# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Checks: the golden spec has only its WRAP warnings; one broken workbook per rule ID.

Each case is the demo_ss sample plus one mutation, and asserts the exact messages.
"""

import dataclasses
import re
import shutil
from pathlib import Path

import pytest
from dsl import SAMPLE_WRAP, unexpected, wrap_refusals
from openpyxl import Workbook

from hdl_bag.checks import RULES, run_checks
from hdl_bag.reader import read_workbook
from hdl_bag.template import build_workbook
from hdl_bag.template.data import SpecData
from hdl_bag.template.sample_demo_ss import SAMPLE as SAMPLE_SPEC

GOLDEN_DIR = Path(__file__).parents[1] / "golden"
HEADER_ROWS = {("Hierarchy", "placement"): 15, ("Wrapper", "exposed"): 17}

Value = str | int | float | None
Edit = tuple[str, int, str, Value]  # (sheet, row, header, value)


def _col(wb: Workbook, sheet: str, row: int, header: str) -> int:
    ws = wb[sheet]
    if sheet == "Hierarchy" and row >= 16:
        hdr = 15
    elif sheet == "Wrapper" and row >= 18:
        hdr = 17
    elif sheet in ("Project", "Wrapper"):
        return 2  # Setting / Value blocks: always column B
    else:
        hdr = 4
    for cell in ws[hdr]:
        if cell.value == header:
            return int(cell.column)
    raise KeyError(f"{sheet}: no header {header!r} in row {hdr}")


def _apply(wb: Workbook, edits: list[Edit]) -> None:
    for sheet, row, header, value in edits:
        # ws.cell(r, c, None) would leave the cell unchanged; assign to clear it.
        wb[sheet].cell(row, _col(wb, sheet, row, header)).value = value


def _row(sheet: str, row: int, **values: Value) -> list[Edit]:
    return [(sheet, row, k.replace("_", " "), v) for k, v in values.items()]


def _messages(tmp_path: Path, edits: list[Edit] | SpecData) -> list[str]:
    """Checks of the sample with EDITS applied, or of the whole workbook EDITS when it is a spec."""
    if isinstance(edits, SpecData):
        wb = build_workbook(data=edits)
    else:
        wb = build_workbook(sample=True)
        _apply(wb, edits)
    path = tmp_path / "spec.xlsx"
    wb.save(path)
    shutil.copy(GOLDEN_DIR / "header.txt", tmp_path / "header.txt")
    return _without_sample_wrap(
        [f.message for f in run_checks(read_workbook(path), tmp_path).findings]
    )


# The sample's own PSTRB-tie warnings (D106): wdt0 and rtc0 have PSTRB tied to the default.
SAMPLE_PSTRB = [
    "Port_Options row 16: PSTRB is tied on wdt0: a write of part of its 32-bit word overwrites "
    "the rest of the word. Drive PSTRB, or write whole words only (PORTOPT-PSTRB-WORD)",
    "Port_Options row 17: PSTRB is tied on rtc0: a write of part of its 32-bit word overwrites "
    "the rest of the word. Drive PSTRB, or write whole words only (PORTOPT-PSTRB-WORD)",
]


def _without_sample_wrap(messages: list[str]) -> list[str]:
    """MESSAGES without the sample's own warnings: its WRAP warnings (SAMPLE_WRAP) when they
    are exactly those, and its PSTRB-tie warnings (SAMPLE_PSTRB). Any other WRAP warning, or
    one of the sample's missing, keeps every WRAP message."""
    messages = [m for m in messages if m not in SAMPLE_PSTRB]
    if sorted(wrap_refusals(messages)) != sorted(SAMPLE_WRAP):
        return messages
    return [m for m in messages if "(AXI-WRAP-REFUSED)" not in m]


def _user(row: int, inst: str, port: str, direction: str, width: int, conn: str) -> list[Edit]:
    """A User_Ports row (D41)."""
    cells = _row("User_Ports", row, Instance=inst, Port=port, Direction=direction, Connection=conn)
    return [*cells, ("User_Ports", row, "Width (bits)", width)]


# aon_apb becomes a user-supplied fabric, so User_Ports rows may name it.
EXT: list[Edit] = [("Fabrics", 9, "Backing template", "bag_external")]


def _clear(sheet: str, row: int, headers: tuple[str, ...]) -> list[Edit]:
    return [(sheet, row, h, None) for h in headers]


MM_COLS = ("Slave / bridge", "Region", "Base (hex)", "Size (hex)", "Access", "Secure only", "Notes")
SPARE_SLAVE: list[Edit] = [
    *[("Slaves", 16, h, v) for h, v in (
        ("Slave", "spare0"), ("Protocol", "APB4"), ("Data width (bits)", 32),
        ("Addr width (bits)", 12), ("Clock domain", "D_PER"), ("Error response", "PSLVERR"),
    )],
    ("Wrapper", 38, "Port", "spare0"), ("Wrapper", 38, "Exposed at top", "Y"),
]  # fmt: skip


def _mm_row(row: int, target: str, region: str, base: str, size: str) -> list[Edit]:
    vals = (target, region, base, size, "RW", "N", "")
    return [("Memory_Map", row, h, v) for h, v in zip(MM_COLS, vals, strict=True)]


def _freq_case(kind: str, value: Value) -> tuple[str, list[Edit], list[str]]:
    """A Clocks > Freq (MHz) that is no positive number: every period is derived from it."""
    return (
        f"VALUE-ILLEGAL-freq-{kind}",
        [("Clocks", 5, "Freq (MHz)", value)],
        [f"Clocks row 5: Freq (MHz) {value!r} is not a positive number (VALUE-ILLEGAL)"],
    )


# A workbook with no bus at all: a case for a rule that edits of the sample cannot reach.
NO_BUS = dataclasses.replace(
    SAMPLE_SPEC, fabrics=[], masters=[], slaves=[], bridges=[], topology=[], access_rules=[],
    memory_map=[], address_decode=[], port_options=[], user_ports=[], exposed_ports=[],
    placement=[], sub_wrappers=[list(SAMPLE_SPEC.sub_wrappers[0])],
)  # fmt: skip

# (case id, edits or a whole spec, expected messages)
CASES: list[tuple[str, list[Edit] | SpecData, list[str]]] = [
    ("FAB-NONE", NO_BUS,
     ["Fabrics: the spec lists no fabric, so there is no bus to generate. Add a row for each fabric (FAB-NONE)"]),
    ("VALUE-MISSING", [("Clocks", 5, "Freq (MHz)", None)],
     ["Clocks row 5: Freq (MHz) is required (VALUE-MISSING)"]),
    ("VALUE-ILLEGAL", [("Resets", 5, "Polarity", "low")],
     ["Resets row 5: Polarity 'low' is not one of active-low, active-high (VALUE-ILLEGAL)"]),
    *[_freq_case(kind, value)
      for kind, value in (("zero", 0), ("negative", -50), ("text", "fast"), ("bool", True))],
    ("PROJ-SCHEMA-VERSION", [("Project", 6, "Value", "0.2")],
     ["Project row 6: Spec schema version '0.2' differs from '0.7' expected by this BAG release (PROJ-SCHEMA-VERSION)"]),
    ("PROJ-MODULE-PREFIX", [("Project", 18, "Value", "Demo-SS")],
     ["Project row 18: Module prefix 'Demo-SS' is not a lowercase SystemVerilog identifier (PROJ-MODULE-PREFIX)"]),
    ("PROJ-MODULE-PREFIX-underscore", [("Project", 18, "Value", "demo_ss_")],
     ["Project row 18: Module prefix 'demo_ss_' must not end with an underscore: BAG adds one (PROJ-MODULE-PREFIX)"]),
    ("PROJ-PREFIX-COLLISION", [("Project", 18, "Value", "demo_ss"), ("Hierarchy", 9, "Sub-wrapper", "bus_top"),
                               ("Hierarchy", 9, "Parent", "demo_ss_bus_top"), ("Hierarchy", 20, "Sub-wrapper", "bus_top")],
     ["Project row 18: with Module prefix 'demo_ss', sub-wrapper bus_top is generated as demo_ss_bus_top, the name of the top wrapper; choose another Module prefix (PROJ-PREFIX-COLLISION)"]),
    ("PROJ-PREFIX-COLLISION-interface", [("Project", 18, "Value", "demo_ss"), ("Hierarchy", 9, "Sub-wrapper", "apb4_if"),
                                         ("Hierarchy", 9, "Parent", "demo_ss_bus_top"), ("Hierarchy", 20, "Sub-wrapper", "apb4_if")],
     ["Project row 18: with Module prefix 'demo_ss', sub-wrapper apb4_if is generated as demo_ss_apb4_if, the name of a generated interface; choose another Module prefix (PROJ-PREFIX-COLLISION)"]),
    ("PROJ-PREFIX-COLLISION-keyword", [("Project", 18, "Value", "always"), ("Hierarchy", 9, "Sub-wrapper", "comb"),
                                       ("Hierarchy", 9, "Parent", "demo_ss_bus_top"), ("Hierarchy", 20, "Sub-wrapper", "comb")],
     ["Project row 18: with Module prefix 'always', sub-wrapper comb is generated as always_comb, a SystemVerilog keyword; choose another Module prefix (PROJ-PREFIX-COLLISION)"]),
    ("PROJ-NAME-LENGTH", [("Project", 18, "Value", "abcdefghij" * 4)],
     ["Project row 18: with Module prefix 'abcdefghijabcdefghijabcdefghijabcdefghij', 5 names BAG can generate are longer than 64 characters; the longest is abcdefghijabcdefghijabcdefghijabcdefghij_axi_burst_splitter_gran_counters (73); shorten the prefix (PROJ-NAME-LENGTH)"]),
    ("NAME-RESERVED", [("Project", 5, "Value", "addr")],
     ["Project row 5: with Project name 'addr', the formal decode harness is named bag_addr_decode, which is also the name of the in-house module; choose another Project name (NAME-RESERVED)"]),
    ("NAME-RESERVED-and-prefix", [("Project", 5, "Value", "addr"), ("Project", 18, "Value", "bag")],
     ["Project row 5: with Project name 'addr', the formal decode harness is named bag_addr_decode, which is also how the vendored PULP module addr_decode is generated; choose another Project name or Module prefix (NAME-RESERVED)"]),
    ("NAME-RESERVED-package", [("Wrapper", 9, "Value", "bag_demo_ss_reset_props")],
     ["Wrapper row 9: the parameter package is named bag_demo_ss_reset_props, which is also the formal reset property module; choose another Parameter package (NAME-RESERVED)"]),
    ("HIER-RESERVED-NAME-formal-unit", [("Hierarchy", 7, "Sub-wrapper", "bag_demo_ss_unit_x"), ("Hierarchy", 20, "Sub-wrapper", "bag_demo_ss_unit_x")],
     ["Hierarchy row 7: sub-wrapper bag_demo_ss_unit_x has the name of a formal unit harness; rename the sub-wrapper (HIER-RESERVED-NAME)"]),
    # A setting that does not apply is warned about and ignored, never validated: one message,
    # and not a VALUE-ILLEGAL from inside resolve (#156).
    ("FAB-NA-SETTING-junk-value", [("Fabrics", 7, "Max mst trans", "abc")],
     ["Fabrics row 7: Max mst trans 'abc' does not apply to a non-AXI fabric; set n/a (FAB-NA-SETTING)"]),
    ("WRAP-FILE-HEADER", [("Wrapper", 10, "Value", "missing.txt")],
     ["Wrapper row 10: File header 'missing.txt' not found next to the workbook (WRAP-FILE-HEADER)"]),
    ("WRAP-PATTERN", [("Wrapper", 7, "Value", "{port}_{signal}")],
     ["Wrapper row 7: Signal naming pattern '{port}_{signal}' must use each of {port}, {proto}, {signal} exactly once (WRAP-PATTERN)"]),
    ("NAME-UNIQUE", [("Masters", 10, h, v) for h, v in (
         ("Master", "cpu0"), ("Protocol", "AXI4"), ("Data width (bits)", 64), ("Addr width (bits)", 32),
         ("ID width (bits)", 4), ("User width (bits)", 0), ("Clock domain", "D_SYS"),
         ("Max outstanding rd", 1), ("Max outstanding wr", 1))],
     ["Masters row 10: cpu0 is already defined on Masters row 5 (NAME-UNIQUE)"]),
    ("NAME-UNIQUE-region", _mm_row(21, "sram0", "mem", "0x1004_0000", "0x0004_0000"),
     ["Memory_Map row 21: region mem of sram0 repeats row 5 (NAME-UNIQUE)"]),
    ("NAME-IDENT", [("Memory_Map", 20, "Region", "Regs")],
     ["Memory_Map row 20: Region 'Regs' is not a lowercase SystemVerilog identifier (NAME-IDENT)"]),
    ("VALUE-ILLEGAL-master-id-width", [("Masters", 5, "ID width (bits)", "abc")],
     ["Masters row 5: ID width (bits) 'abc' is not an integer >= 1 (VALUE-ILLEGAL)"]),
    ("NAME-IDENT-reset-domain", [("Resets", 5, "Domain", "Foo-bar")],
     ["Clocks row 5: domain D_CPU has no reset on Resets (CLK-DOMAIN-NO-RESET)",
      "Resets row 5: Domain 'Foo-bar' is not an SystemVerilog identifier (NAME-IDENT)"]),
    # Fabrics row 9 is read by text() (the adapter derivation) before check_names validates
    # it, so on a shared parse cache the first reader decided: a bad value was reported as
    # REF-UNKNOWN rather than NAME-IDENT, and a blank one was never reported at all, which
    # left bag check to fail an assertion in resolve (#149).
    ("NAME-IDENT-fabric-domain", [("Fabrics", 9, "Clock domain", "Foo-bar")],
     ["Fabrics row 9: Clock domain 'Foo-bar' is not an SystemVerilog identifier (NAME-IDENT)",
      "Slaves row 12: wdt0 must match the clock domain of fabric aon_apb: adapters exist only between AXI4/AXI4-Lite ports and fabrics (PORT-ADAPTER)",
      "Slaves row 13: rtc0 must match the clock domain of fabric aon_apb: adapters exist only between AXI4/AXI4-Lite ports and fabrics (PORT-ADAPTER)"]),
    ("VALUE-MISSING-fabric-domain", [("Fabrics", 9, "Clock domain", None)],
     ["Fabrics row 9: Clock domain is required (VALUE-MISSING)"]),
    ("REF-UNKNOWN", [("Masters", 6, "Clock domain", "D_X")],
     ["Masters row 6: domain D_X is not defined on Clocks (REF-UNKNOWN)"]),
    ("CLK-DOMAIN-TWO-CLOCKS", [("Clocks", 9, h, v) for h, v in (
        ("Clock", "clk_x"), ("Freq (MHz)", 10), ("Domain", "D_SYS"), ("Reset", "rst_sys_n"))],
     ["Clocks row 9: domain D_SYS already has a clock on Clocks row 6 (CLK-DOMAIN-TWO-CLOCKS)"]),
    ("CLK-DOMAIN-NO-RESET", [("Clocks", 9, h, v) for h, v in (
        ("Clock", "clk_x"), ("Freq (MHz)", 10), ("Domain", "D_X"), ("Reset", "rst_sys_n"))],
     ["Clocks row 9: domain D_X has no reset on Resets (CLK-DOMAIN-NO-RESET)",
      "Clocks row 9: reset rst_sys_n belongs to domain D_SYS, not D_X (CLK-RESET-DOMAIN)"]),
    ("CLK-RESET-DOMAIN", [("Clocks", 5, "Reset", "rst_sys_n")],
     ["Clocks row 5: reset rst_sys_n belongs to domain D_SYS, not D_CPU (CLK-RESET-DOMAIN)"]),
    ("CLK-CDC-RESET", [("Resets", 5, "Deassertion", "async")],
     ["Resets row 5: reset rst_cpu_n (async assertion, async deassertion) resets one side of the "
      "clock-domain crossing cpu0_cdc_2_main_xbar (D_CPU -> D_SYS); a crossing needs async "
      "assertion and sync deassertion on both sides (CLK-CDC-RESET)"]),
    ("CLK-CDC-RESET-bridge", [("Resets", 8, "Assertion", "sync")],
     ["Resets row 8: reset rst_aon_n (sync assertion, sync deassertion) resets one side of the "
      "clock-domain crossing br_ahb2main (D_AON -> D_SYS); a crossing needs async assertion and "
      "sync deassertion on both sides (CLK-CDC-RESET)"]),
    ("WIDTH-DATA", [("Masters", 7, "Data width (bits)", 128)],
     ["Masters row 7: data width 128 is not legal for AXI4-Lite (32, 64) (WIDTH-DATA)"]),
    ("WIDTH-ADDR", [("Slaves", 7, "Addr width (bits)", 40)],
     ["Slaves row 7: address width 40 is wider than fabric periph_apb (32) (WIDTH-ADDR)"]),
    ("WIDTH-ID", [("Masters", 8, "ID width (bits)", 4)],
     ["Masters row 8: ID width applies to AXI4 only; leave it blank for AHB-Lite (WIDTH-ID)"]),
    ("WIDTH-USER", [("Masters", 8, "User width (bits)", 2)],
     ["Masters row 8: User width 2 applies to AXI4 only; use 0 or blank for AHB-Lite (WIDTH-USER)"]),
    ("TMPL-FABRIC", [("Fabrics", 6, "Backing template", "bag_axi_shared_bus")],
     ["Fabrics row 6: bag_axi_shared_bus does not support AXI4-Lite (TMPL-FABRIC)"]),
    ("TMPL-BRIDGE", [("Bridges", 6, "Template", "bag_axi2apb")],
     ["Bridges row 6: bag_axi2apb needs a AXI4 or AXI4-Lite upstream fabric; ahb_bus is AHB-Lite (TMPL-BRIDGE)"]),
    ("FAB-TOPOLOGY", [("Fabrics", 9, "Topology", "single-layer"), ("Fabrics", 9, "Backing template", "bag_external")],
     ["Fabrics row 9: topology single-layer does not suit APB4 (decoder-mux) (FAB-TOPOLOGY)"]),
    ("FAB-ARBITRATION", [("Fabrics", 7, "Arbitration", "round-robin")],
     ["Fabrics row 7: round-robin is not supported by bag_apb_mux (single-master) (FAB-ARBITRATION)"]),
    ("FAB-SINGLE-MASTER", [
        *[("Masters", 10, h, v) for h, v in (
            ("Master", "apbm0"), ("Protocol", "APB4"), ("Data width (bits)", 32),
            ("Addr width (bits)", 32), ("Clock domain", "D_PER"))],
        *_row("Topology", 29, Source="apbm0", Destination="periph_apb", Port_index=1),
        ("Address_Decode", 14, "Master", "apbm0"), ("Address_Decode", 14, "Miss response", "PSLVERR"),
        ("Wrapper", 38, "Port", "apbm0"), ("Wrapper", 38, "Exposed at top", "Y")],
     ["Fabrics row 7: periph_apb has 2 master ports but Arbitration is single-master (FAB-SINGLE-MASTER)"]),
    ("FAB-EMPTY-SIDE", [("Fabrics", 10, h, v) for h, v in (
        ("Fabric", "spare_apb"), ("Protocol", "APB4"), ("Topology", "decoder-mux"),
        ("Data width (bits)", 32), ("Addr width (bits)", 32), ("Clock domain", "D_PER"),
        ("Arbitration", "single-master"), ("Backing template", "bag_apb_mux"))],
     ["Fabrics row 10: fabric spare_apb has no master ports in Topology (FAB-EMPTY-SIDE)",
      "Fabrics row 10: fabric spare_apb has no slave ports in Topology (FAB-EMPTY-SIDE)"]),
    ("FAB-NA-SETTING", [("Fabrics", 7, "Latency mode", "CUT_ALL_AX")],
     ["Fabrics row 7: Latency mode 'CUT_ALL_AX' does not apply outside a crossbar; set n/a (FAB-NA-SETTING)"]),
    ("EXT-MODULE-NAME", [*EXT, ("Fabrics", 9, "External module", "1bad")],
     ["Fabrics row 9: External module '1bad' is not a SystemVerilog identifier (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-reserved", [*EXT, ("Fabrics", 9, "External module", "bag_axi_guard")],
     ["Fabrics row 9: External module 'bag_axi_guard' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-top", [*EXT, ("Fabrics", 9, "External module", "demo_ss_bus_top")],
     ["Fabrics row 9: External module 'demo_ss_bus_top' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    # ddr0 at the fabric width: no upsizer in front of it, so dma0's WRAP warning goes and
    # cpu0's, from its own downsizer, stays.
    ("AXI-WRAP-REFUSED", [("Slaves", 5, "Data width (bits)", 64),
                          ("Hierarchy", 25, "Instance", None), ("Hierarchy", 25, "Sub-wrapper", None),
                          ("Hierarchy", 25, "RTL instance name", None), ("Hierarchy", 25, "Notes", None)],
     ["Masters row 5: WRAP bursts from cpu0 to br_main2cfg, br_main2per, ddr0, sram0 get SLVERR: "
      "dw adapter cpu0_dw_2_main_xbar cannot convert them. Send INCR bursts on this path, or keep "
      "it at one width and protocol (AXI-WRAP-REFUSED)"]),
    ("EXT-MODULE-NAME-keyword", [*EXT, ("Fabrics", 9, "External module", "module")],
     ["Fabrics row 9: External module 'module' is a SystemVerilog keyword (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-package", [*EXT, ("Fabrics", 9, "External module", "demo_ss_bus_pkg")],
     ["Fabrics row 9: External module 'demo_ss_bus_pkg' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-sub-wrapper", [*EXT, ("Fabrics", 9, "External module", "main_wrapper")],
     ["Fabrics row 9: External module 'main_wrapper' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-harness", [*EXT, ("Fabrics", 9, "External module", "demo_ss_bus_top_flat")],
     ["Fabrics row 9: External module 'demo_ss_bus_top_flat' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-interface", [*EXT, ("Fabrics", 9, "External module", "demo_ss_apb4_if")],
     ["Fabrics row 9: External module 'demo_ss_apb4_if' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-vendored", [*EXT, ("Fabrics", 9, "External module", "axi_xbar")],
     ["Fabrics row 9: External module 'axi_xbar' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-default", [*EXT, ("Fabrics", 7, "Backing template", "bag_external"),
                                 ("Fabrics", 9, "External module", "demo_ss_periph_apb")],
     ["Fabrics row 9: External module 'demo_ss_periph_apb' clashes with a module BAG generates (EXT-MODULE-NAME)"]),
    ("EXT-MODULE-NAME-case", [*EXT, ("Fabrics", 7, "Backing template", "bag_external"),
                              ("Fabrics", 7, "External module", "My_Ip"), ("Fabrics", 9, "External module", "my_ip")],
     ["Fabrics row 9: External module 'my_ip' differs from 'My_Ip' only in case, so their files clash on some systems (EXT-MODULE-NAME)"]),
    ("USERPORT-NAME-position", [*EXT, ("Fabrics", 9, "External module", "mine"),
                                *_user(5, "aon_apb", "s0_apb4_psel", "input", 1, "tie-0")],
     ["User_Ports row 5: s0_apb4_psel is a bus or clock port BAG already connects on aon_apb (USERPORT-NAME)"]),
    ("FAB-NA-SETTING-external", [("Fabrics", 5, "External module", "mine")],
     ["Fabrics row 5: External module 'mine' does not apply to bag_axi_xbar; set n/a (FAB-NA-SETTING)"]),
    ("EXT-MODULE-SHARED", [*EXT, ("Fabrics", 8, "Backing template", "bag_external"),
                           ("Fabrics", 9, "External module", "one"), ("Fabrics", 8, "External module", "one")],
     ["Fabrics row 9: aon_apb and ahb_bus share module one but differ in protocol, widths, attachments or user ports (EXT-MODULE-SHARED)"]),
    ("BRIDGE-FIFO-IGNORED", [("Bridges", 7, "FIFO depth", 2)],
     ["Bridges row 7: bag_ahb2axi handles one transfer at a time and ignores FIFO depth 2 (BRIDGE-FIFO-IGNORED)"]),
    ("FAB-ID-GROWTH", [("Masters", 5, "ID width (bits)", 16)],
     ["Fabrics row 5: master-side ID width of main_xbar is 16 bits (> 8): each master port's guard keeps 65536 in-flight counters per channel, one per ID (D65); narrow the master IDs if that area matters (FAB-GUARD-ID-WIDTH)",
      "Fabrics row 5: slave-side ID width of main_xbar is 18 bits (> 16) (FAB-ID-GROWTH)"]),
    ("FAB-GUARD-ID-WIDTH", [("Masters", 5, "ID width (bits)", 9)],
     ["Fabrics row 5: master-side ID width of main_xbar is 9 bits (> 8): each master port's guard keeps 512 in-flight counters per channel, one per ID (D65); narrow the master IDs if that area matters (FAB-GUARD-ID-WIDTH)"]),
    ("PORT-ADAPTER", [("Slaves", 7, "Clock domain", "D_SYS")],
     ["Slaves row 7: uart0 must match the clock domain of fabric periph_apb: adapters exist only between AXI4/AXI4-Lite ports and fabrics (PORT-ADAPTER)"]),
    ("PORT-ADAPTER-buffer", [("Port_Options", 10, "FIFO depth", 2)],
     ["Port_Options row 10: FIFO and register slices exist only on AXI4/AXI4-Lite ports; uart0 is APB4 (PORT-ADAPTER)"]),
    ("TOPO-UNKNOWN-NAME", _row("Topology", 29, Source="ghost", Destination="main_xbar", Port_index=4),
     ["Topology row 29: ghost is not defined on Masters, Slaves, Bridges or Fabrics (TOPO-UNKNOWN-NAME)"]),
    ("TOPO-ILLEGAL-LINK", _row("Topology", 29, Source="cpu0", Destination="ddr0", Port_index=0),
     ["Topology row 29: cpu0 (master) -> ddr0 (slave) is not master -> fabric, fabric -> slave, fabric -> bridge or bridge -> fabric (TOPO-ILLEGAL-LINK)"]),
    ("TOPO-DUPLICATE", _row("Topology", 29, Source="cpu0", Destination="main_xbar", Port_index=0),
     ["Topology row 29: link cpu0 -> main_xbar repeats row 5 (TOPO-DUPLICATE)"]),
    ("TOPO-MULTI-ATTACH", _row("Topology", 29, Source="cpu0", Destination="cfg_bus", Port_index=2),
     ["Topology row 29: cpu0 is already attached on row 5 (TOPO-MULTI-ATTACH)"]),
    ("TOPO-NOT-CONNECTED", SPARE_SLAVE,
     ["Slaves row 16: slave spare0 has no Topology link (TOPO-NOT-CONNECTED)"]),
    ("TOPO-INDEX-MISSING", [("Topology", 5, "Port index", None)],
     ["Topology row 5: link cpu0 -> main_xbar has no Port index (TOPO-INDEX-MISSING)"]),
    ("TOPO-INDEX-NOT-INTEGER", [("Topology", 5, "Port index", 1.5)],
     ["Topology row 5: Port index 1.5 is not an integer >= 0 (TOPO-INDEX-NOT-INTEGER)"]),
    ("TOPO-INDEX-OUT-OF-RANGE", [("Topology", 5, "Port index", 4)],
     ["Topology row 5: Port index 4 is outside 0..3 for the master ports of main_xbar (TOPO-INDEX-OUT-OF-RANGE)"]),
    ("TOPO-INDEX-REPEATED", [("Topology", 6, "Port index", 0)],
     ["Topology row 6: Port index 0 repeats row 5 on the master ports of main_xbar (TOPO-INDEX-REPEATED)"]),
    ("TOPO-PRIORITY-NOT-INTEGER", [("Topology", 13, "Priority", "high")],
     ["Topology row 13: Priority 'high' is not an integer >= 0 (TOPO-PRIORITY-NOT-INTEGER)"]),
    ("TOPO-PRIORITY-MISSING", [("Topology", 13, "Priority", None)],
     ["Topology row 13: master port br_main2cfg on fixed-priority fabric cfg_bus has no Priority (TOPO-PRIORITY-MISSING)"]),
    ("TOPO-PRIORITY-REPEATED", [("Topology", 13, "Priority", 0)],
     ["Topology row 14: Priority 0 repeats row 13 on cfg_bus (TOPO-PRIORITY-REPEATED)"]),
    ("TOPO-BRIDGE-CYCLE", [
        *_row("Bridges", 9, Bridge="br_per2main", Template="bag_apb2axi"),
        ("Bridges", 9, "Window base (hex)", "0x1000_0000"), ("Bridges", 9, "Pass-through address", "Y"),
        *_row("Topology", 29, Source="periph_apb", Destination="br_per2main", Port_index=4),
        *_row("Topology", 30, Source="br_per2main", Destination="main_xbar", Port_index=4),
        *_mm_row(21, "br_per2main", "sram", "0x1000_0000", "0x0004_0000"),
        ("Address_Decode", 14, "Master", "br_per2main"), ("Address_Decode", 14, "Miss response", "DECERR"),
        ("Wrapper", 38, "Port", "br_per2main"), ("Wrapper", 38, "Exposed at top", "N"),
        ("Wrapper", 38, "If not exposed", "internal")],
     ["Topology row 17: bridges link fabrics in a loop: main_xbar -> br_main2per -> periph_apb -> br_per2main -> main_xbar (TOPO-BRIDGE-CYCLE)"]),
    ("PORTOPT-PSTRB-WORD", [("Port_Options", 10, "PSTRB", "tie-default")],
     ["Port_Options row 10: PSTRB is tied on uart0: a write of part of its 32-bit word "
      "overwrites the rest of the word. Drive PSTRB, or write whole words only (PORTOPT-PSTRB-WORD)"]),
    ("AXI-SPLIT-REFUSED", [("Port_Options", 5, "CACHE", "tie-0")],
     ["Masters row 5: bursts, and transfers wider than a converter on the way, from cpu0 to "
      "clkrst0, gpio0, pmu0, spi0, timer0, uart0 get SLVERR where the converter must split them: "
      "CACHE is tied to 0 on cpu0, so they are non-modifiable. Drive CACHE, or tie it to the "
      "default (AXI-SPLIT-REFUSED)"]),
    ("ACC-UNKNOWN-NAME", [("Access_Rules", 5, "Master", "dma9")],
     ["Access_Rules row 5: dma9 is not a connected master or bridge (ACC-UNKNOWN-NAME)"]),
    ("ACC-NOT-SAME-FABRIC", [("Access_Rules", 6, "Slave / bridge", "uart0")],
     ["Access_Rules row 6: dbg0 is on main_xbar but uart0 is on periph_apb (ACC-NOT-SAME-FABRIC)"]),
    ("ACC-DUPLICATE", [*_row("Access_Rules", 9, Master="dbg0", Access="-"), ("Access_Rules", 9, "Slave / bridge", "ddr0")],
     ["Access_Rules row 9: rule dbg0 -> ddr0 repeats row 6 (ACC-DUPLICATE)"]),
    ("ACC-UNREACHABLE-SLAVE", [
        ("Access_Rules", 6, "Access", "-"),
        *_row("Access_Rules", 9, Master="cpu0", Access="-"), ("Access_Rules", 9, "Slave / bridge", "ddr0"),
        *_row("Access_Rules", 10, Master="dma0", Access="-"), ("Access_Rules", 10, "Slave / bridge", "ddr0")],
     ["Masters row 5: WRAP bursts from cpu0 to br_main2cfg, br_main2per, sram0 get SLVERR: dw "
      "adapter cpu0_dw_2_main_xbar cannot convert them. Send INCR bursts on this path, or keep it "
      "at one width and protocol (AXI-WRAP-REFUSED)",
      "Topology row 9: no master on main_xbar can reach ddr0 (ACC-UNREACHABLE-SLAVE)"]),
    ("MM-UNKNOWN-SLAVE", _mm_row(21, "ghost", "regs", "0x6000_0000", "0x0000_1000"),
     ["Memory_Map row 21: ghost is not defined on Slaves or Bridges (MM-UNKNOWN-SLAVE)"]),
    ("MM-NOT-CONNECTED", [*SPARE_SLAVE, *_mm_row(21, "spare0", "regs", "0x4000_4000", "0x0000_1000")],
     ["Slaves row 16: slave spare0 has no Topology link (TOPO-NOT-CONNECTED)",
      "Memory_Map row 21: spare0 has no Topology link as a Destination (MM-NOT-CONNECTED)"]),
    ("MM-OVERLAP", [("Memory_Map", 12, "Base (hex)", "0x4000_0000")],
     ["Memory_Map row 12: gpio0 0x4000_0000..0x4000_0FFF overlaps uart0 (Memory_Map row 11) on periph_apb (MM-OVERLAP)"]),
    ("MM-SIZE-NOT-POW2", [("Memory_Map", 20, "Size (hex)", "0x0000_3000")],
     ["Memory_Map row 20: size 0x0000_3000 is not a power of 2 (MM-SIZE-NOT-POW2)"]),
    ("MM-MISALIGNED", [("Memory_Map", 20, "Base (hex)", "0x5000_1800")],
     ["Memory_Map row 20: base 0x5000_1800 is not aligned to size 0x0000_1000 (MM-MISALIGNED)"]),
    ("MM-ADDR-RANGE", [("Memory_Map", 11, "Size (hex)", "0x0000_2000")],
     ["Memory_Map row 11: size 0x0000_2000 exceeds the 12-bit address space of uart0 (MM-ADDR-RANGE)"]),
    ("MM-SLAVE-ALIGN", _mm_row(21, "uart0", "alt", "0x4000_8000", "0x0000_1000"),
     ["Memory_Map row 21: uart0 0x4000_8000..0x4000_8FFF is outside the 12-bit block 0x4000_0000..0x4000_0FFF of its region regs (Memory_Map row 11) (MM-SLAVE-ALIGN)"]),
    ("MM-SMALL-AXI", [("Memory_Map", 9, "Size (hex)", "0x0000_0800")],
     ["Memory_Map row 9: pmu0 region regs is smaller than 4 KB on AXI fabric cfg_bus (MM-SMALL-AXI)"]),
    ("MM-PATH-NO-REGION", _clear("Memory_Map", 20, MM_COLS),
     ["Slaves row 13: slave rtc0 has no region in Memory_Map (MM-PATH-NO-REGION)"]),
    ("BRIDGE-WINDOW-BASE", [("Bridges", 5, "Window base (hex)", "0x4000_1000")],
     ["Bridges row 5: Window base 0x4000_1000 differs from br_main2per's lowest region 0x4000_0000 (BRIDGE-WINDOW-BASE)"]),
    ("BRIDGE-WINDOW-EMPTY", [("Memory_Map", 16, "Base (hex)", "0x5100_0000"), ("Bridges", 6, "Window base (hex)", "0x5100_0000")],
     ["Memory_Map row 16: br_ahb2aon window 0x5100_0000..0x5100_FFFF reaches no region on aon_apb (BRIDGE-WINDOW-EMPTY)"]),
    ("DEC-UNKNOWN-MASTER", [("Address_Decode", 14, "Master", "ghost"), ("Address_Decode", 14, "Miss response", "DECERR")],
     ["Address_Decode row 14: ghost is not a connected master or bridge (DEC-UNKNOWN-MASTER)"]),
    ("DEC-DUPLICATE", [("Address_Decode", 14, "Master", "dma0"), ("Address_Decode", 14, "Miss response", "DECERR")],
     ["Address_Decode row 14: dma0 already has a row (6) (DEC-DUPLICATE)"]),
    ("DEC-MISSING-ROW", [("Address_Decode", 6, "Master", None), ("Address_Decode", 6, "Miss response", None)],
     ["Address_Decode: master port dma0 on main_xbar has no row (DEC-MISSING-ROW)"]),
    # br_main2cfg pass-through N: cfg_bus decodes its offsets, but sec0 sends system addresses.
    ("DEC-MIXED-TRANSLATION", [("Bridges", 8, "Pass-through address", "N")],
     ["Fabrics row 6: the masters of cfg_bus see different addresses (br_main2cfg: offsets from 0x4100_0000; sec0: system addresses), but one decoder serves them all: give them separate fabrics, or set Pass-through address to Y on the bridges (DEC-MIXED-TRANSLATION)"]),
    # br_ahb2main pass-through N: main_xbar mixes it with direct masters, and br_main2cfg,
    # pass-through Y behind it, carries its offset on into cfg_bus beside sec0.
    ("DEC-MIXED-TRANSLATION", [("Bridges", 7, "Pass-through address", "N")],
     ["Fabrics row 5: the masters of main_xbar see different addresses (cpu0: system addresses; dma0: system addresses; dbg0: system addresses; br_ahb2main: offsets from 0x1000_0000), but one decoder serves them all: give them separate fabrics, or set Pass-through address to Y on the bridges (DEC-MIXED-TRANSLATION)",
      "Fabrics row 6: the masters of cfg_bus see different addresses (br_main2cfg: offsets from 0x1000_0000; sec0: system addresses), but one decoder serves them all: give them separate fabrics, or set Pass-through address to Y on the bridges (DEC-MIXED-TRANSLATION)"]),
    ("DEC-MISS-RESP", [("Address_Decode", 6, "Miss response", "PSLVERR")],
     ["Address_Decode row 6: PSLVERR does not suit AXI4 fabric main_xbar (DECERR, SLVERR, default-slave) (DEC-MISS-RESP)"]),
    ("DEC-DEFAULT-SLAVE", [("Address_Decode", 6, "Miss response", "default-slave")],
     ["Address_Decode row 6: Miss response default-slave needs a Default slave (DEC-DEFAULT-SLAVE)"]),
    ("DEC-ALIAS-SIZE", [("Address_Decode", 5, "Alias size (hex)", "0x0003_0000")],
     ["Address_Decode row 5: alias size 0x0003_0000 must be a power of 2 with base and target aligned to it (DEC-ALIAS-SIZE)"]),
    ("DEC-ALIAS-TARGET", [("Address_Decode", 5, "Alias maps to (hex)", "0x2000_0000")],
     ["Address_Decode row 5: alias target 0x2000_0000..0x2003_FFFF is not inside one region on main_xbar (DEC-ALIAS-TARGET)"]),
    ("DEC-ALIAS-OVERLAP", [("Address_Decode", 5, "Alias base (hex)", "0x8000_0000")],
     ["Address_Decode row 5: alias 0x8000_0000..0x8003_FFFF overlaps ddr0 mem on main_xbar (DEC-ALIAS-OVERLAP)"]),
    ("DEC-ALIAS-ENABLE", [("Address_Decode", 5, "Alias enable", "remap_i")],
     ["Address_Decode row 5: Alias enable 'remap_i' is neither 'always' nor a Wrapper > Extra top-level input (DEC-ALIAS-ENABLE)"]),
    ("SEC-PROT-TIED", _clear("Access_Rules", 8, ("Master", "Slave / bridge", "Access", "Notes")),
     ["Memory_Map row 9: secure-only pmu0 regs is reachable from dma0, whose PROT is tied (SEC-PROT-TIED)",
      "Memory_Map row 10: secure-only clkrst0 regs is reachable from dma0, whose PROT is tied (SEC-PROT-TIED)"]),
    ("PORTOPT-UNKNOWN-PORT", [("Port_Options", 18, "Port", "ghost")],
     ["Port_Options row 18: ghost is not a connected master, slave or bridge (PORTOPT-UNKNOWN-PORT)"]),
    ("PORTOPT-DUPLICATE", [("Port_Options", 18, "Port", "cpu0")],
     ["Port_Options row 18: cpu0 is already listed on row 5 (PORTOPT-DUPLICATE)"]),
    ("PORTOPT-SIGNAL", [("Port_Options", 10, "QOS", "drive")],
     ["Port_Options row 10: QOS does not exist on APB4; use n/a (PORTOPT-SIGNAL)"]),
    ("HIER-UNKNOWN-PARENT", [("Hierarchy", 7, "Parent", "nowhere")],
     ["Hierarchy row 7: parent nowhere is not a sub-wrapper (HIER-UNKNOWN-PARENT)"]),
    ("HIER-SELF-PARENT", [("Hierarchy", 7, "Parent", "per_wrapper")],
     ["Hierarchy row 7: per_wrapper cannot be its own parent (HIER-SELF-PARENT)"]),
    ("HIER-PARENT-LOOP", [("Hierarchy", 6, "Parent", "per_wrapper")],
     ["Hierarchy row 6: sub-wrappers form a parent loop: main_wrapper -> per_wrapper -> main_wrapper (HIER-PARENT-LOOP)"]),
    ("HIER-TOP", [("Hierarchy", 7, "Parent", "-")],
     ["Hierarchy row 7: only the top wrapper may have Parent -; give per_wrapper a parent (HIER-TOP)"]),
    ("HIER-UNKNOWN-INSTANCE", [("Hierarchy", 26, "Instance", "cpu0_dw_2_ghost")],
     ["Hierarchy row 26: cpu0_dw_2_ghost is not a fabric, bridge or adapter instance (HIER-UNKNOWN-INSTANCE)"]),
    ("HIER-UNKNOWN-SUBWRAPPER", [("Hierarchy", 16, "Sub-wrapper", "nowhere")],
     ["Hierarchy row 16: nowhere is not defined in the Sub-wrapper table (HIER-UNKNOWN-SUBWRAPPER)"]),
    ("PORTOPT-ERROR-MASKED", [("Port_Options", 18, h, v) for h, v in (
        ("Port", "br_ahb2aon"), ("FIFO depth", 0), ("Register slice", "none"), ("PROT", "drive"),
        ("QOS", "n/a"), ("REGION", "n/a"), ("CACHE", "n/a"), ("LOCK / excl", "n/a"),
        ("PSTRB", "drive"), ("PSLVERR", "tie-0"))],
     ["Port_Options row 18: PSLVERR is tied on br_ahb2aon: errors from aon_apb, its miss response included, never reach it (PORTOPT-ERROR-MASKED)"]),
    ("HIER-PLACED-TWICE", [("Hierarchy", 27, "Instance", "main_xbar"), ("Hierarchy", 27, "Sub-wrapper", "aon_wrapper")],
     ["Hierarchy row 27: main_xbar is already placed on row 16 (HIER-PLACED-TWICE)"]),
    ("HIER-RTL-NAME", [("Hierarchy", 16, "RTL instance name", "u_cfg_bus")],
     ["Hierarchy row 6: instance name u_cfg_bus in main_wrapper is used by fabric cfg_bus and fabric main_xbar (HIER-RTL-NAME)"]),
    ("HIER-RTL-NAME-child", [("Hierarchy", 16, "RTL instance name", "u_per_wrapper")],
     ["Hierarchy row 6: instance name u_per_wrapper in main_wrapper is used by fabric main_xbar and sub-wrapper per_wrapper (HIER-RTL-NAME)"]),
    ("HIER-RESERVED-NAME", [("Hierarchy", 7, "Sub-wrapper", "bag_sync"), ("Hierarchy", 20, "Sub-wrapper", "bag_sync")],
     ["Hierarchy row 7: sub-wrapper bag_sync has the name of an in-house module; rename the sub-wrapper (HIER-RESERVED-NAME)"]),
    ("HIER-RESERVED-NAME-pulp", [("Hierarchy", 7, "Sub-wrapper", "axi_mux"), ("Hierarchy", 20, "Sub-wrapper", "axi_mux")],
     ["Hierarchy row 7: sub-wrapper axi_mux has the name of a vendored PULP module; rename the sub-wrapper (HIER-RESERVED-NAME)"]),
    ("HIER-RESERVED-NAME-derived", [("Hierarchy", 7, "Sub-wrapper", "bag_axi_mux"), ("Hierarchy", 20, "Sub-wrapper", "bag_axi_mux")],
     ["Hierarchy row 7: sub-wrapper bag_axi_mux has the name of a module BAG derives from PULP; rename the sub-wrapper (HIER-RESERVED-NAME)"]),
    ("PROJ-PREFIX-COLLISION-sub-wrapper-and-derived", [("Project", 18, "Value", "p"), ("Hierarchy", 7, "Sub-wrapper", "bag_axi_mux"), ("Hierarchy", 20, "Sub-wrapper", "bag_axi_mux")],
     ["Project row 18: with Module prefix 'p', sub-wrapper bag_axi_mux and module BAG derives from PULP bag_axi_mux are both generated as p_bag_axi_mux; rename the sub-wrapper (PROJ-PREFIX-COLLISION)"]),
    ("PROJ-PREFIX-COLLISION-sub-wrapper-and-pulp", [("Project", 18, "Value", "p"), ("Hierarchy", 7, "Sub-wrapper", "axi_mux"), ("Hierarchy", 20, "Sub-wrapper", "axi_mux")],
     ["Project row 18: with Module prefix 'p', sub-wrapper axi_mux and vendored PULP module axi_mux are both generated as p_axi_mux; rename the sub-wrapper (PROJ-PREFIX-COLLISION)"]),
    ("HIER-RESERVED-NAME-formal", [("Hierarchy", 7, "Sub-wrapper", "faxil_slave"), ("Hierarchy", 20, "Sub-wrapper", "faxil_slave")],
     ["Hierarchy row 7: sub-wrapper faxil_slave has the name of a vendored ZipCPU module; rename the sub-wrapper (HIER-RESERVED-NAME)"]),
    ("HIER-RESERVED-NAME-header-package", [("Hierarchy", 7, "Sub-wrapper", "assert_rpt_pkg"), ("Hierarchy", 20, "Sub-wrapper", "assert_rpt_pkg")],
     ["Hierarchy row 7: sub-wrapper assert_rpt_pkg has the name of a vendored PULP package; rename the sub-wrapper (HIER-RESERVED-NAME)"]),
    ("HIER-EMPTY", [("Hierarchy", 9, "Sub-wrapper", "spare_wrapper"), ("Hierarchy", 9, "Parent", "demo_ss_bus_top")],
     ["Hierarchy row 9: sub-wrapper spare_wrapper has no instances (HIER-EMPTY)"]),
    ("WRAP-PORT-NOT-LISTED", _clear("Wrapper", 31, ("Port", "Exposed at top", "Top-level prefix")),
     ["Wrapper: master sec0 is not listed under Exposed ports (WRAP-PORT-NOT-LISTED)"]),
    ("WRAP-UNKNOWN-PORT", [("Wrapper", 38, "Port", "ghost"), ("Wrapper", 38, "Exposed at top", "Y")],
     ["Wrapper row 38: ghost is not a master, slave or bridge (WRAP-UNKNOWN-PORT)"]),
    ("WRAP-DUPLICATE-PORT", [("Wrapper", 38, "Port", "cpu0"), ("Wrapper", 38, "Exposed at top", "Y")],
     ["Wrapper row 38: cpu0 is already listed on row 18 (WRAP-DUPLICATE-PORT)"]),
    ("WRAP-INTERNAL-PORT", [("Wrapper", 34, "Exposed at top", "Y")],
     ["Wrapper row 34: bridge br_main2per is always internal; set N (WRAP-INTERNAL-PORT)"]),
    ("WRAP-NAME-COLLISION", [("Wrapper", 11, "Value", "boot_remap_i, cpu0_axi_awaddr")],
     ["Wrapper row 18: top-level port cpu0_axi_awaddr of cpu0 collides with Wrapper > Extra top-level inputs (WRAP-NAME-COLLISION)"]),
    ("USERPORT-INSTANCE", _user(5, "main_xbar", "irq_o", "output", 1, "open"),
     ["User_Ports row 5: main_xbar is not a fabric with a user-supplied template (bag_external) (USERPORT-INSTANCE)"]),
    ("USERPORT-DUPLICATE", [*EXT, *_user(5, "aon_apb", "irq_o", "output", 1, "open"),
                            *_user(6, "aon_apb", "irq_o", "output", 1, "expose")],
     ["User_Ports row 6: aon_apb.irq_o is already declared on row 5 (USERPORT-DUPLICATE)"]),
    ("USERPORT-NAME", [*EXT, *_user(5, "aon_apb", "clk_i", "input", 1, "clock D_AON")],
     ["User_Ports row 5: clk_i is a bus or clock port BAG already connects on aon_apb (USERPORT-NAME)"]),
    ("USERPORT-CONNECTION", [*EXT, *_user(5, "aon_apb", "cfg_i", "input", 4, "open"),
                             *_user(6, "aon_apb", "irq_o", "output", 1, "tie-0"),
                             *_user(7, "aon_apb", "test_i", "input", 1, "extra input nope_i"),
                             *_user(8, "aon_apb", "qos_i", "input", 2, "0x7"),
                             *_user(9, "aon_apb", "bad_i", "input", 1, "wire it")],
     ["User_Ports row 5: open leaves the port unconnected, so cfg_i must be an output (tie an unused input) (USERPORT-CONNECTION)",
      "User_Ports row 6: tie-0 drives the port, so irq_o must be an input (USERPORT-CONNECTION)",
      "User_Ports row 7: nope_i is not on Wrapper > Extra top-level inputs (USERPORT-CONNECTION)",
      "User_Ports row 8: 0x7 does not fit in 2 bits (USERPORT-CONNECTION)",
      "User_Ports row 9: 'wire it' is not expose, open, tie-0, tie-1, 0x<hex>, extra input <name>, clock <domain> or reset <domain> (USERPORT-CONNECTION)"]),
    ("WRAP-NAME-COLLISION-user", [*EXT, *_user(5, "aon_apb", "awaddr", "output", 1, "expose"),
                                  ("Wrapper", 11, "Value", "boot_remap_i, aon_apb_awaddr")],
     ["User_Ports row 5: top-level port aon_apb_awaddr of User_Ports aon_apb.awaddr collides with Wrapper > Extra top-level inputs (WRAP-NAME-COLLISION)"]),
    ("WRAP-NAME-COLLISION-boundary", [("Wrapper", 11, "Value", "boot_remap_i, br_main2per_2_periph_apb_apb4_paddr")],
     ["Hierarchy row 7: boundary port br_main2per_2_periph_apb_apb4_paddr of sub-wrapper per_wrapper is claimed by extra input br_main2per_2_periph_apb_apb4_paddr and link br_main2per_2_periph_apb (WRAP-NAME-COLLISION)"]),
]  # fmt: skip


def test_golden_has_only_its_wrap_and_pstrb_warnings() -> None:
    path = GOLDEN_DIR / "demo_ss.xlsx"
    messages = [f.message for f in run_checks(read_workbook(path), GOLDEN_DIR).findings]
    assert messages[-2:] == SAMPLE_PSTRB
    assert unexpected(messages[:-2], SAMPLE_WRAP) == []
    assert messages[1] == (
        "Masters row 6: WRAP bursts from dma0 to ddr0 get SLVERR: dw adapter main_xbar_dw_2_ddr0 "
        "cannot convert them. Send INCR bursts on this path, or keep it at one width and "
        "protocol (AXI-WRAP-REFUSED)"
    )


def test_blank_template_reports_missing_settings(tmp_path: Path) -> None:
    path = tmp_path / "blank.xlsx"
    build_workbook(sample=False).save(path)
    messages = [f.message for f in run_checks(read_workbook(path), tmp_path).findings]
    assert "Project row 5: Project name is required (VALUE-MISSING)" in messages
    assert "Wrapper row 5: Top module name is required (VALUE-MISSING)" in messages


@pytest.mark.parametrize(("case", "edits", "expected"), CASES, ids=[c[0] for c in CASES])
def test_broken_workbook(
    tmp_path: Path, case: str, edits: list[Edit] | SpecData, expected: list[str]
) -> None:
    assert _messages(tmp_path, edits) == expected


@pytest.mark.parametrize(
    "edits",
    [
        # n/a means blank, as on the other columns that may not apply.
        [("Fabrics", 5, "External module", "n/a")],
        [*EXT, ("Fabrics", 9, "External module", "n/a")],
        # With position names, the name a blank External module would reserve is free.
        [
            *EXT,
            ("Fabrics", 9, "External module", "mine"),
            *_user(5, "aon_apb", "aon_apb_br_ahb2aon_apb4_psel", "input", 1, "tie-0"),
        ],
    ],
    ids=["na-not-external", "na-external", "old-name-free"],
)
def test_legal_external_modules(tmp_path: Path, edits: list[Edit]) -> None:
    assert _messages(tmp_path, edits) == []


@pytest.mark.parametrize(
    "edits",
    [
        [("Masters", 5, "User width (bits)", "n/a")],
        [("Slaves", 5, "User width (bits)", "n/a")],
        [("Slaves", 7, "User width (bits)", "n/a")],
    ],
    ids=["axi4-master", "axi4-slave", "apb4-slave"],
)
def test_a_legal_token_passes_every_reader_of_its_cell(tmp_path: Path, edits: list[Edit]) -> None:
    """n/a User width is accepted by the width check and read again by the wrapper check and by
    resolve. Every reader of a cell must allow the same tokens: the parse cache no longer hides
    a narrower reader behind whichever one ran first (#149). The sample already covers the other
    two tokens, auto ID width and variable Wait states, on ddr0."""
    assert _messages(tmp_path, edits) == []


@pytest.mark.parametrize("name", ["foo", "Foo-bar"], ids=["legal", "illegal"])
def test_a_repeated_sub_wrapper_name_is_not_a_prefix_collision_with_itself(
    tmp_path: Path, name: str
) -> None:
    """NAME-UNIQUE (or NAME-IDENT) owns a repeated name; the prefix check must not add a
    sub-wrapper-against-itself error on top, whether or not the name is a legal identifier."""
    edits: list[Edit] = [
        ("Project", 18, "Value", "p"),
        ("Hierarchy", 8, "Sub-wrapper", name),
        ("Hierarchy", 9, "Sub-wrapper", name),
    ]
    messages = _messages(tmp_path, edits)
    assert not [m for m in messages if "PROJ-PREFIX-COLLISION" in m], messages
    assert any("NAME-UNIQUE" in m or "NAME-IDENT" in m for m in messages)


def test_a_sub_wrapper_named_like_a_pulp_unit_is_refused_for_the_prefixed_clash(
    tmp_path: Path,
) -> None:
    """Sub-wrapper axi_mux under prefix p is emitted as p_axi_mux, and so is PULP's axi_mux, now
    that the prefix reaches the vendored units. One message, not two: the `fixed` arm does not
    also fire, because p_axi_mux is not a name anything keeps.
    """
    edits: list[Edit] = [
        ("Project", 18, "Value", "p"),
        ("Hierarchy", 7, "Sub-wrapper", "axi_mux"),
        ("Hierarchy", 20, "Sub-wrapper", "axi_mux"),
    ]
    assert _messages(tmp_path, edits) == [
        "Project row 18: with Module prefix 'p', sub-wrapper axi_mux and vendored PULP module "
        "axi_mux are both generated as p_axi_mux; rename the sub-wrapper (PROJ-PREFIX-COLLISION)"
    ]


def test_a_sub_wrapper_that_equals_the_prefix_joined_to_a_pulp_unit_is_legal(
    tmp_path: Path,
) -> None:
    """Sub-wrapper cut under prefix axi is axi_cut, and PULP's axi_cut is axi_axi_cut: the
    prefix reaches PULP's unit too, so nothing clashes and `bag check` stays silent."""
    edits: list[Edit] = [
        ("Project", 18, "Value", "axi"),
        ("Hierarchy", 7, "Sub-wrapper", "cut"),
        ("Hierarchy", 20, "Sub-wrapper", "cut"),
    ]
    assert _messages(tmp_path, edits) == []


def test_a_normal_module_prefix_is_legal(tmp_path: Path) -> None:
    assert _messages(tmp_path, [("Project", 18, "Value", "demo_ss")]) == []


def test_every_rule_has_a_broken_workbook() -> None:
    covered = {msg.rsplit("(", 1)[1].rstrip(")") for _, _, exp in CASES for msg in exp}
    assert set(RULES) - covered == set()
    assert len(RULES) >= 40


def test_the_readme_states_the_number_of_checks() -> None:
    """The README's status table says how many checks there are; it rotted once, so it is pinned."""
    readme = Path(__file__).resolve().parents[2] / "README.md"
    found = re.search(r"(\d+) validation checks", readme.read_text(encoding="utf-8"))
    assert found, f"{readme} no longer says 'N validation checks'; restore the phrase"
    assert int(found.group(1)) == len(RULES), (
        f"README.md says {found.group(1)} validation checks but RULES has {len(RULES)}: "
        f"edit the number in {readme}"
    )


def test_findings_are_sorted_and_deterministic(tmp_path: Path) -> None:
    edits: list[Edit] = [("Topology", 6, "Port index", 0), ("Clocks", 5, "Freq (MHz)", None)]
    first = _messages(tmp_path, edits)
    assert first == _messages(tmp_path, edits)
    assert first[0].startswith("Clocks row 5")


def _unreachable(messages: list[str]) -> list[str]:
    return [m for m in messages if "(ACC-UNREACHABLE-SLAVE)" in m]


def test_a_slave_that_rules_and_region_access_shut_together_is_unreachable(tmp_path: Path) -> None:
    """D105 (seed 012): ddr0's only region is write-only and every master may only read it, so
    neither a read nor a write gets in, though no rule denies it outright."""
    edits = [
        ("Memory_Map", 7, "Access", "WO"),
        *_row("Access_Rules", 9, Master="cpu0", Access="RO"), ("Access_Rules", 9, "Slave / bridge", "ddr0"),
        *_row("Access_Rules", 10, Master="dma0", Access="RO"), ("Access_Rules", 10, "Slave / bridge", "ddr0"),
    ]  # fmt: skip
    assert _unreachable(_messages(tmp_path, edits)) == [
        "Topology row 9: no master can reach ddr0: no read or write gets in. Check the "
        "Access_Rules and region access (RO, WO, secure only) on the way to it, the bridges, and "
        "the masters' address widths (ACC-UNREACHABLE-SLAVE)"
    ]


def test_the_slaves_behind_a_bridge_no_master_may_use_are_unreachable(tmp_path: Path) -> None:
    """D105 (seed 036): every master on main_xbar is denied br_main2per, so nothing reaches the
    slaves behind it either."""
    edits = [
        *_row("Access_Rules", 9, Master="cpu0", Access="-"), ("Access_Rules", 9, "Slave / bridge", "br_main2per"),
        *_row("Access_Rules", 10, Master="dbg0", Access="-"), ("Access_Rules", 10, "Slave / bridge", "br_main2per"),
        *_row("Access_Rules", 11, Master="br_ahb2main", Access="-"), ("Access_Rules", 11, "Slave / bridge", "br_main2per"),
    ]  # fmt: skip
    found = _unreachable(_messages(tmp_path, edits))
    assert any("no master on main_xbar can reach br_main2per" in m for m in found)
    behind = sorted(m.split("no master can reach ")[1].split(":")[0] for m in found
                    if "no master can reach " in m)  # fmt: skip
    assert behind == ["gpio0", "spi0", "timer0", "uart0"]


def test_a_pstrb_tied_to_0_stores_nothing(tmp_path: Path) -> None:
    """Review: with PSTRB tied to 0 no write stores any byte, whatever the width."""
    found = [m for m in _messages(tmp_path, [("Port_Options", 10, "PSTRB", "tie-0")])
             if "(PORTOPT-PSTRB-WORD)" in m]  # fmt: skip
    assert found == [
        "Port_Options row 10: PSTRB is tied to 0 on uart0: no write stores any byte. Drive "
        "PSTRB, or tie it to the default (PORTOPT-PSTRB-WORD)"
    ]


def test_a_cache_tied_to_the_default_does_not_warn(tmp_path: Path) -> None:
    edits: list[Edit] = [("Port_Options", 5, "CACHE", "tie-default")]
    assert not [m for m in _messages(tmp_path, edits) if "(AXI-SPLIT-REFUSED)" in m]
