# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb tests for bag_apb2axi (top: tests/rtl/tb/tb_apb2axi.sv).

The configuration comes from the environment: PDW / ADW data widths, LITE (1: the AXI
slave model is AXI4-Lite and sees only the Lite subset), OFFSET (AddrOffset), CDC
(AsyncCdc), PCLK_NS / ACLK_NS clock periods. The AXI slave applies random back-pressure on
every channel; an AXI monitor checks the handshake and single-beat rules throughout.
"""

import os
import random
from typing import Any

import cocotb
from axi_watch import AXI4_SIGS, LITE_SIGS, AxiWatch
from bfm import APB, APB_RESP, ApbMaster, bridge_clocks, fabric_port
from bfm_axi import AxiPortSlave, bus
from cocotb.triggers import ClockCycles

PDW = int(os.environ.get("PDW", "32"))
ADW = int(os.environ.get("ADW", "32"))
LITE = os.environ.get("LITE", "0") == "1"
OFFSET = int(os.environ.get("OFFSET", "0"), 0)
CDC = os.environ.get("CDC", "0") == "1"
PCLK_NS = int(os.environ.get("PCLK_NS", "10"))
ACLK_NS = int(os.environ.get("ACLK_NS", "10"))
BASE = 0x4000_0000
PB = PDW // 8
FULL = (1 << PB) - 1
MASK = (1 << PDW) - 1
PATTERNS = sorted({p & FULL for p in (0b1010, 0b1001, 0b1110, 0b0001, 0b0110)} - {0, FULL})


async def _setup(dut: Any, err: set[int] | None = None) -> tuple[ApbMaster, AxiPortSlave, AxiWatch]:
    aclk = await bridge_clocks(dut.pclk_i, PCLK_NS, dut.aclk_i, ACLK_NS, CDC)
    dut.prst_ni.value = 0
    dut.arst_ni.value = 0
    sigs = {s: f"m_{s}" for s in (LITE_SIGS if LITE else AXI4_SIGS)}
    slave = AxiPortSlave(aclk, bus(dut, sigs, LITE), LITE, ADW, err or set())
    slave.pause(seed=11)
    slave.start()
    watch = AxiWatch(dut, aclk, data_width=ADW, single_beat=True)
    master = ApbMaster(dut.pclk_i, fabric_port(dut, "s", 0, APB, APB_RESP, 32, PDW))
    await ClockCycles(dut.pclk_i, 4)
    dut.prst_ni.value = 1
    dut.arst_ni.value = 1
    await ClockCycles(dut.pclk_i, 4)
    return master, slave, watch


def _mem(s: AxiPortSlave, addr: int, n: int) -> int:
    return int.from_bytes(bytes(s.target.mem.get(addr + i, 0) for i in range(n)), "little")


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def words_cross_with_offset(dut: Any) -> None:
    m, s, w = await _setup(dut)
    for addr in (BASE, BASE + 0x104, BASE + 0x1000 - PB, (BASE + 0x1234) & ~(PB - 1)):
        data = (0x89AB_CDEF ^ addr) & MASK
        n = len(w.log)
        assert not await m.write(addr, data, strb=FULL)
        assert _mem(s, addr - OFFSET, PB) == data
        r = await m.read(addr)
        assert (r.data, r.err) == (data, False)
        assert [(ax.write, ax.addr) for ax in w.log[n:]] == [(True, addr - OFFSET),
                                                             (False, addr - OFFSET)]  # fmt: skip
    # Single beats of the APB width, ID 0, INCR, AxCACHE 0b0011.
    for ax in w.log:
        assert (ax.len, ax.burst, ax.cache, ax.id, ax.lock) == (0, 1, 0b0011, 0, 0), ax
        assert ax.size == PB.bit_length() - 1, ax


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def apb_words_in_one_axi_word(dut: Any) -> None:
    m, _, _ = await _setup(dut)
    word = BASE + 0x200
    for k in range(ADW // PDW):
        assert not await m.write(word + k * PB, (0x11 * (k + 1)) & MASK, strb=FULL)
    for k in range(ADW // PDW):
        r = await m.read(word + k * PB)
        assert r.data == (0x11 * (k + 1)) & MASK, k


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def pstrb_gives_wstrb(dut: Any) -> None:
    """Every strobe pattern on every APB word of an AXI word; PSTRB = 0 is an AXI write with
    no strobes (OKAY, nothing changes)."""
    m, s, w = await _setup(dut)
    for k in range(ADW // PDW):
        addr = BASE + 0x300 + k * PB
        assert not await m.write(addr, MASK, strb=FULL)
        expect = MASK
        for strb in PATTERNS:
            assert not await m.write(addr, MASK, strb=FULL)
            assert not await m.write(addr, 0x0403_0201 & MASK, strb=strb)
            expect = sum((b + 1 if strb >> b & 1 else 0xFF) << (8 * b) for b in range(PB))
            assert _mem(s, addr - OFFSET, PB) == expect, (k, strb)
        n = len(w.log)
        assert not await m.write(addr, 0, strb=0)
        assert len(w.log) == n + 1 and _mem(s, addr - OFFSET, PB) == expect
    assert _mem(s, BASE + 0x300 - OFFSET - 1, 1) == 0
    assert _mem(s, BASE + 0x300 - OFFSET + ADW // 8, 1) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def slverr_becomes_pslverr(dut: Any) -> None:
    bad = BASE + 0x400
    m, _, w = await _setup(dut, {bad - OFFSET})
    assert await m.write(bad, 1, strb=FULL)
    assert (await m.read(bad)).err
    assert w.b[-1].resp == 2 and w.r[-1].resp == 2
    assert not (await m.read(BASE + 0x800)).err
    assert not await m.write(BASE + 0x800, 2, strb=FULL)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def axprot_is_pprot(dut: Any) -> None:
    m, _, w = await _setup(dut)
    for prot in range(8):
        await m.write(BASE + 0x600, 0, strb=FULL, prot=prot)
        await m.read(BASE + 0x600, prot=prot)
        assert [ax.prot for ax in w.log[-2:]] == [prot, prot]


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def back_to_back_transfers(dut: Any) -> None:
    """APB transfers with no idle cycle between them, under AXI back-pressure."""
    m, _, w = await _setup(dut)
    rng = random.Random(5)
    written: set[int] = set()
    ops = []
    for _ in range(24):
        addr = BASE + 0x700 + PB * rng.randrange(8)
        if rng.random() < 0.5 or addr not in written:
            ops.append((addr, 1, rng.getrandbits(PDW), FULL, 0))
            written.add(addr)
        else:
            ops.append((addr, 0, 0, 0, 0))
    n = len(w.log)
    expect: dict[int, int] = {}
    for (addr, write, data, _, _), r in zip(ops, await m.run(ops), strict=True):
        assert not r.err
        if write:
            expect[addr] = data
        else:
            assert r.data == expect[addr], hex(addr)
    assert len(w.log) - n == len(ops)  # one AXI transfer each
