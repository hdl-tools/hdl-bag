# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Wiring smoke test through the generated top of tests/configs/p2/ahb2apb_pt.

mcu0 (AHB-Lite master) -> ahb_bus -> itcm0 (AHB slave, 16-bit addresses) and
br_ahb2aon (ahb2apb, pass-through Y) -> aon_apb -> wdt0 (secure) / rtc0 (12-bit).
The full generated test suite is Phase 4; this proves the generator's wiring:
port naming, address truncation, tie-offs and reset conditioning.
"""

from typing import Any

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from hdl_bag.tests_gen.bfm import (
    AHB_M,
    AHB_S,
    APB,
    AhbMaster,
    AhbResult,
    AhbSlave,
    ApbSlave,
    wrapper_port,
)


async def _setup(dut: Any) -> tuple[AhbMaster, AhbSlave, ApbSlave, ApbSlave]:
    Clock(dut.clk_aon, 20, unit="ns").start()
    dut.rst_aon_n.value = 0
    dut.boot_remap_i.value = 0
    itcm = AhbSlave(dut.clk_aon, wrapper_port(dut, "itcm0_ahb", AHB_S, 16, 32), ident=0)
    # wdt0 and rtc0 have PSTRB tie-default (all ones): the wrapper does not expose it.
    wdt = ApbSlave(dut.clk_aon, wrapper_port(dut, "wdt0_apb", APB, 12, 32, {"pstrb": -1}), ident=1)
    rtc = ApbSlave(dut.clk_aon, wrapper_port(dut, "rtc0_apb", APB, 12, 32, {"pstrb": -1}), ident=2)
    for s in (itcm, wdt, rtc):
        s.start()
    # mcu0 has LOCK tied off.
    mcu = AhbMaster(dut.clk_aon, wrapper_port(dut, "mcu0_ahb", AHB_M, 32, 32, {"hmastlock": 0}))
    await ClockCycles(dut.clk_aon, 4)
    dut.rst_aon_n.value = 1
    await ClockCycles(dut.clk_aon, 4)  # the reset synchronizer releases after two clocks
    return mcu, itcm, wdt, rtc


@cocotb.test(timeout_time=200, timeout_unit="us")
async def ahb_slave_sees_its_address_bits(dut: Any) -> None:
    mcu, itcm, _, _ = await _setup(dut)
    assert not await mcu.write(0x0000_FF00, 0x1234_5678, prot=0b0010, nonsec=1)
    assert (await mcu.read(0x0000_FF00)) == AhbResult(0x1234_5678, False)
    assert not await mcu.write(0x0000_FF06, 0xAB << 16, size=1)  # halfword at offset 2
    a = itcm.log
    assert [(x.addr, x.size) for x in a] == [(0xFF00, 2), (0xFF00, 2), (0xFF06, 1)]
    assert a[0].prot == 0b0010 | 1 << 4  # HPROT and HNONSEC reach the slave


@cocotb.test(timeout_time=200, timeout_unit="us")
async def apb_slaves_through_the_bridge(dut: Any) -> None:
    mcu, itcm, wdt, rtc = await _setup(dut)
    assert not await mcu.write(0x5000_1010, 0xCAFE_0001)
    assert (await mcu.read(0x5000_1010)) == AhbResult(0xCAFE_0001, False)
    assert [a.addr for a in rtc.log] == [0x010, 0x010]  # 12-bit slave address
    assert not await mcu.write(0x5000_1014, 1, nonsec=1)  # rtc0 is not secure-only
    assert not await mcu.write(0x5000_0020, 7, prot=0b0010)  # wdt0 is secure-only: secure ok
    assert await mcu.write(0x5000_0024, 7, nonsec=1)  # ... non-secure is blocked
    assert [a.addr for a in wdt.log] == [0x020] and not itcm.log
    # PPROT = {instruction = !HPROT[0], non-secure = HNONSEC, privileged = HPROT[1]}
    assert (rtc.log[-1].prot, wdt.log[0].prot) == (0b011, 0b101)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def unmapped_address_is_an_error(dut: Any) -> None:
    mcu, itcm, wdt, rtc = await _setup(dut)
    assert await mcu.write(0x6000_0000, 1)
    assert (await mcu.read(0x5000_2000)).err  # inside the bridge window, unmapped on aon_apb
    assert not (itcm.log or wdt.log or rtc.log)
