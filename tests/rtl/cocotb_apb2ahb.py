# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb tests for bag_apb2ahb (top: tests/rtl/tb/tb_apb2ahb.sv).

The configuration comes from the environment: PDW / HDW data widths, OFFSET (AddrOffset),
CDC (AsyncCdc), PCLK_NS / HCLK_NS clock periods.
"""

import os
import random
from typing import Any

import cocotb
from cocotb.triggers import ClockCycles

from hdl_bag.tests_gen.bfm import (
    AHB_S,
    AHB_S_RESP,
    APB,
    APB_RESP,
    NONSEQ,
    AhbSlave,
    ApbMaster,
    bridge_clocks,
    fabric_port,
)

PDW = int(os.environ.get("PDW", "32"))
HDW = int(os.environ.get("HDW", "32"))
OFFSET = int(os.environ.get("OFFSET", "0"), 0)
CDC = os.environ.get("CDC", "0") == "1"
PCLK_NS = int(os.environ.get("PCLK_NS", "10"))
HCLK_NS = int(os.environ.get("HCLK_NS", "10"))
BASE = 0x4000_0000
PB, HB = PDW // 8, HDW // 8
FULL = (1 << PB) - 1
PSIZE = PB.bit_length() - 1
MASK = (1 << PDW) - 1
PATTERNS = sorted({p & FULL for p in (0b1010, 0b1001, 0b1110, 0b0001, 0b0110)} - {0, FULL})


async def _setup(dut: Any) -> tuple[ApbMaster, AhbSlave]:
    hclk = await bridge_clocks(dut.pclk_i, PCLK_NS, dut.hclk_i, HCLK_NS, CDC)
    dut.prst_ni.value = 0
    dut.hrst_ni.value = 0
    slave = AhbSlave(hclk, fabric_port(dut, "m", 0, AHB_S, AHB_S_RESP, 32, HDW), data_width=HDW)
    slave.start()
    master = ApbMaster(dut.pclk_i, fabric_port(dut, "s", 0, APB, APB_RESP, 32, PDW))
    await ClockCycles(dut.pclk_i, 4)
    dut.prst_ni.value = 1
    dut.hrst_ni.value = 1
    await ClockCycles(dut.pclk_i, 4)
    return master, slave


def _ahb_bytes(s: AhbSlave, addr: int, n: int) -> int:
    """N bytes of the AHB slave's memory from ADDR (little endian)."""
    out = 0
    for i in range(n):
        a = addr + i
        out |= ((s.mem.get(a & ~(HB - 1), 0) >> (8 * (a % HB))) & 0xFF) << (8 * i)
    return out


@cocotb.test(timeout_time=500, timeout_unit="us")
async def words_cross_with_offset(dut: Any) -> None:
    m, s = await _setup(dut)
    for addr in (BASE, BASE + 0x104, BASE + 0xFF0 + PB, BASE + 0x2000 - PB):
        data = (0x89AB_CDEF ^ addr) & MASK
        n = len(s.log)
        assert not await m.write(addr, data, strb=FULL)
        assert len(s.log) == n + 1  # exactly one AHB transfer
        a = s.log[-1]
        assert (a.addr, a.write, a.size, a.trans, a.burst) == (
            addr - OFFSET,
            True,
            PSIZE,
            NONSEQ,
            0,
        )
        assert _ahb_bytes(s, addr - OFFSET, PB) == data
        r = await m.read(addr)
        assert (r.data, r.err) == (data, False)
        assert len(s.log) == n + 2
        assert (s.log[-1].addr, s.log[-1].write, s.log[-1].size) == (addr - OFFSET, False, PSIZE)


@cocotb.test(timeout_time=500, timeout_unit="us")
async def apb_words_in_one_ahb_word(dut: Any) -> None:
    """Every APB word of an AHB word lands on (and reads back from) its own byte lanes."""
    m, _ = await _setup(dut)
    word = BASE + 0x200
    for k in range(HB // PB):
        assert not await m.write(word + k * PB, (0x11 * (k + 1)) & MASK, strb=FULL)
    for k in range(HB // PB):
        r = await m.read(word + k * PB)
        assert r.data == (0x11 * (k + 1)) & MASK, k


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def partial_strobes_become_byte_writes(dut: Any) -> None:
    """Every strobe pattern, on every APB word of an AHB word: one byte write per set strobe,
    only those bytes change; no strobe at all reaches nothing."""
    m, s = await _setup(dut)
    for k in range(HB // PB):
        addr = BASE + 0x300 + k * PB
        assert not await m.write(addr, MASK, strb=FULL)
        expect = MASK
        for strb in PATTERNS:
            assert not await m.write(addr, MASK, strb=FULL)
            n = len(s.log)
            assert not await m.write(addr, 0x0403_0201 & MASK, strb=strb)
            lanes = [b for b in range(PB) if strb >> b & 1]
            assert [(a.addr, a.size, a.trans) for a in s.log[n:]] == [
                (addr - OFFSET + b, 0, NONSEQ) for b in lanes
            ], (k, strb)
            expect = sum((b + 1 if b in lanes else 0xFF) << (8 * b) for b in range(PB))
            assert _ahb_bytes(s, addr - OFFSET, PB) == expect, (k, strb)
        n = len(s.log)
        assert not await m.write(addr, 0, strb=0)
        assert len(s.log) == n and _ahb_bytes(s, addr - OFFSET, PB) == expect


@cocotb.test(timeout_time=500, timeout_unit="us")
async def hresp_error_becomes_pslverr(dut: Any) -> None:
    m, s = await _setup(dut)
    bad = BASE + 0x400
    s.err_addrs.add((bad - OFFSET) & ~(HB - 1))
    assert await m.write(bad, 1, strb=FULL)
    assert (await m.read(bad)).err
    s.err_addrs.clear()
    assert not (await m.read(BASE + 0x500)).err
    assert not await m.write(BASE + 0x500, 2, strb=FULL)
    if PB >= 4:  # an error on only the first, or only the last, of three byte writes
        lanes = [0, 2, 3]
        for err_lane in (lanes[0], lanes[-1]):
            s.err_exact = {bad - OFFSET + err_lane}
            n = len(s.log)
            assert await m.write(bad, 0, strb=0b1101), err_lane
            # The other byte writes are still issued.
            assert [a.addr for a in s.log[n:]] == [bad - OFFSET + b for b in lanes], err_lane
        s.err_exact = set()
        assert not await m.write(bad, 0, strb=0b1101)


@cocotb.test(timeout_time=500, timeout_unit="us")
async def hprot_from_pprot(dut: Any) -> None:
    """HPROT = {2'b00, privileged = PPROT[0], data = !PPROT[2]}; HNONSEC = PPROT[1]."""
    m, s = await _setup(dut)
    for prot in range(8):
        await m.write(BASE + 0x600, 0, strb=FULL, prot=prot)
        await m.read(BASE + 0x600, prot=prot)
        expect = (prot & 1) << 1 | (1 - (prot >> 2 & 1)) | (prot >> 1 & 1) << 4
        assert [a.prot for a in s.log[-2:]] == [expect, expect], (prot, s.log[-1].prot)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def back_to_back_transfers(dut: Any) -> None:
    """APB transfers with no idle cycle between them (a new SETUP while the handshake's ack
    may still be high): every one completes with the right data."""
    m, _ = await _setup(dut)
    rng = random.Random(3)
    written: set[int] = set()
    ops = []
    for _ in range(24):
        addr = BASE + 0x700 + PB * rng.randrange(8)
        if rng.random() < 0.5 or addr not in written:
            ops.append((addr, 1, rng.getrandbits(PDW), FULL, 0))
            written.add(addr)
        else:
            ops.append((addr, 0, 0, 0, 0))
    expect: dict[int, int] = {}
    for (addr, write, data, _, _), r in zip(ops, await m.run(ops), strict=True):
        assert not r.err
        if write:
            expect[addr] = data
        else:
            assert r.data == expect[addr], hex(addr)
