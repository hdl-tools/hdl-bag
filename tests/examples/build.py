# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Builds the example workbooks in examples/ with the regress DSL.

The builder lives here, not in examples/, so that nothing suggests a user needs Python to write
a spec. ``python tests/examples/build.py`` writes each workbook, recalculated with LibreOffice
so that it opens with its check columns filled in. Recalculation is presentation: the reader
never trusts cached results, so tests compare content (``signature``), never bytes.
"""

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from hdl_bag.regress.dsl import APB4_NA, AXI4_NA, AXIL_NA, Cfg
from hdl_bag.template import build_workbook

REBUILD = "uv run python tests/examples/build.py"
HERE = Path(__file__).parent
OUT = HERE.parents[1] / "examples"
KB, MB = 0x400, 0x10_0000
AHB_NA = {"qos": "n/a", "region": "n/a", "cache": "n/a", "pstrb": "n/a", "pslverr": "n/a"}


def _example(name: str, title: str, description: str) -> Cfg:
    c = Cfg(name, phase=6)
    c.data.title = title
    c.data.description = description
    c.data.project = {**c.data.project, "Spec revision": "1.0", "Author": "hdl-bag examples"}
    return c


def apb_min() -> Cfg:
    """One APB4 master and three slaves: the smallest workbook that generates."""
    c = _example("apb_min", "Example: one APB4 master, three slaves",
                 "The smallest workbook that generates. Every sheet is filled once.")  # fmt: skip
    c.domain("D_SYS", "clk", "rst_n")
    c.fabric("apb", "APB4", 32, 32, "D_SYS")
    c.master(
        "cpu", "APB4", 32, 32, "D_SYS", description="The one master: a processor or a test bench."
    )
    c.link("cpu", "apb", 0)
    for i, name in enumerate(("uart", "gpio", "timer")):
        c.slave(name, "APB4", 32, 12, "D_SYS", description=f"A 4 KiB register block ({name}).")
        c.link("apb", name, i)
        c.region(name, "regs", 0x1000_0000 + i * 4 * KB, 4 * KB)
        c.option(name, **APB4_NA)
    c.decode("cpu", "PSLVERR")  # a miss gets an error response
    c.option("cpu", **APB4_NA)
    return c


def mcu_ahb() -> Cfg:
    """An AHB-Lite MCU bus: SRAM, and an APB peripheral bus behind bag_ahb2apb."""
    c = _example("mcu_ahb", "Example: AHB-Lite microcontroller bus",
                 "A CPU on an AHB-Lite bus with an SRAM and, behind a bag_ahb2apb bridge, "
                 "an APB4 bus of three peripherals.")  # fmt: skip
    c.domain("D_SYS", "clk", "rst_n")
    c.fabric("ahb", "AHB-Lite", 32, 32, "D_SYS").fabric("apb", "APB4", 32, 32, "D_SYS")
    c.master("cpu", "AHB-Lite", 32, 32, "D_SYS", description="A Cortex-M style processor.")
    c.slave("sram", "AHB-Lite", 32, 16, "D_SYS", description="64 KiB of on-chip memory.")
    c.bridge("br_apb", 0x4000_0000, template="bag_ahb2apb")
    c.link("cpu", "ahb", 0).link("ahb", "sram", 0).link("ahb", "br_apb", 1)
    c.link("br_apb", "apb", 0)
    c.region("sram", "mem", 0x2000_0000, 64 * KB)
    c.region("br_apb", "win", 0x4000_0000, 64 * KB)
    for i, name in enumerate(("uart", "gpio", "timer")):
        c.slave(name, "APB4", 32, 12, "D_SYS", description=f"A 4 KiB register block ({name}).")
        c.link("apb", name, i)
        c.region(name, "regs", 0x4000_0000 + i * 4 * KB, 4 * KB)
        c.option(name, **APB4_NA)
    c.decode("cpu", "HRESP_ERROR").decode("br_apb", "PSLVERR")
    c.option("cpu", **AHB_NA).option("sram", **{**AHB_NA, "lock": "n/a"}).option(
        "br_apb", **APB4_NA
    )
    return c


def soc_4x8() -> Cfg:
    """The usability-gate target: 4 masters and 8 slaves on one AXI4 crossbar, one clock."""
    c = _example("soc_4x8", "Example: 4 masters, 8 slaves",
                 "An AXI4 crossbar with three AXI4 masters and an AXI4-Lite debug master, a ROM, "
                 "a RAM, and, behind bag_axi2apb, an APB4 bus of six peripherals.")  # fmt: skip
    c.domain("D_SYS", "clk", "rst_n")
    c.fabric("main_xbar", "AXI4", 32, 32, "D_SYS", "round-robin", max_trans=(4, 4))
    c.fabric("periph_apb", "APB4", 32, 32, "D_SYS")
    c.master("cpu_i", "AXI4", 32, 32, "D_SYS", description="CPU instruction fetch: reads only.")
    c.master("cpu_d", "AXI4", 32, 32, "D_SYS", description="CPU data port.")
    c.master(
        "dma", "AXI4", 32, 32, "D_SYS", description="DMA engine: moves data, never fetches code."
    )
    c.master("dbg", "AXI4-Lite", 32, 32, "D_SYS", description="Debug access port (AXI4-Lite).")
    c.slave("rom", "AXI4", 32, 16, "D_SYS", description="64 KiB boot ROM.")
    c.slave("ram", "AXI4", 32, 20, "D_SYS", description="1 MiB RAM.")
    c.bridge("br_apb", 0x4000_0000, template="bag_axi2apb")
    for i, m in enumerate(("cpu_i", "cpu_d", "dma", "dbg")):
        c.link(m, "main_xbar", i)
    c.link("main_xbar", "rom", 0).link("main_xbar", "ram", 1).link("main_xbar", "br_apb", 2)
    c.link("br_apb", "periph_apb", 0)
    c.region("rom", "boot", 0x0000_0000, 64 * KB, access="RO")
    c.region("ram", "mem", 0x2000_0000, MB)
    c.region("br_apb", "win", 0x4000_0000, 64 * KB)
    for i, name in enumerate(("uart", "gpio", "timer", "spi", "i2c", "wdt")):
        c.slave(name, "APB4", 32, 12, "D_SYS", description=f"A 4 KiB register block ({name}).")
        c.link("periph_apb", name, i)
        c.region(name, "regs", 0x4000_0000 + i * 4 * KB, 4 * KB)
        c.option(name, **APB4_NA)
    # Two denials: the instruction port may not write the RAM, the DMA may not touch the ROM.
    c.access("cpu_i", "ram", "RO").access("dma", "rom", "-")
    for m in ("cpu_i", "cpu_d", "dma", "dbg"):
        c.decode(m, "DECERR")
        c.option(m, **(AXIL_NA if m == "dbg" else AXI4_NA))
    c.decode("br_apb", "PSLVERR")
    c.option("rom", **AXI4_NA).option("ram", **AXI4_NA).option("br_apb", **APB4_NA)
    return c


BUILT: dict[str, Callable[[], Cfg]] = {f.__name__: f for f in (apb_min, mcu_ahb, soc_4x8)}


def main() -> None:
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    from recalc import recalc

    OUT.mkdir(exist_ok=True)
    (OUT / "header.txt").write_text(
        "// Copyright 2026 Example Corp.\n// SPDX-License-Identifier: Apache-2.0\n//\n"
        "// Sample file header for the example workbooks (Wrapper > File header).\n",
        encoding="utf-8", newline="\n",
    )  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        for name, make in BUILT.items():
            raw = Path(tmp) / f"{name}.xlsx"
            build_workbook(data=make().data).save(raw)
            recalc(raw, OUT / f"{name}.xlsx")
            print(f"{name}: written")


if __name__ == "__main__":
    main()
