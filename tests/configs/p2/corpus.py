# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The Phase 2 gate corpus: 10 hand-written APB/AHB-only configs.

Each config is written at workbook level (the same inputs a user types) and becomes
<name>.ir.yaml through the real path: workbook -> `bag check` (zero findings) -> resolve.
`python tests/configs/p2/corpus.py` rewrites the IR files; tests/gen/test_corpus.py
keeps them in sync. The feature each config must cover follows the PR 3/PR 4 design
reviews; see COVERAGE.
"""

import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))  # tests/configs: the shared DSL

import dsl  # noqa: E402
from dsl import AHB_MASTER_NA, AHB_SLAVE_NA, APB3_NA, APB4_NA, Cfg  # noqa: E402


def apb4_min() -> Cfg:
    """Smallest legal design: one APB4 master and slave on a 12-bit fabric; default
    pattern with no prefixes; an APB-master alias on a pin; an unused extra input and an
    unused clock domain; an active-high reset with async assertion and deassertion."""
    c = Cfg("apb4_min", extra_inputs=("alias_i", "spare_i"))
    c.domain("D_A", "clk_a", "rst_a", polarity="active-high", deassertion="async")
    c.domain("D_UNUSED", "clk_u", "rst_u_n")
    c.fabric("apb", "APB4", 32, 12, "D_A")
    c.master("m0", "APB4", 32, 12, "D_A").slave("s0", "APB4", 32, 12, "D_A")
    c.link("m0", "apb", 0).link("apb", "s0", 0)
    c.region("s0", "regs", 0x000, 0x800)
    c.decode("m0", "PSLVERR", alias=(0x800, 0x100, 0x000, "alias_i"))
    c.option("m0", **APB4_NA).option("s0", **APB4_NA)
    return c


def apb3_wide() -> Cfg:
    """APB3 fabric, 8 slaves whose Port index, name and address orders all differ;
    misses go to a default slave at a non-zero index; PSLVERR tie-0 on two slaves;
    denied accesses (an RO region, Access_Rules WO / RO) also go to the default slave;
    mixed slave address widths; the {proto}_{port}_{signal} pattern."""
    c = Cfg("apb3_wide", signal_pattern="{proto}_{port}_{signal}")
    c.domain("D_P", "clk_p", "rst_p_n")
    c.fabric("apb", "APB3", 32, 32, "D_P")
    c.master("host", "APB3", 32, 32, "D_P").link("host", "apb", 0)
    # (name, Port index, address slot, slave address width)
    plan = [("p0", 5, 3, 12), ("p1", 2, 7, 12), ("p2", 7, 0, 12), ("p3", 0, 5, 16),
            ("p4", 3, 1, 12), ("p5", 6, 6, 32), ("p6", 1, 2, 12), ("p7", 4, 4, 13)]  # fmt: skip
    for name, index, slot, aw in plan:
        c.slave(name, "APB3", 32, aw, "D_P").link("apb", name, index)
        base = 0x4000_0000 + slot * 0x1_0000  # 64 KB slots: aligned for every width here
        c.region(name, "regs", base, 0x1000, access="RO" if name == "p1" else "RW")
        tie = {"pslverr": "tie-0"} if name in ("p2", "p6") else {}
        c.option(name, **APB3_NA, **tie)
    c.option("host", **APB3_NA)
    c.decode("host", "default-slave", default="p5")
    c.access("host", "p4", "WO").access("host", "p0", "RO")
    return c


def apb4_rules() -> Cfg:
    """Region access (RO, WO, secure-only, a slave with RW and RO regions) and
    Port_Options variety: master PSTRB tie-default (write-qualified strobes), slave
    PSTRB tie-0 and tie-default, PROT tie-0 / tie-default / drive, PSLVERR tie-0; a
    reset with sync assertion and sync deassertion."""
    c = Cfg("apb4_rules")
    c.domain("D_R", "clk_r", "rst_r_n", assertion="sync", deassertion="sync")
    c.fabric("apb", "APB4", 32, 32, "D_R")
    c.master("cpu", "APB4", 32, 32, "D_R").link("cpu", "apb", 0)
    for i, name in enumerate(("ro0", "wo0", "sec0", "dual0")):
        # dual0 is a narrow slave with two regions in one 2^16-aligned block (D29).
        c.slave(name, "APB4", 32, 16, "D_R").link("apb", name, i)
    c.region("ro0", "regs", 0x1000_0000, 0x1000, access="RO")
    c.region("wo0", "regs", 0x1001_0000, 0x1000, access="WO")
    c.region("sec0", "regs", 0x1002_0000, 0x1000, secure=True)
    c.region("dual0", "ctrl", 0x1003_0000, 0x1000)
    c.region("dual0", "status", 0x1003_1000, 0x1000, access="RO")
    c.decode("cpu", "PSLVERR")
    c.option("cpu", **APB4_NA, pstrb="tie-default")
    c.option("ro0", **APB4_NA, prot="tie-0", pslverr="tie-0", pstrb="tie-0")
    c.option("wo0", **APB4_NA, pstrb="tie-default")
    c.option("sec0", **APB4_NA)
    c.option("dual0", **APB4_NA, prot="tie-default")
    return c


def ahb_single() -> Cfg:
    """One AHB-Lite master, four slaves with 12/16/24/32-bit addresses (truncation, one
    base with bit 12 set); a slave with two regions; master HPROT tie-default and LOCK
    tie-0; one slave with PROT tie-default."""
    c = Cfg("ahb_single")
    c.domain("D_H", "clk_h", "rst_h_n")
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_H")
    c.master("mcu", "AHB-Lite", 32, 32, "D_H", prefix="mcu_ahb").link("mcu", "ahb", 0)
    for i, (name, aw, base, size) in enumerate((
        ("s12", 12, 0x1000_3000, 0x1000), ("s16", 16, 0x2000_0000, 0x1_0000),
        ("s24", 24, 0x3000_0000, 0x10_0000), ("s32", 32, 0x8000_0000, 0x1000),
    )):  # fmt: skip
        c.slave(name, "AHB-Lite", 32, aw, "D_H").link("ahb", name, i)
        c.region(name, "mem", base, size)
        c.option(name, **AHB_SLAVE_NA, **({"prot": "tie-default"} if name == "s16" else {}))
    c.region("s32", "mem2", 0x8001_0000, 0x1000)  # a second region on the same slave
    c.decode("mcu", "HRESP_ERROR")
    c.option("mcu", **AHB_MASTER_NA, prot="tie-default", lock="tie-0")
    return c


def ahb_rr() -> Cfg:
    """Three masters, round-robin, 3x4 (non-square) tables; master names in reverse
    Port-index order; a 24-bit master (zero-extension); asymmetric Access_Rules
    (none / RO / WO); LOCK driven on two masters, tied on one."""
    c = Cfg("ahb_rr")
    c.domain("D_H", "clk_h", "rst_h_n")
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_H", arb="round-robin")
    for index, (name, aw) in enumerate((("m_c", 32), ("m_b", 24), ("m_a", 32))):
        c.master(name, "AHB-Lite", 32, aw, "D_H").link(name, "ahb", index)
        c.decode(name, "HRESP_ERROR")
        c.option(name, **AHB_MASTER_NA, **({"lock": "tie-0"} if name == "m_b" else {}))
    for i, (name, base) in enumerate((("t0", 0x0000_0000), ("t1", 0x0010_0000),
                                      ("t2", 0x0020_0000), ("t3", 0x00F0_0000))):  # fmt: skip
        c.slave(name, "AHB-Lite", 32, 32, "D_H").link("ahb", name, i)
        c.region(name, "mem", base, 0x1000)
        c.option(name, **AHB_SLAVE_NA)
    c.access("m_a", "t1", "RO").access("m_b", "t2", "WO").access("m_c", "t3", "-")
    c.access("m_a", "t3", "WO")
    return c


def ahb_fixed() -> Cfg:
    """Three masters, fixed priority not equal to the Port index; aliases: always-on,
    pin-enabled on two distinct pins; an unused pin; one master misses to a default
    slave; a secure-only slave; a reset with sync assertion and async deassertion."""
    c = Cfg("ahb_fixed", extra_inputs=("boot_a_i", "boot_b_i", "spare_i"))
    c.domain("D_H", "clk_h", "rst_h_n", assertion="sync", deassertion="async")
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_H", arb="fixed-priority")
    # Priorities chosen so the fixed-priority order (m2, m0, m1) differs from the
    # round-robin order from reset (m1, m2, m0).
    for index, (name, prio) in enumerate((("m0", 1), ("m1", 2), ("m2", 0))):
        c.master(name, "AHB-Lite", 32, 32, "D_H").link(name, "ahb", index, prio)
        c.option(name, **AHB_MASTER_NA)
    for i, name in enumerate(("s0", "s1", "s2")):
        c.slave(name, "AHB-Lite", 32, 32, "D_H").link("ahb", name, i)
        c.region(name, "mem", 0x2000_0000 + i * 0x1_0000, 0x1_0000, secure=name == "s2")
        c.option(name, **AHB_SLAVE_NA)
    c.decode("m0", "HRESP_ERROR", alias=(0x0000_0000, 0x1000, 0x2000_0000, "always"))
    c.decode(
        "m1", "default-slave", default="s1", alias=(0x0000_0000, 0x1000, 0x2001_0000, "boot_a_i")
    )
    c.decode("m2", "HRESP_ERROR", alias=(0x0001_0000, 0x1000, 0x2002_0000, "boot_b_i"))
    return c


def ahb2apb_cdc() -> Cfg:
    """AHB (64-bit, fast domain) to a 16-bit-address APB4 fabric (32-bit data, slow
    domain) through a CDC bridge with pass-through N and a high window; an alias on the
    bridge's own Address_Decode row (pin); active-low and active-high resets."""
    c = Cfg("ahb2apb_cdc", extra_inputs=("remap_i",))
    c.domain("D_FAST", "clk_fast", "rst_fast_n")
    c.domain("D_SLOW", "clk_slow", "rst_slow", polarity="active-high")
    c.fabric("ahb", "AHB-Lite", 64, 32, "D_FAST").fabric("apb", "APB4", 32, 16, "D_SLOW")
    c.master("cpu", "AHB-Lite", 64, 32, "D_FAST").link("cpu", "ahb", 0)
    c.slave("mem", "AHB-Lite", 64, 16, "D_FAST").link("ahb", "mem", 0)
    c.bridge("br", 0x9000_0000, pass_through=False).link("ahb", "br", 1).link("br", "apb", 0)
    c.slave("uart", "APB4", 32, 12, "D_SLOW").link("apb", "uart", 0)
    c.slave("gpio", "APB4", 32, 12, "D_SLOW").link("apb", "gpio", 1)
    c.region("mem", "ram", 0x0000_0000, 0x1_0000)
    c.region("br", "periph", 0x9000_0000, 0x1_0000)
    c.region("uart", "regs", 0x9000_0000, 0x1000).region("gpio", "regs", 0x9000_1000, 0x1000)
    c.decode("cpu", "HRESP_ERROR")
    c.decode("br", "PSLVERR", alias=(0x9000_8000, 0x1000, 0x9000_1000, "remap_i"))
    c.option("cpu", **AHB_MASTER_NA).option("mem", **AHB_SLAVE_NA)
    c.option("br", **APB4_NA).option("uart", **APB4_NA).option("gpio", **APB4_NA)
    return c


def nested_deep() -> Cfg:
    """Four levels: top { br_a, l0 { l1 { ahb, br_b, l2 { apb_a, l3 { apb_b } } } } }.
    l0 holds no instance (pure pass-through); bridges sit away from their fabrics; the
    same RTL name in two wrappers; a second clock domain behind a CDC bridge; an alias
    pin routed down; a reordered sub-wrapper port pattern; sv-interface port style."""
    c = Cfg("nested_deep", extra_inputs=("boot_i",), style="sv-interface",
            subwrapper_naming="{signal}_{proto}_{instance}")  # fmt: skip
    c.domain("D_SYS", "clk_sys", "rst_sys_n").domain("D_SLOW", "clk_slow", "rst_slow_n")
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_SYS")
    c.fabric("apb_a", "APB4", 32, 32, "D_SYS").fabric("apb_b", "APB4", 32, 32, "D_SLOW")
    c.master("mcu", "AHB-Lite", 32, 32, "D_SYS").link("mcu", "ahb", 0)
    c.slave("tcm", "AHB-Lite", 32, 16, "D_SYS").link("ahb", "tcm", 0)
    c.bridge("br_a", 0x4000_0000).link("ahb", "br_a", 1).link("br_a", "apb_a", 0)
    c.bridge("br_b", 0x5000_0000).link("ahb", "br_b", 2).link("br_b", "apb_b", 0)
    c.slave("timer", "APB4", 32, 12, "D_SYS").link("apb_a", "timer", 0)
    c.slave("wdt", "APB4", 32, 12, "D_SLOW").link("apb_b", "wdt", 0)
    c.slave("rtc", "APB4", 32, 12, "D_SLOW").link("apb_b", "rtc", 1)
    c.region("tcm", "mem", 0x0000_0000, 0x1_0000)
    c.region("br_a", "win", 0x4000_0000, 0x1_0000).region("br_b", "win", 0x5000_0000, 0x1_0000)
    c.region("timer", "regs", 0x4000_0000, 0x1000)
    c.region("wdt", "regs", 0x5000_0000, 0x1000).region("rtc", "regs", 0x5000_1000, 0x1000)
    c.decode("mcu", "HRESP_ERROR", alias=(0x0010_0000, 0x1000, 0x5000_1000, "boot_i"))
    c.decode("br_a", "PSLVERR").decode("br_b", "PSLVERR")
    c.option("mcu", **AHB_MASTER_NA).option("tcm", **AHB_SLAVE_NA)
    for port in ("br_a", "br_b", "timer", "wdt", "rtc"):
        c.option(port, **APB4_NA)
    top = "nested_deep_top"
    c.sub("l0", top).sub("l1", "l0").sub("l2", "l1").sub("l3", "l2")
    c.place("ahb", "l1", "u_ahb_core").place("br_b", "l1").place("br_a", top, "u_bridge_a")
    c.place("apb_a", "l2", "u_mux").place("apb_b", "l3", "u_mux")
    return c


def multi() -> Cfg:
    """Two AHB and two APB fabrics in three domains: ahb_m (two masters, round-robin)
    feeds an APB4 fabric through a pass-through Y bridge and an APB3 fabric through a
    CDC pass-through N bridge; a second, separate AHB fabric with its own alias pin;
    a bridge downstream tie (PSTRB tie-default) and a slave PSLVERR tie-0; an unused
    input."""
    c = Cfg("multi", extra_inputs=("remap_i", "unused_i"))
    c.domain("D_M", "clk_m", "rst_m_n").domain("D_P3", "clk_p3", "rst_p3_n")
    c.domain("D_S", "clk_s", "rst_s", polarity="active-high")
    c.fabric("ahb_m", "AHB-Lite", 32, 32, "D_M", arb="round-robin")
    c.fabric("ahb_s", "AHB-Lite", 32, 32, "D_S")
    c.fabric("p4", "APB4", 32, 32, "D_M").fabric("p3", "APB3", 32, 32, "D_P3")
    c.master("cpu", "AHB-Lite", 32, 32, "D_M").link("cpu", "ahb_m", 0)
    c.master("dma", "AHB-Lite", 32, 32, "D_M").link("dma", "ahb_m", 1)
    c.master("aux", "AHB-Lite", 32, 32, "D_S").link("aux", "ahb_s", 0)
    c.slave("ram", "AHB-Lite", 32, 32, "D_M").link("ahb_m", "ram", 0)
    c.bridge("br_y", 0x4000_0000).link("ahb_m", "br_y", 1).link("br_y", "p4", 0)
    c.bridge("br_n", 0x6000_0000, pass_through=False).link("ahb_m", "br_n", 2)
    c.link("br_n", "p3", 0)
    c.slave("uart", "APB4", 32, 12, "D_M").link("p4", "uart", 0)
    c.slave("spi", "APB4", 32, 12, "D_M").link("p4", "spi", 1)
    c.slave("legacy", "APB3", 32, 12, "D_P3").link("p3", "legacy", 0)
    c.slave("aux_ram", "AHB-Lite", 32, 32, "D_S").link("ahb_s", "aux_ram", 0)
    c.region("ram", "mem", 0x0000_0000, 0x1_0000)
    c.region("br_y", "win", 0x4000_0000, 0x1_0000).region("br_n", "win", 0x6000_0000, 0x1_0000)
    c.region("uart", "regs", 0x4000_0000, 0x1000, access="RO")
    c.region("spi", "regs", 0x4000_1000, 0x1000)
    c.region("legacy", "regs", 0x6000_0000, 0x1000)
    c.region("aux_ram", "mem", 0x8000_0000, 0x1_0000)
    for m in ("cpu", "dma"):
        c.decode(m, "HRESP_ERROR")
    c.decode("aux", "HRESP_ERROR", alias=(0x0000_0000, 0x1000, 0x8000_0000, "remap_i"))
    c.decode("br_y", "PSLVERR").decode("br_n", "PSLVERR")
    c.access("dma", "br_n", "-")  # an Access_Rule on a bridge port
    for port in ("cpu", "dma", "aux"):
        c.option(port, **AHB_MASTER_NA)
    c.option("ram", **AHB_SLAVE_NA).option("aux_ram", **AHB_SLAVE_NA)
    c.option("br_y", **APB4_NA, pstrb="tie-default")
    c.option("br_n", **APB3_NA)
    c.option("uart", **APB4_NA).option("spi", **APB4_NA, pslverr="tie-0")
    c.option("legacy", **APB3_NA)
    return c


BUILT: dict[str, Callable[[], Cfg]] = {
    f.__name__: f
    for f in (apb4_min, apb3_wide, apb4_rules, ahb_single, ahb_rr, ahb_fixed, ahb2apb_cdc,
              nested_deep, multi)
}  # fmt: skip
# Hand-written IR files that predate the DSL (PR 3/PR 4); part of the 10.
LEGACY = ("ahb2apb_pt", "ahb2apb_nested")


def write_all() -> None:
    dsl.write_all(BUILT, HERE)


if __name__ == "__main__":
    write_all()
