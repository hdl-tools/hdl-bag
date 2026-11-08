# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb smoke tests for bag_apb_mux (top: tests/rtl/tb/tb_apb_mux.sv)."""

import os
from typing import Any

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from hdl_bag.tests_gen.bfm import APB, APB_RESP, ApbMaster, ApbResult, ApbSlave, fabric_port

MISS_TO_DEFAULT = os.environ.get("MISS_TO_DEFAULT") == "1"
NONSEC = 0b010


TRACE: list[tuple[int, int]] = []


async def _setup(dut: Any) -> tuple[ApbMaster, list[ApbSlave]]:
    Clock(dut.clk_i, 10, unit="ns").start()
    dut.alias_en_i.value = 0
    TRACE.clear()
    slaves = [
        ApbSlave(dut.clk_i, fabric_port(dut, "m", i, APB, APB_RESP, 32, 32), trace=TRACE, ident=i)
        for i in range(3)
    ]
    for s in slaves:
        s.start()
    master = ApbMaster(dut.clk_i, fabric_port(dut, "s", 0, APB, APB_RESP, 32, 32))
    await ClockCycles(dut.clk_i, 2)
    return master, slaves


@cocotb.test(timeout_time=200, timeout_unit="us")
async def decode_hits_each_region(dut: Any) -> None:
    m, s = await _setup(dut)
    hits = ((0x1000_0000, 0), (0x1000_0FFC, 0), (0x1000_3000, 1), (0x1000_3FFC, 1))
    for addr, _ in hits:
        assert not await m.write(addr, addr ^ 0x5A5A_5A5A, prot=0b101)
        r = await m.read(addr, prot=NONSEC)  # non-secure access to a normal region is fine
        assert (r.data, r.err) == (addr ^ 0x5A5A_5A5A, False)
    assert [x for addr, slave in hits for x in ((slave, addr), (slave, addr))] == TRACE
    assert [a.prot for a in s[0].log] == [0b101, NONSEC, 0b101, NONSEC]  # PPROT forwarded
    assert not await m.write(0x1000_2010, 7, prot=0b101)  # s2: write-only, secure (PPROT[1]=0)
    assert s[2].mem[0x1000_2010] == 7
    s[1].mem[0x1000_1004] = 0xC0FF_EE01  # s1 region 1 is read-only
    assert (await m.read(0x1000_1004)) == ApbResult(0xC0FF_EE01, False)
    assert TRACE[-2:] == [(2, 0x1000_2010), (1, 0x1000_1004)]


@cocotb.test(timeout_time=200, timeout_unit="us")
async def ro_wo_and_secure_are_enforced(dut: Any) -> None:
    m, s = await _setup(dut)
    errs = [
        await m.write(0x1000_1000, 1),  # read-only region
        (await m.read(0x1000_2000)).err,  # write-only region
        await m.write(0x1000_2000, 1, prot=NONSEC),  # secure-only, non-secure access
    ]
    if MISS_TO_DEFAULT:  # blocked accesses go to the default slave (s0) instead
        assert errs == [False] * 3
        assert [a.addr for a in s[0].log] == [0x1000_1000, 0x1000_2000, 0x1000_2000]
        assert not s[1].log and not s[2].log
    else:
        assert errs == [True] * 3
        assert not any(x.log for x in s), "blocked accesses must not reach a slave"


@cocotb.test(timeout_time=200, timeout_unit="us")
async def miss_response_or_default_slave(dut: Any) -> None:
    m, s = await _setup(dut)
    r = await m.read(0x3000_0000)
    if MISS_TO_DEFAULT:
        assert not r.err and s[0].log[-1].addr == 0x3000_0000
    else:
        assert r.err and r.data == 0 and not s[0].log


@cocotb.test(timeout_time=200, timeout_unit="us")
async def alias_follows_its_enable(dut: Any) -> None:
    m, _s = await _setup(dut)
    assert not await m.write(0x1000_0010, 0x1234)
    assert not await m.write(0x1000_0FFC, 0x5678)
    TRACE.clear()
    r = await m.read(0x0000_0010)  # alias off: a miss
    if MISS_TO_DEFAULT:
        assert (r.data, r.err, TRACE) == (0, False, [(0, 0x0000_0010)])
    else:
        assert r.err and not TRACE
    dut.alias_en_i.value = 1
    TRACE.clear()
    assert (await m.read(0x0000_0010)) == ApbResult(0x1234, False)
    assert (await m.read(0x0000_0FFC)) == ApbResult(0x5678, False)  # top of the window
    assert TRACE == [(0, 0x1000_0010), (0, 0x1000_0FFC)]
    r = await m.read(0x0000_1000)  # one past the window: a miss
    assert r.err != MISS_TO_DEFAULT


@cocotb.test(timeout_time=200, timeout_unit="us")
async def slave_error_and_strobes_pass_through(dut: Any) -> None:
    m, s = await _setup(dut)
    s[0].err_addrs.add(0x1000_0100)
    assert await m.write(0x1000_0100, 1)
    assert not await m.write(0x1000_0200, 0xAABB_CCDD)
    assert not await m.write(0x1000_0200, 0x0000_1100, strb=0b0010)
    assert (await m.read(0x1000_0200)).data == 0xAABB_11DD


@cocotb.test(timeout_time=200, timeout_unit="us")
async def pready_and_pslverr_follow_the_amba_recommendations(dut: Any) -> None:
    """D61 (owner): towards the master, PREADY is steady high while PSEL is low (in reset
    too), so it never follows the address decode or a slave while idle; and PSLVERR is low
    outside an access phase with PREADY high, as AMBA recommends. Neither a miss address nor
    a slave's stray PSLVERR may reach the master; in the setup phase PSLVERR stays low; an
    error in a completing access still reaches it."""
    _m, s = await _setup(dut)
    for slave in s:
        slave.stop()  # the test drives the slave-side responses itself
    dut.s_psel_i.value, dut.s_penable_i.value = 0, 0
    dut.s_paddr_i.value = 0x3000_0000  # a miss: the mux's own error path
    dut.m_pslverr_i.value = 0b111  # every slave drives PSLVERR outside a transfer
    dut.m_pready_i.value = 0b000  # no slave ready: an idle PREADY must still be high
    seen = []
    for addr in (0x3000_0000, 0x1000_0000):  # a miss, then a hit on s0
        dut.s_paddr_i.value = addr
        for psel, penable in ((0, 0), (1, 0)):  # idle, then the setup phase
            dut.s_psel_i.value, dut.s_penable_i.value = psel, penable
            await ClockCycles(dut.clk_i, 1)
            await ReadOnly()
            seen.append((hex(addr), psel, penable, int(dut.s_pready_o.value),
                         int(dut.s_pslverr_o.value)))  # fmt: skip
            await RisingEdge(dut.clk_i)
    assert [x for x in seen if x[4]] == [], f"PSLVERR outside an access phase: {seen}"
    assert [x for x in seen if x[1] == 0 and not x[3]] == [], f"PREADY low while idle: {seen}"
    # In the access phase with PREADY high, the error does reach the master.
    dut.m_pready_i.value = 0b111
    dut.s_psel_i.value, dut.s_penable_i.value = 1, 1
    await ReadOnly()
    assert (int(dut.s_pready_o.value), int(dut.s_pslverr_o.value)) == (1, 1)
