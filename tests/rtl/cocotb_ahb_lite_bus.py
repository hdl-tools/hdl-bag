# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb smoke tests for bag_ahb_lite_bus (top: tests/rtl/tb/tb_ahb_lite_bus.sv)."""

import os
import random
from typing import Any

import cocotb
from bfm import (
    AHB_M,
    AHB_M_RESP,
    AHB_S,
    AHB_S_RESP,
    NONSEQ,
    SEQ,
    AhbMaster,
    AhbSlave,
    Beat,
    fabric_port,
)
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Combine

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


@cocotb.test()
async def single_master_reaches_every_slave(dut: Any) -> None:
    m, s, _ = await _setup(dut)
    for addr, slave in ((0x2000_0000, 0), (0x2000_3004, 1), (0x2000_2008, 2)):
        assert not await m[0].write(addr, addr + 1)
        r = await m[0].read(addr)
        assert (r.data, r.err) == (addr + 1, False)
        assert s[slave].log[-1].addr == addr
    assert not (await m[0].read(0x2000_1000)).err  # read-only region: reads are fine


@cocotb.test()
async def errors_are_two_cycle_hresp(dut: Any) -> None:
    m, s, trace = await _setup(dut)
    assert await m[0].write(0x3000_0000, 1)  # miss
    assert await m[0].write(0x2000_1000, 1)  # read-only region
    assert await m[0].write(0x2000_2000, 1, nonsec=1)  # secure-only region
    assert (await m[1].read(0x2000_2000)).err  # m1 has no access to s2
    assert await m[2].write(0x2000_0000, 1)  # m2 is read-only towards s0
    assert not (await m[2].read(0x2000_0000)).err
    assert trace == [(0, 0x2000_0000)], "only the allowed access reaches a slave"
    s[1].err_addrs.add(0x2000_3010)
    assert await m[0].write(0x2000_3010, 1)  # slave error passes through
    assert not await m[0].write(0x2000_3014, 1)


@cocotb.test()
async def alias_belongs_to_its_master(dut: Any) -> None:
    m, s, _ = await _setup(dut)
    assert not await m[0].write(0x2000_0040, 0xCAFE)
    assert (await m[0].read(0x40)).err
    dut.alias_en_i.value = 1
    r = await m[0].read(0x40)
    assert (r.data, r.err) == (0xCAFE, False)
    assert s[0].log[-1].addr == 0x2000_0040
    assert (await m[1].read(0x40)).err  # the alias is m0's only


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


@cocotb.test()
async def concurrent_masters_keep_their_data(dut: Any) -> None:
    m, _, _ = await _setup(dut)
    bases = (0x2000_0000, 0x2000_3000, 0x2000_3800)  # m0 -> s0, m1 / m2 -> s1
    done = [cocotb.start_soon(_traffic(m[i], bases[i], 30)) for i in range(3)]
    await Combine(*done)
    for i, task in enumerate(done):
        for addr, data in task.result().items():
            r = await m[i].read(addr)
            assert (r.data, r.err) == (data, False), hex(addr)


@cocotb.test()
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


@cocotb.test()
async def arbitration_order(dut: Any) -> None:
    m, _, trace = await _setup(dut, max_wait=0)
    assert not await m[1].write(0x2000_3000, 0)  # m1 was granted last
    trace.clear()
    tasks = [cocotb.start_soon(m[i].write(0x2000_3010 + 4 * i, i)) for i in range(3)]
    await Combine(*tasks)
    order = [(addr - 0x2000_3010) // 4 for _, addr in trace]
    # Fixed priority m1 > m2 > m0; round-robin continues after m1.
    assert order == ([1, 2, 0] if FIXED else [2, 0, 1]), order
