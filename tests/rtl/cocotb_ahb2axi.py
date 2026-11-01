# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb tests for bag_ahb2axi (top: tests/rtl/tb/tb_ahb2axi.sv).

The configuration comes from the environment: HDW / ADW data widths, LITE (1: the AXI
slave model is AXI4-Lite), OFFSET (AddrOffset), CDC (AsyncCdc), HCLK_NS / ACLK_NS clock
periods. The AXI slave applies random back-pressure on every channel; an AXI monitor checks
the handshake and single-beat rules throughout.
"""

import os
import random
from typing import Any

import cocotb
from axi_watch import AXI4_SIGS, LITE_SIGS, AxiWatch
from bfm import (
    AHB_M,
    AHB_M_RESP,
    BUSY,
    IDLE,
    NONSEQ,
    SEQ,
    AhbMaster,
    Beat,
    bridge_clocks,
    fabric_port,
    value,
)
from bfm_axi import AxiPortSlave, bus
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

HDW = int(os.environ.get("HDW", "32"))
ADW = int(os.environ.get("ADW", "32"))
LITE = os.environ.get("LITE", "0") == "1"
OFFSET = int(os.environ.get("OFFSET", "0"), 0)
CDC = os.environ.get("CDC", "0") == "1"
HCLK_NS = int(os.environ.get("HCLK_NS", "10"))
ACLK_NS = int(os.environ.get("ACLK_NS", "10"))
BASE = 0x4000_0000
HB, AB = HDW // 8, ADW // 8
HSIZE = HB.bit_length() - 1
PARTS = max(1, HB // AB)  # AXI transfers per full AHB beat
INCR4, INCR = 0b011, 0b001


async def _setup(dut: Any, err: set[int] | None = None) -> tuple[AhbMaster, AxiPortSlave, AxiWatch]:
    aclk = await bridge_clocks(dut.hclk_i, HCLK_NS, dut.aclk_i, ACLK_NS, CDC)
    dut.hrst_ni.value = 0
    dut.arst_ni.value = 0
    dut.s_hsel_i.value = 1
    dut.s_hready_gate_i.value = 0
    sigs = {s: f"m_{s}" for s in (LITE_SIGS if LITE else AXI4_SIGS)}
    slave = AxiPortSlave(aclk, bus(dut, sigs, LITE), LITE, ADW, err or set())
    slave.pause(seed=13)
    slave.start()
    watch = AxiWatch(dut, aclk, data_width=ADW, single_beat=True)
    master = AhbMaster(dut.hclk_i, fabric_port(dut, "s", 0, AHB_M, AHB_M_RESP, 32, HDW))
    await ClockCycles(dut.hclk_i, 4)
    dut.hrst_ni.value = 1
    dut.arst_ni.value = 1
    await ClockCycles(dut.hclk_i, 4)
    return master, slave, watch


def _mem(s: AxiPortSlave, addr: int, n: int) -> int:
    return int.from_bytes(bytes(s.target.mem.get(addr + i, 0) for i in range(n)), "little")


def _lane(addr: int, value: int) -> int:
    return value << (8 * (addr % HB))


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def words_cross_with_offset(dut: Any) -> None:
    m, s, w = await _setup(dut)
    for addr in (BASE, BASE + 0x104, BASE + 0xFFC, BASE + 0x2000 - 4):
        data = 0x89AB_CDEF ^ addr & 0xFFFF_FFFF
        n = len(w.log)
        assert not await m.write(addr, _lane(addr, data), size=2)
        assert _mem(s, addr - OFFSET, 4) == data
        r = await m.read(addr, size=2)
        assert not r.err and (r.data >> (8 * (addr % HB))) & 0xFFFF_FFFF == data
        assert [(ax.write, ax.addr) for ax in w.log[n:]] == [(True, addr - OFFSET),
                                                             (False, addr - OFFSET)]  # fmt: skip
    for ax in w.log:
        assert (ax.len, ax.burst, ax.id, ax.lock) == (0, 1, 0, 0), ax
        assert ax.size == 2, ax


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def narrow_accesses_on_every_lane(dut: Any) -> None:
    """Byte and halfword writes, byte and halfword reads, at every offset of the AHB word."""
    m, s, _ = await _setup(dut)
    word = BASE + 0x340
    for b in range(HB):
        assert not await m.write(word + b, _lane(word + b, 0xA0 + b), size=0)
    expect = bytes(0xA0 + b for b in range(HB))
    assert _mem(s, word - OFFSET, HB) == int.from_bytes(expect, "little")
    for b in range(HB):
        r = await m.read(word + b, size=0)
        assert (r.data >> (8 * b)) & 0xFF == 0xA0 + b, b
    for b in range(0, HB, 2):
        assert not await m.write(word + b, _lane(word + b, 0x5A00 + b), size=1)
        r = await m.read(word + b, size=1)
        assert (r.data >> (8 * b)) & 0xFFFF == 0x5A00 + b, b
    assert _mem(s, word - OFFSET - 1, 1) == 0 and _mem(s, word - OFFSET + HB, 1) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def full_beat_and_split(dut: Any) -> None:
    """A full AHB beat is one AXI transfer, or several when the AXI bus is narrower."""
    m, s, w = await _setup(dut)
    addr = BASE + 0x500
    data = int.from_bytes(bytes(range(1, HB + 1)), "little")
    n = len(w.log)
    assert not await m.write(addr, data, size=HSIZE)
    assert [ax.addr for ax in w.log[n:]] == [addr - OFFSET + i * AB for i in range(PARTS)]
    assert all(ax.size == min(HB, AB).bit_length() - 1 for ax in w.log[n:])
    assert _mem(s, addr - OFFSET, HB) == data
    r = await m.read(addr, size=HSIZE)
    assert (r.data, r.err) == (data, False)
    assert len(w.log) - n == 2 * PARTS


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def slverr_becomes_hresp_error(dut: Any) -> None:
    """An error on any part of a beat (only the first, only the last) is HRESP ERROR, and
    every part is still issued; a burst continues after an erroring beat."""
    m, s, w = await _setup(dut)
    addr = BASE + 0x800
    for part in sorted({0, PARTS - 1}):
        s.target.err_exact = {addr - OFFSET + part * AB}
        for write in (True, False):
            n = len(w.log)
            if write:
                assert await m.write(addr, 0, size=HSIZE), part
            else:
                assert (await m.read(addr, size=HSIZE)).err, part
            assert len(w.log) - n == PARTS, part
    s.target.err_exact = {BASE + 0x900 - OFFSET + 8}  # beat 2 of an INCR4
    beats = [Beat(BASE + 0x900 + 4 * i, True, _lane(BASE + 0x900 + 4 * i, i), size=2,
                  burst=INCR4, trans=NONSEQ if i == 0 else SEQ) for i in range(4)]  # fmt: skip
    assert [r.err for r in await m.run(beats)] == [False, False, True, False]
    s.target.err_exact = set()
    assert not await m.write(BASE + 0x900, 0, size=HSIZE)
    assert not (await m.read(BASE + 0x900, size=HSIZE)).err


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def axprot_axcache_from_hprot(dut: Any) -> None:
    """AxPROT = {!HPROT[0], HNONSEC, HPROT[1]}; AxCACHE = {2'b00, HPROT[3], HPROT[2]}, on
    writes and reads."""
    m, _, w = await _setup(dut)
    for hprot in range(16):
        for nonsec in (0, 1):
            n = len(w.log)
            await m.write(BASE + 0x600, 0, size=2, prot=hprot, nonsec=nonsec)
            await m.read(BASE + 0x600, size=2, prot=hprot, nonsec=nonsec)
            prot = (1 - (hprot & 1)) << 2 | nonsec << 1 | (hprot >> 1 & 1)
            assert [(ax.write, ax.prot, ax.cache) for ax in w.log[n:]] == [
                (True, prot, hprot >> 2), (False, prot, hprot >> 2)], (hprot, nonsec)  # fmt: skip


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def bursts_busy_and_mixed_pipelines(dut: Any) -> None:
    """INCR4 and halfword INCR bursts, BUSY inside an INCR burst (no transfer), and reads and
    writes mixed back to back in the AHB pipeline."""
    m, s, w = await _setup(dut)
    base = BASE + 0x700
    beats = [Beat(base + 4 * i, True, _lane(base + 4 * i, i + 1), size=2, burst=INCR4,
                  trans=NONSEQ if i == 0 else SEQ) for i in range(4)]  # fmt: skip
    n = len(w.log)
    assert not any(r.err for r in await m.run(beats))
    assert [ax.addr for ax in w.log[n:]] == [base - OFFSET + 4 * i for i in range(4)]
    assert [_mem(s, base - OFFSET + 4 * i, 4) for i in range(4)] == [1, 2, 3, 4]
    # Halfword INCR with a BUSY between the second and third beats.
    hbase = BASE + 0x780
    seq = [Beat(hbase + 2 * i, True, _lane(hbase + 2 * i, 0x100 + i), size=1, burst=INCR,
                trans=NONSEQ if i == 0 else SEQ) for i in range(4)]  # fmt: skip
    seq.insert(2, Beat(hbase + 4, True, 0, size=1, burst=INCR, trans=BUSY))
    n = len(w.log)
    assert not any(r.err for r in await m.run(seq))
    assert [ax.addr for ax in w.log[n:]] == [hbase - OFFSET + 2 * i for i in range(4)]
    assert [_mem(s, hbase - OFFSET + 2 * i, 2) for i in range(4)] == [0x100 + i for i in range(4)]
    # Reads and writes interleaved, pipelined.
    rng = random.Random(9)
    addrs = [BASE + 0xA00 + 4 * k for k in range(6)]
    mix, model = [], {}
    for k, a in enumerate(addrs):
        model[a] = rng.getrandbits(32)
        mix.append(Beat(a, True, _lane(a, model[a]), size=2))
        if k:
            mix.append(Beat(addrs[k - 1], False, size=2))
    results = await m.run(mix)
    for b, r in zip(mix, results, strict=True):
        assert not r.err
        if not b.write:
            assert (r.data >> (8 * (b.addr % HB))) & 0xFFFF_FFFF == model[b.addr], hex(b.addr)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def hsel_and_hready_gate_acceptance(dut: Any) -> None:
    """An address phase with HSEL low, or while HREADY is low (another slave's wait state),
    is not taken; it is taken in the cycle HREADY returns."""
    m, s, w = await _setup(dut)
    p = m.p
    addr = BASE + 0xB00

    async def idle() -> None:
        p["htrans"] = IDLE
        for _ in range(40):  # long enough for any AXI transfer to show (CDC included)
            await RisingEdge(dut.hclk_i)

    await RisingEdge(dut.hclk_i)
    p["haddr"], p["hwrite"], p["hsize"], p["htrans"] = addr, 1, 2, NONSEQ
    dut.s_hsel_i.value = 0
    await RisingEdge(dut.hclk_i)
    dut.s_hsel_i.value = 1
    await idle()
    assert not w.log, "a transfer with HSEL low was taken"
    # HREADY held low by another slave: the address phase waits.
    dut.s_hready_gate_i.value = 1
    p["haddr"], p["hwrite"], p["hsize"], p["htrans"] = addr, 1, 2, NONSEQ
    for _ in range(3):
        await RisingEdge(dut.hclk_i)
    assert not w.log, "a transfer was taken while HREADY was low"
    dut.s_hready_gate_i.value = 0
    await RisingEdge(dut.hclk_i)  # taken here
    p["htrans"], p["hwdata"] = IDLE, _lane(addr, 0xC0FFEE)
    while True:
        await ReadOnly()
        if value(dut.s_hready_o):
            break
        await RisingEdge(dut.hclk_i)
    await RisingEdge(dut.hclk_i)
    await idle()
    assert [(ax.write, ax.addr) for ax in w.log] == [(True, addr - OFFSET)]
    assert _mem(s, addr - OFFSET, 4) == 0xC0FFEE
