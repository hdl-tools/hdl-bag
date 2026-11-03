# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb smoke tests for bag_ahb_lite_bus (top: tests/rtl/tb/tb_ahb_lite_bus.sv)."""

import os
import random
from typing import Any

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Combine

from hdl_bag.tests_gen.bfm import (
    AHB_M,
    AHB_M_RESP,
    AHB_S,
    AHB_S_RESP,
    BUSY,
    NONSEQ,
    SEQ,
    AhbMaster,
    AhbResult,
    AhbSlave,
    Beat,
    fabric_port,
)

FIXED = os.environ.get("FIXED_PRIORITY") == "1"
INCR4 = 0b011


async def _setup(
    dut: Any, max_wait: int = 2
) -> tuple[list[AhbMaster], list[AhbSlave], list[tuple[int, int]]]:
    Clock(dut.clk_i, 10, unit="ns").start()
    dut.alias_en_i.value = 0
    dut.rst_ni.value = 0
    trace: list[tuple[int, int]] = []
    slaves = [
        AhbSlave(dut.clk_i, fabric_port(dut, "m", i, AHB_S, AHB_S_RESP, 32, 32),
                 max_wait=max_wait, trace=trace, ident=i)
        for i in range(3)
    ]  # fmt: skip
    for s in slaves:
        s.start()
    masters = [
        AhbMaster(dut.clk_i, fabric_port(dut, "s", i, AHB_M, AHB_M_RESP, 32, 32)) for i in range(3)
    ]
    await ClockCycles(dut.clk_i, 3)
    dut.rst_ni.value = 1
    await ClockCycles(dut.clk_i, 2)
    return masters, slaves, trace


@cocotb.test(timeout_time=200, timeout_unit="us")
async def single_master_reaches_every_slave(dut: Any) -> None:
    m, s, _ = await _setup(dut)
    for addr, slave in ((0x2000_0000, 0), (0x2000_3004, 1), (0x2000_2008, 2)):
        assert not await m[0].write(addr, addr + 1)
        r = await m[0].read(addr)
        assert (r.data, r.err) == (addr + 1, False)
        assert s[slave].log[-1].addr == addr
    assert not (await m[0].read(0x2000_1000)).err  # read-only region: reads are fine


@cocotb.test(timeout_time=200, timeout_unit="us")
async def blocked_accesses_get_error_or_default_slave(dut: Any) -> None:
    m, s, trace = await _setup(dut)
    # Every error below is also checked as the two-cycle ERROR shape by AhbMaster.
    assert await m[0].write(0x3000_0000, 1)  # miss
    assert await m[0].write(0x2000_1000, 1)  # read-only region
    assert (await m[0].read(0x3000_0004)).err  # read miss
    assert await m[0].write(0x2000_2000, 1, nonsec=1)  # secure-only region, non-secure
    assert not await m[0].write(0x2000_2004, 5, nonsec=0)  # ... secure is fine
    assert await m[2].write(0x2000_0000, 1)  # m2 is read-only towards s0
    s[0].mem[0x2000_0000] = 0xFACE_0000
    assert (await m[2].read(0x2000_0000)) == AhbResult(0xFACE_0000, False)
    assert (await m[2].read(0x2000_2004)).err  # m2 is write-only towards s2
    assert not await m[2].write(0x2000_2008, 3)
    assert trace == [(2, 0x2000_2004), (0, 0x2000_0000), (2, 0x2000_2008)], (
        "blocked accesses reach no slave"
    )
    # m1 is routed to its default slave port (s0) instead of an error.
    trace.clear()
    assert not (await m[1].read(0x2000_2000)).err  # m1 has no access to s2
    assert not await m[1].write(0x2000_3000, 9)  # normal access
    assert not (await m[1].read(0x3000_0000)).err  # miss
    assert trace == [(0, 0x2000_2000), (1, 0x2000_3000), (0, 0x3000_0000)]
    s[1].err_addrs.add(0x2000_3010)
    assert await m[0].write(0x2000_3010, 1)  # slave error passes through
    assert not await m[0].write(0x2000_3014, 1)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def attributes_reach_the_slave(dut: Any) -> None:
    m, s, _ = await _setup(dut)
    for size, prot, nonsec, burst in ((0, 0b0000, 1, 0), (1, 0b0011, 0, 1), (2, 0b1110, 1, 0)):
        addr = 0x2000_3020 + (1 << size)
        await m[0].run(
            [Beat(addr, True, 0xA5 << (8 * (addr % 4)), size, burst, NONSEQ, prot, nonsec)]
        )
        a = s[1].log[-1]
        assert (a.addr, a.size, a.prot) == (addr, size, prot | nonsec << 4)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def alias_belongs_to_its_master(dut: Any) -> None:
    m, s, _ = await _setup(dut)
    assert not await m[0].write(0x2000_0040, 0xCAFE)
    assert (await m[0].read(0x40)).err
    dut.alias_en_i.value = 1
    r = await m[0].read(0x40)
    assert (r.data, r.err) == (0xCAFE, False)
    assert s[0].log[-1].addr == 0x2000_0040
    assert not (await m[1].read(0x40)).err  # not m1's alias: a miss, to m1's default slave
    assert s[0].log[-1].addr == 0x40


def _incr4(base: int, words: list[int]) -> list[Beat]:
    return [
        Beat(base + 4 * i, True, w, burst=INCR4, trans=NONSEQ if i == 0 else SEQ)
        for i, w in enumerate(words)
    ]


async def _traffic(m: AhbMaster, base: int, n: int) -> dict[int, int]:
    """Random singles and INCR4 bursts in [base, base + 0x800); returns what was written."""
    written: dict[int, int] = {}
    for _ in range(n):
        addr = base + 16 * random.randrange(0x800 // 16)
        words = [random.getrandbits(32) for _ in range(4)]
        if random.random() < 0.5:
            results = await m.run(_incr4(addr, words))
        else:
            results = await m.run([Beat(addr, True, words[0])])
            words = words[:1]
        assert not any(r.err for r in results)
        written.update({addr + 4 * i: w for i, w in enumerate(words)})
    return written


@cocotb.test(timeout_time=200, timeout_unit="us")
async def concurrent_masters_keep_their_data(dut: Any) -> None:
    m, _, _ = await _setup(dut)
    bases = (0x2000_0000, 0x2000_3000, 0x2000_3800)  # m0 -> s0, m1 / m2 -> s1
    done = [cocotb.start_soon(_traffic(m[i], bases[i], 30)) for i in range(3)]
    await Combine(*done)
    for i, task in enumerate(done):
        for addr, data in task.result().items():
            r = await m[i].read(addr)
            assert (r.data, r.err) == (data, False), hex(addr)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def burst_keeps_the_bus(dut: Any) -> None:
    m, _, trace = await _setup(dut)
    burst = cocotb.start_soon(m[0].run(_incr4(0x2000_0100, [1, 2, 3, 4])))
    await ClockCycles(dut.clk_i, 1)  # m0 owns the bus; m1 and m2 keep asking for it
    others = [
        cocotb.start_soon(
            m[i].run([Beat(0x2000_3000 + 0x100 * i + 4 * k, True, k) for k in range(6)])
        )
        for i in (1, 2)
    ]
    await Combine(burst, *others)
    order = [addr for _, addr in trace]
    first = order.index(0x2000_0100)
    assert order[first : first + 4] == [0x2000_0100 + 4 * i for i in range(4)], order


@cocotb.test(timeout_time=200, timeout_unit="us")
async def arbitration_order(dut: Any) -> None:
    m, _, trace = await _setup(dut, max_wait=0)
    assert not await m[1].write(0x2000_3000, 0)  # m1 was granted last
    trace.clear()
    tasks = [cocotb.start_soon(m[i].write(0x2000_3010 + 4 * i, i)) for i in range(3)]
    await Combine(*tasks)
    order = [(addr - 0x2000_3010) // 4 for _, addr in trace]
    # Fixed priority m1 > m2 > m0; round-robin continues after m1.
    assert order == ([1, 2, 0] if FIXED else [2, 0, 1]), order


@cocotb.test(timeout_time=200, timeout_unit="us")
async def concurrent_reads_return_their_own_data(dut: Any) -> None:
    m, s, _ = await _setup(dut)
    for k in range(16):
        s[0].mem[0x2000_0000 + 4 * k] = 0x0000_0000 | k
        s[1].mem[0x2000_3000 + 4 * k] = 0x1000_0000 | k
        s[1].mem[0x2000_3800 + 4 * k] = 0x2000_0000 | k

    async def reads(i: int, base: int, tag: int) -> None:
        for k in random.sample(range(16), 16):
            r = await m[i].read(base + 4 * k)
            assert r == AhbResult(tag | k, False), (i, k, r)

    await Combine(*(cocotb.start_soon(reads(i, b, t)) for i, b, t in
                    ((0, 0x2000_0000, 0), (1, 0x2000_3000, 0x1000_0000),
                     (2, 0x2000_3800, 0x2000_0000))))  # fmt: skip


@cocotb.test(timeout_time=200, timeout_unit="us")
async def locked_sequence_keeps_the_bus(dut: Any) -> None:
    m, _, trace = await _setup(dut)
    locked = [Beat(0x2000_0200 + 4 * k, True, k, lock=1) for k in range(3)]
    task = cocotb.start_soon(m[0].run(locked))
    await ClockCycles(dut.clk_i, 1)
    others = [
        cocotb.start_soon(
            m[i].run([Beat(0x2000_3000 + 0x100 * i + 4 * k, True, k) for k in range(4)])
        )
        for i in (1, 2)
    ]
    await Combine(task, *others)
    order = [addr for _, addr in trace]
    first = order.index(0x2000_0200)
    assert order[first : first + 3] == [0x2000_0200 + 4 * k for k in range(3)], order


@cocotb.test(timeout_time=200, timeout_unit="us")
async def busy_and_early_burst_termination(dut: Any) -> None:
    m, s, trace = await _setup(dut)
    beats = _incr4(0x2000_0300, [1, 2, 3, 4])
    beats.insert(2, Beat(0x2000_0308, True, 0, burst=INCR4, trans=BUSY))  # BUSY before beat 3
    burst = cocotb.start_soon(m[0].run(beats))
    await ClockCycles(dut.clk_i, 1)  # contenders keep asking while m0 is BUSY mid-burst
    others = [
        cocotb.start_soon(
            m[i].run([Beat(0x2000_3000 + 0x100 * i + 4 * k, True, k) for k in range(6)])
        )
        for i in (1, 2)
    ]
    await Combine(burst, *others)
    assert not any(r.err for r in burst.result())
    assert [s[0].mem[0x2000_0300 + 4 * k] for k in range(4)] == [1, 2, 3, 4]
    order = [addr for _, addr in trace]
    first = order.index(0x2000_0300)
    assert order[first : first + 4] == [0x2000_0300 + 4 * k for k in range(4)], order
    trace.clear()
    # INCR4 terminated after two beats by a new NONSEQ, under contention.
    cut = [*_incr4(0x2000_0400, [5, 6, 7, 8])[:2], Beat(0x2000_3100, True, 9)]
    other = cocotb.start_soon(m[2].run([Beat(0x2000_3800 + 4 * k, True, k) for k in range(3)]))
    assert not any(r.err for r in await m[0].run(cut))
    await other
    assert s[0].mem[0x2000_0404] == 6 and s[1].mem[0x2000_3100] == 9


@cocotb.test(timeout_time=200, timeout_unit="us")
async def held_transfer_keeps_its_attributes(dut: Any) -> None:
    """A non-secure access that loses arbitration is still blocked when issued later."""
    m, s, _trace = await _setup(dut)
    for _ in range(8):
        loser = cocotb.start_soon(m[0].write(0x2000_2040, 0xBAD, nonsec=1))
        winners = [cocotb.start_soon(m[i].write(0x2000_3040 + 4 * i, i)) for i in (1, 2)]
        assert await loser
        await Combine(*winners)
    assert 0x2000_2040 not in s[2].mem and all(a.addr != 0x2000_2040 for a in s[2].log)
