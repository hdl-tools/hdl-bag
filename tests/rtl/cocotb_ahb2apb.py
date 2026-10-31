# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb smoke tests for bag_ahb2apb (top: tests/rtl/tb/tb_ahb2apb.sv).

The configuration comes from the environment: HDW / PDW data widths, OFFSET (AddrOffset),
HCLK_NS / PCLK_NS clock periods.
"""

import os
from typing import Any

import cocotb
from bfm import (
    AHB_M,
    AHB_M_RESP,
    APB,
    APB_RESP,
    NONSEQ,
    SEQ,
    AhbMaster,
    ApbSlave,
    Beat,
    fabric_port,
)
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

HDW = int(os.environ.get("HDW", "32"))
PDW = int(os.environ.get("PDW", "32"))
OFFSET = int(os.environ.get("OFFSET", "0"), 0)
HCLK_NS = int(os.environ.get("HCLK_NS", "10"))
PCLK_NS = int(os.environ.get("PCLK_NS", "10"))
BASE = 0x4000_0000
INCR4 = 0b011


async def _setup(dut: Any) -> tuple[AhbMaster, ApbSlave]:
    Clock(dut.hclk_i, HCLK_NS, unit="ns").start()
    if PCLK_NS != HCLK_NS:
        Clock(dut.pclk_i, PCLK_NS, unit="ns").start()
        pclk = dut.pclk_i
    else:
        pclk = dut.hclk_i
    dut.hrst_ni.value = 0
    dut.prst_ni.value = 0
    slave = ApbSlave(pclk, fabric_port(dut, "m", 0, APB, APB_RESP, 32, PDW), data_width=PDW)
    slave.start()
    master = AhbMaster(dut.hclk_i, fabric_port(dut, "s", 0, AHB_M, AHB_M_RESP, 32, HDW))
    await ClockCycles(dut.hclk_i, 4)
    dut.hrst_ni.value = 1
    dut.prst_ni.value = 1
    await ClockCycles(dut.hclk_i, 4)
    return master, slave


def _lane(addr: int, value: int) -> int:
    """VALUE placed on the AHB byte lanes of ADDR."""
    return value << (8 * (addr % (HDW // 8)))


@cocotb.test(timeout_time=500, timeout_unit="us")
async def words_cross_with_offset(dut: Any) -> None:
    m, s = await _setup(dut)
    size = (PDW // 8).bit_length() - 1  # one APB-width beat
    for addr in (BASE, BASE + 0x104, BASE + 0xFF0):
        data = (0x89AB_CDEF ^ addr) & ((1 << PDW) - 1)
        assert not await m.write(addr, _lane(addr, data), size=size)
        r = await m.read(addr, size=size)
        assert not r.err
        assert (r.data >> (8 * (addr % (HDW // 8)))) & ((1 << PDW) - 1) == data
        assert s.log[-1].addr == addr - OFFSET


@cocotb.test(timeout_time=500, timeout_unit="us")
async def wide_beat_splits_into_apb_transfers(dut: Any) -> None:
    m, s = await _setup(dut)
    size = (HDW // 8).bit_length() - 1  # one full AHB beat
    data = int.from_bytes(bytes(range(1, HDW // 8 + 1)), "little")
    n = len(s.log)
    assert not await m.write(BASE + 0x200, data, size=size)
    parts = HDW // PDW
    assert [a.addr for a in s.log[n:]] == [
        BASE + 0x200 - OFFSET + i * PDW // 8 for i in range(parts)
    ]
    r = await m.read(BASE + 0x200, size=size)
    assert (r.data, r.err) == (data, False)


@cocotb.test(timeout_time=500, timeout_unit="us")
async def byte_writes_use_strobes(dut: Any) -> None:
    m, s = await _setup(dut)
    assert not await m.write(
        BASE + 0x300, _lane(BASE + 0x300, (1 << PDW) - 1), size=(PDW // 8).bit_length() - 1
    )
    addr = BASE + 0x300 + PDW // 8 - 1  # the top byte of the APB word
    assert not await m.write(addr, _lane(addr, 0x5A), size=0)
    assert s.log[-1].strb == 1 << (PDW // 8 - 1)
    word = s.mem[BASE + 0x300 - OFFSET]
    assert word >> (PDW - 8) == 0x5A and word & ((1 << (PDW - 8)) - 1) == (1 << (PDW - 8)) - 1


@cocotb.test(timeout_time=500, timeout_unit="us")
async def every_byte_lane_writes(dut: Any) -> None:
    """A byte write to each byte of an AHB word strobes exactly its APB lane."""
    m, s = await _setup(dut)
    word, pb = BASE + 0x340, PDW // 8
    for b in range(HDW // 8):
        addr = word + b
        assert not await m.write(addr, _lane(addr, 0xA0 + b), size=0)
        assert s.log[-1].strb == 1 << (b % pb), (b, s.log[-1].strb)
    for b in range(HDW // 8):
        apb_word = (word + b - OFFSET) & ~(pb - 1)
        assert (s.mem[apb_word] >> (8 * (b % pb))) & 0xFF == 0xA0 + b, b


@cocotb.test(timeout_time=500, timeout_unit="us")
async def pslverr_becomes_hresp_error(dut: Any) -> None:
    m, s = await _setup(dut)
    s.err_addrs.add(BASE + 0x400 - OFFSET)
    assert await m.write(BASE + 0x400, 1, size=0)
    assert (await m.read(BASE + 0x400, size=0)).err
    assert not (await m.read(BASE + 0x500, size=0)).err


@cocotb.test(timeout_time=500, timeout_unit="us")
async def pprot_from_hprot_and_hnonsec(dut: Any) -> None:
    """PPROT = {instruction = !HPROT[0], non-secure = HNONSEC, privileged = HPROT[1]}."""
    m, s = await _setup(dut)
    for hprot in range(4):
        for nonsec in (0, 1):
            await m.write(BASE + 0x600, 0, size=0, prot=hprot, nonsec=nonsec)
            expect = (1 - (hprot & 1)) << 2 | nonsec << 1 | (hprot >> 1 & 1)
            assert s.log[-1].prot == expect, (hprot, nonsec, s.log[-1].prot)


@cocotb.test(timeout_time=500, timeout_unit="us")
async def wide_beat_error_on_any_part(dut: Any) -> None:
    m, s = await _setup(dut)
    size, parts = (HDW // 8).bit_length() - 1, HDW // PDW
    for bad in range(parts):
        s.err_addrs = {BASE + 0x800 - OFFSET + bad * PDW // 8}
        n = len(s.log)
        assert await m.write(BASE + 0x800, 0, size=size)
        assert len(s.log) - n == parts  # every part is still issued
    s.err_addrs = set()
    assert not await m.write(BASE + 0x800, 0, size=size)


@cocotb.test(timeout_time=500, timeout_unit="us")
async def reads_drive_no_strobes(dut: Any) -> None:
    m, s = await _setup(dut)
    await m.read(BASE + 0x900, size=0)
    assert s.log[-1].strb == 0 and not s.log[-1].write


@cocotb.test(timeout_time=500, timeout_unit="us")
async def incr4_burst_becomes_four_transfers(dut: Any) -> None:
    m, s = await _setup(dut)
    size = (PDW // 8).bit_length() - 1
    step = PDW // 8
    beats = [
        Beat(BASE + 0x700 + step * i, True, _lane(BASE + 0x700 + step * i, i + 1), size=size,
             burst=INCR4, trans=NONSEQ if i == 0 else SEQ)
        for i in range(4)
    ]  # fmt: skip
    n = len(s.log)
    assert not any(r.err for r in await m.run(beats))
    assert [a.addr for a in s.log[n:]] == [BASE + 0x700 - OFFSET + step * i for i in range(4)]
    assert [a.data & 0xFF for a in s.log[n:]] == [1, 2, 3, 4]
