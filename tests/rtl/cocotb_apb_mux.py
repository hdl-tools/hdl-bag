# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb smoke tests for bag_apb_mux (top: tests/rtl/tb/tb_apb_mux.sv)."""

import os
from typing import Any

import cocotb
from bfm import APB, APB_RESP, ApbMaster, ApbSlave, fabric_port
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

MISS_TO_DEFAULT = os.environ.get("MISS_TO_DEFAULT") == "1"
NONSEC = 0b010


async def _setup(dut: Any) -> tuple[ApbMaster, list[ApbSlave]]:
    Clock(dut.clk_i, 10, unit="ns").start()
    dut.alias_en_i.value = 0
    slaves = [
        ApbSlave(dut.clk_i, fabric_port(dut, "m", i, APB, APB_RESP, 32, 32)) for i in range(3)
    ]
    for s in slaves:
        s.start()
    master = ApbMaster(dut.clk_i, fabric_port(dut, "s", 0, APB, APB_RESP, 32, 32))
    await ClockCycles(dut.clk_i, 2)
    return master, slaves


@cocotb.test()
async def decode_hits_each_region(dut: Any) -> None:
    m, s = await _setup(dut)
    for addr, slave in ((0x1000_0000, 0), (0x1000_0FFC, 0), (0x1000_3000, 1), (0x1000_3FFC, 1)):
        assert not await m.write(addr, addr ^ 0x5A5A_5A5A)
        r = await m.read(addr)
        assert (r.data, r.err) == (addr ^ 0x5A5A_5A5A, False)
        assert s[slave].log[-1].addr == addr
    assert not await m.write(0x1000_2010, 7)  # s2 is write-only and secure
    assert s[2].mem[0x1000_2010] == 7
    assert (await m.read(0x1000_1004)).err is False  # s1 region 1 is read-only


@cocotb.test()
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


@cocotb.test()
async def miss_response_or_default_slave(dut: Any) -> None:
    m, s = await _setup(dut)
    r = await m.read(0x3000_0000)
    if MISS_TO_DEFAULT:
        assert not r.err and s[0].log[-1].addr == 0x3000_0000
    else:
        assert r.err and r.data == 0 and not s[0].log


@cocotb.test()
async def alias_follows_its_enable(dut: Any) -> None:
    m, s = await _setup(dut)
    assert not await m.write(0x1000_0010, 0x1234)
    r = await m.read(0x0000_0010)
    assert r.err != MISS_TO_DEFAULT  # alias off: a miss
    dut.alias_en_i.value = 1
    r = await m.read(0x0000_0010)
    assert (r.data, r.err) == (0x1234, False)
    assert s[0].log[-1].addr == 0x1000_0010


@cocotb.test()
async def slave_error_and_strobes_pass_through(dut: Any) -> None:
    m, s = await _setup(dut)
    s[0].err_addrs.add(0x1000_0100)
    assert await m.write(0x1000_0100, 1)
    assert not await m.write(0x1000_0200, 0xAABB_CCDD)
    assert not await m.write(0x1000_0200, 0x0000_1100, strb=0b0010)
    assert (await m.read(0x1000_0200)).data == 0xAABB_11DD
