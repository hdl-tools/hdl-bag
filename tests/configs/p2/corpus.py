# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The Phase 2 gate corpus: 10 hand-written APB/AHB-only configs.

Each config is written at workbook level (the same inputs a user types) and becomes
<name>.ir.yaml through the real path: workbook -> `bag check` (zero findings) -> resolve.
`python tests/configs/p2/corpus.py` rewrites the IR files; tests/gen/test_corpus.py
keeps them in sync. The feature each config must cover follows the PR 3/PR 4 design
reviews; see COVERAGE.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hdl_bag.template.data import PROJECT_DEFAULTS, WRAPPER_DEFAULTS, Cell, SpecData

HERE = Path(__file__).parent
TEMPLATE = {"AHB-Lite": "bag_ahb_lite_bus", "APB4": "bag_apb_mux", "APB3": "bag_apb_mux"}
TOPOLOGY = {"AHB-Lite": "single-layer", "APB4": "decoder-mux", "APB3": "decoder-mux"}
ERROR = {"AHB-Lite": "HRESP_ERROR", "APB4": "PSLVERR", "APB3": "PSLVERR"}
OPTION_COLUMNS = ("fifo", "slice", "prot", "qos", "region", "cache", "lock", "pstrb", "pslverr")


@dataclass
class Cfg:
    name: str
    extra_inputs: tuple[str, ...] = ()
    style: str = "flat"
    signal_pattern: str = "{port}_{proto}_{signal}"
    subwrapper_naming: str = "{instance}_{proto}_{signal}"
    data: SpecData = field(init=False)

    def __post_init__(self) -> None:
        self.data = SpecData(title=f"BAG Phase 2 gate config: {self.name}",
                             description="Hand-written gate config (tests/configs/p2/corpus.py).")  # fmt: skip
        self.data.project = {**PROJECT_DEFAULTS, "Project name": self.name, "Spec revision": "p2",
                             "Author": "BAG", "Output directory": f"out/{self.name}",
                             "Generate RTL": "Y", "Generate C header": "Y",
                             "Generate memory-map doc": "Y", "Generate cocotb unit tests": "Y",
                             "Generate formal (SymbiYosys)": "Y"}  # fmt: skip
        self.data.wrapper = {**WRAPPER_DEFAULTS, "Top module name": f"{self.name}_top",
                             "Parameter package": f"{self.name}_pkg", "File header": "header.txt",
                             "Port style": self.style, "Signal naming pattern": self.signal_pattern,
                             "Sub-wrapper port naming": self.subwrapper_naming,
                             "Extra top-level inputs": ", ".join(self.extra_inputs) or None}  # fmt: skip

    # ---- clocks and resets
    def domain(self, domain: str, clock: str, reset: str, polarity: str = "active-low",
               assertion: str = "async", deassertion: str = "sync") -> "Cfg":  # fmt: skip
        self.data.clocks.append([clock, 100, "top port", domain, reset, None])
        self.data.resets.append([reset, polarity, assertion, deassertion, domain, "top port", None])
        return self

    # ---- fabrics and ports
    def fabric(self, name: str, proto: str, dw: int, aw: int, domain: str,
               arb: str = "single-master") -> "Cfg":  # fmt: skip
        self.data.fabrics.append([name, proto, TOPOLOGY[proto], dw, aw, domain, arb, "n/a", "n/a",
                                  "n/a", TEMPLATE[proto], "in-house template", "n/a", "n/a",
                                  None])  # fmt: skip
        return self

    def master(self, name: str, proto: str, dw: int, aw: int, domain: str,
               prefix: str | None = None, exposed: bool = True) -> "Cfg":  # fmt: skip
        self.data.masters.append([name, proto, dw, aw, None, 0, domain, 1, 1, None, None])
        return self._expose(name, prefix, exposed)

    def slave(self, name: str, proto: str, dw: int, aw: int, domain: str,
              prefix: str | None = None) -> "Cfg":  # fmt: skip
        self.data.slaves.append([name, proto, dw, aw, None, None, domain, ERROR[proto], 0, None,
                                 None])  # fmt: skip
        return self._expose(name, prefix, True)

    def bridge(self, name: str, window: int, pass_through: bool = True,
               template: str = "bag_ahb2apb") -> "Cfg":  # fmt: skip
        self.data.bridges.append([name, template, _hex(window), "Y" if pass_through else "N", 0,
                                  None])  # fmt: skip
        return self._expose(name, None, False)

    def _expose(self, port: str, prefix: str | None, exposed: bool) -> "Cfg":
        self.data.exposed_ports.append([port, "Y" if exposed else "N", prefix,
                                        None if exposed else "internal", None])  # fmt: skip
        return self

    # ---- connectivity and map
    def link(self, src: str, dst: str, index: int, priority: int | None = None) -> "Cfg":
        self.data.topology.append([src, dst, index, priority, None])
        return self

    def region(self, target: str, region: str, base: int, size: int, access: str = "RW",
               secure: bool = False) -> "Cfg":  # fmt: skip
        self.data.memory_map.append([target, region, _hex(base), _hex(size), access,
                                     "Y" if secure else "N", None])  # fmt: skip
        return self

    def decode(self, master: str, miss: str, default: str | None = None,
               alias: tuple[int, int, int, str] | None = None) -> "Cfg":  # fmt: skip
        base, size, to, enable = alias or (None, None, None, None)
        self.data.address_decode.append([
            master, miss, default, _hex(base) if base is not None else None,
            _hex(size) if size is not None else None, _hex(to) if to is not None else None,
            enable, None,
        ])  # fmt: skip
        return self

    def access(self, master: str, slave: str, access: str) -> "Cfg":
        self.data.access_rules.append([master, slave, access, None])
        return self

    def option(self, port: str, **values: Cell) -> "Cfg":
        """Port_Options row; unspecified optional signals default to drive / n/a."""
        row: dict[str, Cell] = {"fifo": 0, "slice": "none"}
        row.update(values)
        self.data.port_options.append([row.get(c, "drive") for c in OPTION_COLUMNS] + [None])
        self.data.port_options[-1].insert(0, port)
        return self

    # ---- hierarchy
    def sub(self, name: str, parent: str) -> "Cfg":
        self.data.sub_wrappers.append([name, parent, None])
        return self

    def place(self, instance: str, wrapper: str, rtl: str | None = None) -> "Cfg":
        self.data.placement.append([instance, wrapper, rtl, None])
        return self


def _hex(v: int) -> str:
    h = f"{v:08X}"
    return "0x" + "_".join(h[i : i + 4] for i in range(0, len(h), 4))


# Optional-signal values that do not exist on a protocol must be n/a.
APB4_NA = {"qos": "n/a", "region": "n/a", "cache": "n/a", "lock": "n/a"}
APB3_NA = {**APB4_NA, "prot": "n/a", "pstrb": "n/a"}
AHB_MASTER_NA = {"qos": "n/a", "region": "n/a", "cache": "n/a", "pstrb": "n/a", "pslverr": "n/a"}
AHB_SLAVE_NA = {**AHB_MASTER_NA, "lock": "n/a"}


def apb4_min() -> Cfg:
    """Smallest legal design: one APB4 master and slave on a 12-bit fabric; default
    pattern with no prefixes; an unused extra input and an unused clock domain; an
    active-high reset with async assertion and deassertion (no synchronizer)."""
    c = Cfg("apb4_min", extra_inputs=("spare_i",))
    c.domain("D_A", "clk_a", "rst_a", polarity="active-high", deassertion="async")
    c.domain("D_UNUSED", "clk_u", "rst_u_n")
    c.fabric("apb", "APB4", 32, 12, "D_A")
    c.master("m0", "APB4", 32, 12, "D_A").slave("s0", "APB4", 32, 12, "D_A")
    c.link("m0", "apb", 0).link("apb", "s0", 0)
    c.region("s0", "regs", 0x000, 0x800)
    c.decode("m0", "PSLVERR")
    c.option("m0", **APB4_NA).option("s0", **APB4_NA)
    return c


def apb3_wide() -> Cfg:
    """APB3 fabric, 8 slaves whose Port index, name and address orders all differ;
    misses go to a default slave at a non-zero index; PSLVERR tie-0 on two slaves;
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
        c.region(name, "regs", base, 0x1000)
        tie = {"pslverr": "tie-0"} if name in ("p2", "p6") else {}
        c.option(name, **APB3_NA, **tie)
    c.option("host", **APB3_NA)
    c.decode("host", "default-slave", default="p5")
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
        # dual0 has two regions; D29 (each region base aligned to 2^slave width) would
        # reject them on a narrow slave, so it takes the full address width.
        c.slave(name, "APB4", 32, 32 if name == "dual0" else 16, "D_R").link("apb", name, i)
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
    """One AHB-Lite master, four slaves with 12/16/24/32-bit addresses (truncation);
    master HPROT tie-default and LOCK tie-0; one slave with PROT tie-default."""
    c = Cfg("ahb_single")
    c.domain("D_H", "clk_h", "rst_h_n")
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_H")
    c.master("mcu", "AHB-Lite", 32, 32, "D_H", prefix="mcu_ahb").link("mcu", "ahb", 0)
    for i, (name, aw, base, size) in enumerate((
        ("s12", 12, 0x1000_0000, 0x1000), ("s16", 16, 0x2000_0000, 0x1_0000),
        ("s24", 24, 0x3000_0000, 0x10_0000), ("s32", 32, 0x8000_0000, 0x1000),
    )):  # fmt: skip
        c.slave(name, "AHB-Lite", 32, aw, "D_H").link("ahb", name, i)
        c.region(name, "mem", base, size)
        c.option(name, **AHB_SLAVE_NA, **({"prot": "tie-default"} if name == "s16" else {}))
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
    pin-enabled, and one pin shared by two masters; an unused pin; one master misses
    to a default slave; a reset with sync assertion and async deassertion."""
    c = Cfg("ahb_fixed", extra_inputs=("boot_a_i", "spare_i"))
    c.domain("D_H", "clk_h", "rst_h_n", assertion="sync", deassertion="async")
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_H", arb="fixed-priority")
    for index, (name, prio) in enumerate((("m0", 2), ("m1", 0), ("m2", 1))):
        c.master(name, "AHB-Lite", 32, 32, "D_H").link(name, "ahb", index, prio)
        c.option(name, **AHB_MASTER_NA)
    for i, name in enumerate(("s0", "s1", "s2")):
        c.slave(name, "AHB-Lite", 32, 32, "D_H").link("ahb", name, i)
        c.region(name, "mem", 0x2000_0000 + i * 0x1_0000, 0x1_0000)
        c.option(name, **AHB_SLAVE_NA)
    c.decode("m0", "HRESP_ERROR", alias=(0x0000_0000, 0x1000, 0x2000_0000, "always"))
    c.decode(
        "m1", "default-slave", default="s1", alias=(0x0000_0000, 0x1000, 0x2001_0000, "boot_a_i")
    )
    c.decode("m2", "HRESP_ERROR", alias=(0x0001_0000, 0x1000, 0x2002_0000, "boot_a_i"))
    return c


def ahb2apb_cdc() -> Cfg:
    """AHB (64-bit, fast domain) to APB4 (32-bit, slow domain) through a CDC bridge
    with pass-through N and a high window; active-low and active-high resets."""
    c = Cfg("ahb2apb_cdc")
    c.domain("D_FAST", "clk_fast", "rst_fast_n")
    c.domain("D_SLOW", "clk_slow", "rst_slow", polarity="active-high")
    c.fabric("ahb", "AHB-Lite", 64, 32, "D_FAST").fabric("apb", "APB4", 32, 32, "D_SLOW")
    c.master("cpu", "AHB-Lite", 64, 32, "D_FAST").link("cpu", "ahb", 0)
    c.slave("mem", "AHB-Lite", 64, 16, "D_FAST").link("ahb", "mem", 0)
    c.bridge("br", 0x9000_0000, pass_through=False).link("ahb", "br", 1).link("br", "apb", 0)
    c.slave("uart", "APB4", 32, 12, "D_SLOW").link("apb", "uart", 0)
    c.slave("gpio", "APB4", 32, 12, "D_SLOW").link("apb", "gpio", 1)
    c.region("mem", "ram", 0x0000_0000, 0x1_0000)
    c.region("br", "periph", 0x9000_0000, 0x1_0000)
    c.region("uart", "regs", 0x9000_0000, 0x1000).region("gpio", "regs", 0x9000_1000, 0x1000)
    c.decode("cpu", "HRESP_ERROR").decode("br", "PSLVERR")
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
    c.region("uart", "regs", 0x4000_0000, 0x1000).region("spi", "regs", 0x4000_1000, 0x1000)
    c.region("legacy", "regs", 0x6000_0000, 0x1000)
    c.region("aux_ram", "mem", 0x8000_0000, 0x1_0000)
    for m in ("cpu", "dma"):
        c.decode(m, "HRESP_ERROR")
    c.decode("aux", "HRESP_ERROR", alias=(0x0000_0000, 0x1000, 0x8000_0000, "remap_i"))
    c.decode("br_y", "PSLVERR").decode("br_n", "PSLVERR")
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
    """Build every config through workbook -> bag check -> resolve and write its IR."""
    import shutil
    import tempfile

    from hdl_bag.checks import run_checks
    from hdl_bag.ir import dump, resolve
    from hdl_bag.reader import read_workbook
    from hdl_bag.template.builder import build_workbook

    with tempfile.TemporaryDirectory() as tmp:
        for name, make in BUILT.items():
            wb = build_workbook(data=make().data)
            path = Path(tmp) / f"{name}.xlsx"
            wb.save(path)
            shutil.copy(HERE / "header.txt", Path(tmp) / "header.txt")
            result = run_checks(read_workbook(path), Path(tmp))
            if result.findings:
                print(f"{name}:")
                for f in result.findings:
                    print(f"  {f.severity}: {f.message}")
                continue
            dump(resolve(result), HERE / f"{name}.ir.yaml")
            print(f"{name}: ok")


if __name__ == "__main__":
    write_all()
