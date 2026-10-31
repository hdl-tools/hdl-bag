# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Input data of the demo_ss sample workbook (schema 0.3).

Rows hold input-column values only, in input-column order; derived columns are
filled with formulas by the builder.
"""

from typing import Final

Cell = str | int | None

PROJECT: Final[dict[str, str]] = {
    "Project name": "demo_ss",
    "Spec revision": "r4",
    "Author": "Chris",
    "Output directory": "out/demo_ss",
    "Generate RTL": "Y",
    "Generate C header": "Y",
    "Generate memory-map doc": "Y",
    "Generate cocotb unit tests": "Y",
    "Generate formal (SymbiYosys)": "Y",
}

CLOCKS: Final[list[list[Cell]]] = [
    ["clk_cpu", 1000, "top port (CPU PLL)", "D_CPU", "rst_cpu_n", "CPU cluster interface clock"],
    [
        "clk_sys",
        500,
        "top port (SYS PLL)",
        "D_SYS",
        "rst_sys_n",
        "Main crossbar, DDR/SRAM interfaces",
    ],
    ["clk_per", 100, "top port (divider)", "D_PER", "rst_per_n", "APB peripherals"],
    ["clk_aon", 50, "top port (AON osc)", "D_AON", "rst_aon_n", "Always-on AHB-Lite segment"],
]

RESETS: Final[list[list[Cell]]] = [
    ["rst_cpu_n", "active-low", "async", "sync", "D_CPU", "top port", ""],
    ["rst_sys_n", "active-low", "async", "sync", "D_SYS", "top port", ""],
    ["rst_per_n", "active-low", "async", "sync", "D_PER", "top port", ""],
    ["rst_aon_n", "active-low", "async", "sync", "D_AON", "top port (POR)", "Power-on reset only"],
]

# Fabric .. Mid-point register slice, Notes. Settings that do not apply read n/a (D16).
FABRICS: Final[list[list[Cell]]] = [
    ["main_xbar", "AXI4", "crossbar", 64, 32, "D_SYS", "round-robin", "CUT_ALL_AX", 8, 8,
     "bag_axi_xbar", "PULP axi_xbar", "n/a", "n/a", "Main system interconnect"],
    ["cfg_bus", "AXI4-Lite", "shared-bus", 32, 32, "D_SYS", "fixed-priority", "n/a", 4, 4,
     "bag_axil_shared_bus", "PULP axi_lite_mux + axi_lite_demux", 2, "cut",
     "Low-bandwidth config bus: area over bandwidth"],
    ["periph_apb", "APB4", "decoder-mux", 32, 32, "D_PER", "single-master", "n/a", "n/a", "n/a",
     "bag_apb_mux", "in-house template", "n/a", "n/a", "Fed by br_main2per"],
    ["ahb_bus", "AHB-Lite", "single-layer", 32, 32, "D_AON", "single-master", "n/a", "n/a", "n/a",
     "bag_ahb_lite_bus", "in-house template", "n/a", "n/a", "Always-on segment"],
    ["aon_apb", "APB4", "decoder-mux", 32, 32, "D_AON", "single-master", "n/a", "n/a", "n/a",
     "bag_apb_mux", "in-house template", "n/a", "n/a", "Fed by br_ahb2aon"],
]  # fmt: skip

# Master, Protocol, Data width, Addr width, ID width, User width, Clock domain,
# Max outstanding rd, Max outstanding wr, Description, Notes
MASTERS: Final[list[list[Cell]]] = [
    ["cpu0", "AXI4", 128, 32, 6, 0, "D_CPU", 16, 16, "CPU cluster master port", ""],
    ["dma0", "AXI4", 64, 32, 4, 0, "D_SYS", 8, 8, "System DMA", ""],
    ["dbg0", "AXI4-Lite", 32, 32, None, 0, "D_SYS", 1, 1, "Debug access port", ""],
    ["mcu0", "AHB-Lite", 32, 32, None, 0, "D_AON", 1, 1, "Always-on microcontroller", ""],
    ["sec0", "AXI4-Lite", 32, 32, None, 0, "D_SYS", 1, 1, "Security engine (config writes)", ""],
]

# Slave, Protocol, Data width, Addr width, ID width, User width, Clock domain,
# Error response, Wait states, Description, Notes
SLAVES: Final[list[list[Cell]]] = [
    ["ddr0", "AXI4", 128, 32, "auto", 0, "D_SYS", "SLVERR", "variable", "DDR controller", ""],
    ["sram0", "AXI4", 64, 32, "auto", 0, "D_SYS", "SLVERR", 1, "On-chip SRAM 256 KB", ""],
    ["uart0", "APB4", 32, 12, None, None, "D_PER", "PSLVERR", 0, "UART", ""],
    ["gpio0", "APB4", 32, 12, None, None, "D_PER", "PSLVERR", 0, "GPIO", ""],
    ["timer0", "APB4", 32, 12, None, None, "D_PER", "PSLVERR", 0, "General timer", ""],
    ["spi0", "APB4", 32, 12, None, None, "D_PER", "PSLVERR", 0, "SPI master", ""],
    ["itcm0", "AHB-Lite", 32, 16, None, None, "D_AON", "HRESP_ERROR", 0, "MCU TCM 64 KB", ""],
    ["wdt0", "APB4", 32, 12, None, None, "D_AON", "PSLVERR", 0, "Watchdog", ""],
    ["rtc0", "APB4", 32, 12, None, None, "D_AON", "PSLVERR", 0, "Real-time clock", ""],
    ["pmu0", "AXI4-Lite", 32, 12, None, 0, "D_SYS", "SLVERR", 0, "Power management registers", ""],
    ["clkrst0", "AXI4-Lite", 32, 12, None, 0, "D_SYS", "SLVERR", 0,
     "Clock/reset control registers", ""],
]  # fmt: skip

# Bridge, Template, Window base, Pass-through address, FIFO depth, Notes
BRIDGES: Final[list[list[Cell]]] = [
    ["br_main2per", "bag_axi2apb", "0x4000_0000", "Y", 2,
     "AXI4 -> AXI4-Lite -> APB4 (PULP axi_to_axi_lite + axi_lite_to_apb)"],
    ["br_ahb2aon", "bag_ahb2apb", "0x5000_0000", "Y", 0, "In-house AHB-Lite -> APB4 bridge"],
    ["br_ahb2main", "bag_ahb2axi", "0x1000_0000", "Y", 2,
     "In-house AHB-Lite -> AXI4 bridge; gives mcu0 access to sram0 and peripherals. "
     "AXI side ID width 1"],
    ["br_main2cfg", "bag_axi2axil", "0x4100_0000", "Y", 0,
     "AXI4 64-bit -> AXI4-Lite 32-bit (PULP axi_to_axi_lite + width converter)"],
]  # fmt: skip

# Source, Destination, Port index, Priority (0 = highest), Notes
TOPOLOGY: Final[list[list[Cell]]] = [
    ["cpu0", "main_xbar", 0, None, ""], ["dma0", "main_xbar", 1, None, ""],
    ["dbg0", "main_xbar", 2, None, ""],
    ["br_ahb2main", "main_xbar", 3, None, "Bridge from the AON segment"],
    ["main_xbar", "ddr0", 0, None, ""], ["main_xbar", "sram0", 1, None, ""],
    ["main_xbar", "br_main2per", 2, None, ""], ["main_xbar", "br_main2cfg", 3, None, ""],
    ["br_main2cfg", "cfg_bus", 0, 1, "CPU/DMA/debug path into the config bus"],
    ["sec0", "cfg_bus", 1, 0, "Security engine wins arbitration on cfg_bus"],
    ["cfg_bus", "pmu0", 0, None, ""], ["cfg_bus", "clkrst0", 1, None, ""],
    ["br_main2per", "periph_apb", 0, None, ""],
    ["periph_apb", "uart0", 0, None, ""], ["periph_apb", "gpio0", 1, None, ""],
    ["periph_apb", "timer0", 2, None, ""], ["periph_apb", "spi0", 3, None, ""],
    ["mcu0", "ahb_bus", 0, None, ""], ["ahb_bus", "itcm0", 0, None, ""],
    ["ahb_bus", "br_ahb2aon", 1, None, ""], ["ahb_bus", "br_ahb2main", 2, None, ""],
    ["br_ahb2aon", "aon_apb", 0, None, ""], ["aon_apb", "wdt0", 0, None, ""],
    ["aon_apb", "rtc0", 1, None, ""],
]  # fmt: skip

# Master, Slave / bridge, Access, Notes
ACCESS_RULES: Final[list[list[Cell]]] = [
    ["dma0", "br_main2per", "-", "DMA has no peripheral access"],
    ["dbg0", "ddr0", "RO", "Debug may read DDR but not write it"],
    ["br_ahb2main", "ddr0", "-", "MCU has no DDR path"],
    ["dma0", "br_main2cfg", "-", "DMA has no config-bus access"],  # schema 0.3
]

# Slave / bridge, Region, Base, Size, Access, Secure only, Notes
MEMORY_MAP: Final[list[list[Cell]]] = [
    ["sram0", "mem", "0x1000_0000", "0x0004_0000", "RW", "N", "256 KB"],
    ["br_main2per", "periph", "0x4000_0000", "0x0010_0000", "RW", "N", "1 MB window to periph_apb"],
    ["ddr0", "mem", "0x8000_0000", "0x4000_0000", "RW", "N", "1 GB"],
    ["br_main2cfg", "cfg", "0x4100_0000", "0x0010_0000", "RW", "N", "1 MB window to cfg_bus"],
    ["pmu0", "regs", "0x4100_0000", "0x0000_1000", "RW", "Y", "Secure only"],
    ["clkrst0", "regs", "0x4100_1000", "0x0000_1000", "RW", "Y", "Secure only"],
    ["uart0", "regs", "0x4000_0000", "0x0000_1000", "RW", "N", ""],
    ["gpio0", "regs", "0x4000_1000", "0x0000_1000", "RW", "N", ""],
    ["timer0", "regs", "0x4000_2000", "0x0000_1000", "RW", "N", ""],
    ["spi0", "regs", "0x4000_3000", "0x0000_1000", "RW", "N", ""],
    ["itcm0", "mem", "0x0000_0000", "0x0001_0000", "RW", "N", "64 KB, MCU view only"],
    ["br_ahb2aon", "aon", "0x5000_0000", "0x0001_0000", "RW", "N", "64 KB window to aon_apb"],
    ["br_ahb2main", "sram", "0x1000_0000", "0x0004_0000", "RW", "N",
     "Window to sram0 via main_xbar"],
    ["br_ahb2main", "periph", "0x4000_0000", "0x0010_0000", "RW", "N",
     "Window to peripherals via main_xbar"],
    ["wdt0", "regs", "0x5000_0000", "0x0000_1000", "RW", "Y", "Secure only"],
    ["rtc0", "regs", "0x5000_1000", "0x0000_1000", "RW", "N", ""],
]  # fmt: skip

# Master, Miss response, Default slave, Alias base, Alias size, Alias maps to, Alias enable, Notes
ADDRESS_DECODE: Final[list[list[Cell]]] = [
    ["cpu0", "DECERR", None, "0x0000_0000", "0x0004_0000", "0x1000_0000", "boot_remap_i",
     "Boot alias of sram0 at 0x0 while boot_remap_i = 1"],
    ["dma0", "DECERR", None, None, None, None, None, ""],
    ["dbg0", "DECERR", None, None, None, None, None, ""],
    ["br_ahb2main", "DECERR", None, None, None, None, None, "Returned to mcu0 as HRESP ERROR"],
    ["br_main2per", "PSLVERR", None, None, None, None, None, "Returned to AXI side as SLVERR"],
    ["mcu0", "HRESP_ERROR", None, None, None, None, None, "Default slave inside ahb_bus"],
    ["br_ahb2aon", "PSLVERR", None, None, None, None, None, ""],
    ["br_main2cfg", "DECERR", None, None, None, None, None, ""],
    ["sec0", "DECERR", None, None, None, None, None, ""],
]  # fmt: skip

# Port, FIFO depth, Register slice, PROT, QOS, REGION, CACHE, LOCK / excl, PSTRB, PSLVERR, Notes
PORT_OPTIONS: Final[list[list[Cell]]] = [
    ["cpu0", 4, "cut", "drive", "drive", "tie-0", "drive", "drive", "n/a", "n/a",
     "Extra depth absorbs CDC + downsizer stalls"],
    ["dma0", 2, "none", "tie-default", "drive", "tie-0", "tie-default", "tie-0", "n/a", "n/a", ""],
    ["dbg0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", ""],
    ["ddr0", 8, "multicut-2", "drive", "drive", "drive", "drive", "drive", "n/a", "n/a",
     "Long route towards DDR controller"],
    ["sram0", 0, "cut", "drive", "tie-0", "tie-0", "drive", "drive", "n/a", "n/a", ""],
    ["uart0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "drive", "drive", ""],
    ["gpio0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "drive", "drive", ""],
    ["timer0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "drive", "drive", ""],
    ["spi0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "drive", "drive", ""],
    ["mcu0", 0, "none", "drive", "n/a", "n/a", "n/a", "tie-0", "n/a", "n/a", ""],
    ["itcm0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", ""],
    ["wdt0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "tie-default", "drive",
     "PPROT drives the secure-only check"],
    ["rtc0", 0, "none", "drive", "n/a", "n/a", "n/a", "n/a", "tie-default", "drive", ""],
]  # fmt: skip

# Sub-wrapper, Parent, Notes (the top wrapper row is added by the builder)
SUB_WRAPPERS: Final[list[list[Cell]]] = [
    ["main_wrapper", "demo_ss_bus_top", "Main crossbar and its port adapters (D_SYS)"],
    ["per_wrapper", "main_wrapper", "Peripheral APB bus, nested inside main_wrapper (D_PER)"],
    ["aon_wrapper", "demo_ss_bus_top", "Always-on segment; maps to the AON power domain (D_AON)"],
]

# Instance, Sub-wrapper, RTL instance name, Notes
PLACEMENT: Final[list[list[Cell]]] = [
    ["main_xbar", "main_wrapper", "", ""],
    ["br_main2per", "main_wrapper", "", "AXI side in D_SYS; CDC to D_PER inside the bridge"],
    ["cfg_bus", "main_wrapper", "", "Shared-bus fabric"],
    ["br_main2cfg", "main_wrapper", "", ""],
    ["periph_apb", "per_wrapper", "", ""],
    ["ahb_bus", "aon_wrapper", "", ""],
    ["aon_apb", "aon_wrapper", "", ""],
    ["br_ahb2aon", "aon_wrapper", "", ""],
    ["br_ahb2main", "aon_wrapper", "",
     "Bridge stays in AON; its CDC output crosses into main_wrapper"],
    ["main_xbar_dw_2_ddr0", "main_wrapper", "u_ddr0_upsizer", "Listed only to rename the instance"],
    ["cpu0_cdc_2_main_xbar", "demo_ss_bus_top", "",
     "Override: keep CPU CDC at top level, next to the CPU boundary"],
]  # fmt: skip

WRAPPER: Final[dict[str, str]] = {
    "Top module name": "demo_ss_bus_top",
    "Parameter package": "demo_ss_bus_pkg",
    "File header": "header.txt",
    "Extra top-level inputs": "boot_remap_i",
}

# Port, Exposed at top, Top-level prefix, If not exposed, Notes
EXPOSED_PORTS: Final[list[list[Cell]]] = [
    ["cpu0", "Y", "cpu0_axi", "", ""], ["dma0", "Y", "dma0_axi", "", ""],
    ["dbg0", "Y", "dbg0_axil", "", ""], ["mcu0", "Y", "mcu0_ahb", "", ""],
    ["ddr0", "Y", "ddr0_axi", "", ""], ["sram0", "Y", "sram0_axi", "", ""],
    ["uart0", "Y", "uart0_apb", "", ""], ["gpio0", "Y", "gpio0_apb", "", ""],
    ["timer0", "Y", "timer0_apb", "", ""], ["spi0", "Y", "spi0_apb", "", ""],
    ["itcm0", "Y", "itcm0_ahb", "", ""], ["wdt0", "Y", "wdt0_apb", "", ""],
    ["rtc0", "Y", "rtc0_apb", "", ""], ["sec0", "Y", "sec0_axil", "", ""],
    ["pmu0", "Y", "pmu0_axil", "", ""], ["clkrst0", "Y", "clkrst0_axil", "", ""],
    ["br_main2per", "N", "", "internal", "Bridges are always internal"],
    ["br_ahb2aon", "N", "", "internal", ""],
    ["br_ahb2main", "N", "", "internal", ""],
    ["br_main2cfg", "N", "", "internal", ""],
]  # fmt: skip
