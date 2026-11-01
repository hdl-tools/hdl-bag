# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The Phase 3 gate corpus: hand-written AXI and mixed-protocol configs.

Each config is written at workbook level with the shared DSL (tests/configs/dsl.py) and
becomes <name>.ir.yaml through workbook -> `bag check` (zero findings) -> resolve.
`python tests/configs/p3/corpus.py` rewrites the IR files; tests/gen/test_corpus.py keeps
them in sync. PR 3 adds the AXI fabric configs (no adapters yet); later PRs add adapters,
bridges and the mixed configs.
"""

import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))  # tests/configs: the shared DSL

import dsl  # noqa: E402
from dsl import AXI4_NA, AXIL_NA, Cfg  # noqa: E402

KB, MB = 0x400, 0x10_0000


def _xbar_rules(c: Cfg, proto: str, na: dict[str, str]) -> Cfg:
    """Three masters, three slaves on one crossbar with every decode feature."""
    axi4 = proto == "AXI4"
    c.domain("D_SYS", "clk_sys", "rst_sys_n")
    c.fabric("xbar", proto, 64 if axi4 else 32, 32, "D_SYS", "round-robin",
             latency="CUT_ALL_AX", max_trans=(4, 4))  # fmt: skip
    dw = 64 if axi4 else 32
    c.master("cpu", proto, dw, 32, "D_SYS", id_width=4 if axi4 else None)
    c.master("dma", proto, dw, 32, "D_SYS", id_width=2 if axi4 else None)
    c.master("dbg", proto, dw, 24, "D_SYS", id_width=3 if axi4 else None)  # narrow address
    c.slave("mem", proto, dw, 32, "D_SYS")
    c.slave("rom", proto, dw, 16, "D_SYS")
    c.slave("regs", proto, dw, 16, "D_SYS")
    # Port indices differ from row order on purpose.
    c.link("dma", "xbar", 0).link("cpu", "xbar", 1).link("dbg", "xbar", 2)
    c.link("xbar", "regs", 0).link("xbar", "mem", 1).link("xbar", "rom", 2)
    c.region("mem", "lo", 0x8000_0000, 1 * MB)
    c.region("mem", "sec", 0x8010_0000, 64 * KB, secure=True)
    c.region("rom", "code", 0x0001_0000, 64 * KB, access="RO")
    c.region("regs", "csr", 0x4000_0000, 4 * KB)
    c.region("regs", "log", 0x4000_1000, 4 * KB, access="WO")
    c.decode("cpu", "DECERR", alias=(0x0000_0000, 64 * KB, 0x0001_0000, "boot_remap_i"))
    c.decode("dma", "SLVERR")
    # dbg's writes to the read-only rom are denied by its own default slave (D32: error).
    c.decode("dbg", "default-slave", default="rom")
    c.access("dma", "rom", "-")  # DMA cannot reach the ROM
    c.access("cpu", "regs", "RO")  # the CPU may only read the registers
    for p in ("cpu", "dma", "dbg", "mem", "rom", "regs"):
        c.option(p, **na)
    return c


def axi_xbar_rules() -> Cfg:
    """AXI4 crossbar: aliases on a pin, RO/WO and secure regions, Access_Rules (none on an RO
    region, RO on a RW region), DECERR / SLVERR / default-slave misses, a narrow master address, mixed ID widths."""
    return _xbar_rules(Cfg("axi_xbar_rules", phase=3, extra_inputs=("boot_remap_i",)),
                       "AXI4", AXI4_NA)  # fmt: skip


def axil_xbar_rules() -> Cfg:
    """The AXI4-Lite counterpart of axi_xbar_rules."""
    return _xbar_rules(Cfg("axil_xbar_rules", phase=3, extra_inputs=("boot_remap_i",)),
                       "AXI4-Lite", AXIL_NA)  # fmt: skip


def _shared(c: Cfg, proto: str, na: dict[str, str], arb: str, midpoint: tuple[int, str],
            n_masters: int, n_slaves: int, prios: tuple[int, ...] = ()) -> Cfg:  # fmt: skip
    """A shared bus; fixed priority defaults to priorities reversed against the Port index."""
    axi4 = proto == "AXI4"
    c.domain("D_SYS", "clk_sys", "rst_sys_n")
    c.fabric("bus", proto, 32, 32, "D_SYS", arb, topology="shared-bus", max_trans=(2, 2),
             midpoint=midpoint)  # fmt: skip
    for m in range(n_masters):
        name = f"m{m}"
        c.master(name, proto, 32, 32, "D_SYS", id_width=m + 1 if axi4 else None)
        prio = None
        if arb == "fixed-priority":
            prio = prios[m] if prios else n_masters - 1 - m
        c.link(name, "bus", m, prio)
        c.decode(name, "SLVERR" if m % 2 else "DECERR")
        c.option(name, **na)
    for s in range(n_slaves):
        name = f"s{s}"
        c.slave(name, proto, 32, 32, "D_SYS")
        c.link("bus", name, s)
        c.region(name, "regs", 0x1000_0000 + s * 64 * KB, 16 * KB, access="RO" if s == 1 else "RW")
        c.option(name, **na)
    return c


def axi_shared_fixed() -> Cfg:
    """AXI4 shared bus, fixed priority against the Port index, FIFO 2 + multicut-3."""
    return _shared(Cfg("axi_shared_fixed", phase=3), "AXI4", AXI4_NA, "fixed-priority",
                   (2, "multicut-3"), 3, 3)  # fmt: skip


def axil_shared_rr() -> Cfg:
    """AXI4-Lite shared bus, round robin, no FIFO, one register slice."""
    return _shared(Cfg("axil_shared_rr", phase=3), "AXI4-Lite", AXIL_NA, "round-robin",
                   (0, "cut"), 3, 2)  # fmt: skip


def axil_shared_one() -> Cfg:
    """AXI4-Lite shared bus with one master and one slave (the mux/demux bypasses)."""
    return _shared(Cfg("axil_shared_one", phase=3), "AXI4-Lite", AXIL_NA, "round-robin",
                   (4, "none"), 1, 1)  # fmt: skip


def axi_xbar_pow2() -> Cfg:
    """AXI4 crossbar with 4 slaves (the route map's last end address wraps to 0), USER
    signals, a 40-bit fabric with a 32-bit master, and no latency cuts."""
    c = Cfg("axi_xbar_pow2", phase=3)
    c.domain("D_SYS", "clk_sys", "rst_sys_n")
    c.fabric("xbar", "AXI4", 32, 40, "D_SYS", "round-robin", latency="NO_LATENCY",
             max_trans=(8, 8))  # fmt: skip
    c.master("a", "AXI4", 32, 40, "D_SYS", id_width=3, user=2)
    c.master("b", "AXI4", 32, 32, "D_SYS", id_width=1, user=2)
    c.link("a", "xbar", 0).link("b", "xbar", 1)
    for i in range(4):
        name = f"s{i}"
        c.slave(name, "AXI4", 32, 20, "D_SYS", user=2)
        c.link("xbar", name, 3 - i)  # Port index against the address order
        c.region(name, "mem", 0x2000_0000 + i * MB, MB)
        c.option(name, **AXI4_NA)
    for m in ("a", "b"):
        c.decode(m, "DECERR")
        c.option(m, **AXI4_NA)
    return c


def axi_shared_one() -> Cfg:
    """AXI4 shared bus with one master and one slave (the mux and demux pass through)."""
    return _shared(Cfg("axi_shared_one", phase=3), "AXI4", AXI4_NA, "round-robin",
                   (0, "none"), 1, 1)  # fmt: skip


def axil_shared_fixed() -> Cfg:
    """AXI4-Lite shared bus, fixed priority in a non-monotonic order (m2, m0, m1), no
    mid-point: a stalled slave leaves requests waiting at the arbiter (the lock check)."""
    return _shared(Cfg("axil_shared_fixed", phase=3), "AXI4-Lite", AXIL_NA, "fixed-priority",
                   (0, "none"), 3, 2, prios=(1, 2, 0))  # fmt: skip


def axi_shared_rr() -> Cfg:
    """AXI4 shared bus, round robin, 4 masters x 4 slaves (power-of-two route bits), no
    mid-point."""
    return _shared(Cfg("axi_shared_rr", phase=3), "AXI4", AXI4_NA, "round-robin",
                   (0, "none"), 4, 4)  # fmt: skip


def axi_adapters() -> Cfg:
    """AXI4 crossbar with every adapter kind and direction (D9 chain order): cdc + dw down
    on a master in another domain, pc (Lite -> AXI4) + dw up, fifo + multicut on a master;
    dw up + fifo + cut on a slave, iw down (remap) and iw up, pc (AXI4 -> Lite) + dw down,
    cdc on a slave in another domain."""
    c = Cfg("axi_adapters", phase=3)
    c.domain("D_SYS", "clk_sys", "rst_sys_n").domain("D_CPU", "clk_cpu", "rst_cpu_n")
    c.domain("D_PER", "clk_per", "rst_per_n")
    c.fabric("xbar", "AXI4", 64, 32, "D_SYS", "round-robin", latency="CUT_SLV_AX",
             max_trans=(4, 4))  # fmt: skip
    # USER (4 bits) on every AXI4 port, so it crosses cdc, dw, iw, fifo and cut.
    c.master("cpu", "AXI4", 128, 32, "D_CPU", id_width=4, user=4)
    c.master("dbg", "AXI4-Lite", 32, 32, "D_SYS")
    c.master("dma", "AXI4", 64, 32, "D_SYS", id_width=3, user=4)
    c.slave("ddr", "AXI4", 128, 32, "D_SYS", user=4)
    c.slave("sram", "AXI4", 64, 20, "D_SYS", id_width=2, user=4)  # narrower ID: remap
    c.slave("wide", "AXI4", 64, 20, "D_SYS", id_width=8, user=4)  # wider ID: prepend
    c.slave("pmu", "AXI4-Lite", 32, 12, "D_SYS")
    c.slave("per", "AXI4", 64, 16, "D_PER", user=4)
    for i, m in enumerate(("cpu", "dbg", "dma")):
        c.link(m, "xbar", i).decode(m, "DECERR")
    for i, s_ in enumerate(("ddr", "sram", "wide", "pmu", "per")):
        c.link("xbar", s_, i)
    c.region("ddr", "mem", 0x8000_0000, 16 * MB)
    c.region("sram", "mem", 0x1000_0000, MB)
    c.region("wide", "mem", 0x1010_0000, MB)
    c.region("pmu", "regs", 0x4000_0000, 4 * KB)
    c.region("per", "regs", 0x4001_0000, 64 * KB)
    c.option("cpu", **AXI4_NA).option("dbg", **AXIL_NA)
    c.option("dma", fifo=2, slice="multicut-2", **AXI4_NA)
    c.option("ddr", fifo=4, slice="cut", **AXI4_NA)
    for s_ in ("sram", "wide", "per"):
        c.option(s_, **AXI4_NA)
    c.option("pmu", **AXIL_NA)
    return c


def axil_adapters() -> Cfg:
    """AXI4-Lite crossbar with adapters: a 64-bit AXI4 master (dw down on the AXI4 side,
    then pc: D33), dw down on a 64-bit Lite master, cdc on a Lite master in another domain
    with a cut; dw up on a 64-bit Lite slave with a fifo, a 64-bit AXI4 slave (pc, then dw
    up on the AXI4 side: D33)."""
    c = Cfg("axil_adapters", phase=3)
    c.domain("D_SYS", "clk_sys", "rst_sys_n").domain("D_AON", "clk_aon", "rst_aon_n")
    c.fabric("cfg", "AXI4-Lite", 32, 32, "D_SYS", "round-robin", latency="NO_LATENCY",
             max_trans=(2, 2))  # fmt: skip
    c.master("cpu", "AXI4", 64, 32, "D_SYS", id_width=4)
    c.master("dsp", "AXI4-Lite", 64, 32, "D_SYS")
    c.master("aon", "AXI4-Lite", 32, 32, "D_AON")
    c.slave("buf", "AXI4-Lite", 64, 16, "D_SYS")
    c.slave("mem", "AXI4", 64, 16, "D_SYS")
    for i, m in enumerate(("cpu", "dsp", "aon")):
        c.link(m, "cfg", i).decode(m, "SLVERR" if i == 1 else "DECERR")
    c.link("cfg", "buf", 0).link("cfg", "mem", 1)
    c.region("buf", "regs", 0x2000_0000, 16 * KB)
    c.region("mem", "mem", 0x2001_0000, 64 * KB)
    c.option("cpu", **AXI4_NA).option("dsp", **AXIL_NA)
    c.option("aon", slice="cut", **AXIL_NA)
    c.option("buf", fifo=2, **AXIL_NA).option("mem", **AXI4_NA)
    return c


BUILT: dict[str, Callable[[], Cfg]] = {
    f.__name__: f
    for f in (axi_xbar_rules, axil_xbar_rules, axi_xbar_pow2, axi_shared_fixed, axi_shared_rr,
              axi_shared_one, axil_shared_rr, axil_shared_fixed, axil_shared_one, axi_adapters,
              axil_adapters)
}  # fmt: skip


def write_all() -> None:
    dsl.write_all(BUILT, HERE)


if __name__ == "__main__":
    write_all()
