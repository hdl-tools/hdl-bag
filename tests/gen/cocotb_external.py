# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Traffic through the external_fabric config with pass-through user fabrics
(tests/gen/test_external.py): cpu -> cdc, dw -> noc (in sub-wrapper ss_noc) -> ram, and
mcu -> sys -> bag_ahb2apb (pass-through N) -> peri -> uart."""

from typing import Any

import cocotb
from bfm import Beat
from cocotb_route import _bring_up


@cocotb.test(timeout_time=200, timeout_unit="us")
async def accesses_cross_the_user_fabrics(dut: Any) -> None:
    masters, slaves, _ = await _bring_up(dut)
    cpu, ram = masters["cpu"], slaves["ram"]
    for k, addr in enumerate((0x8000_0000, 0x8000_0104, 0x800F_FFFC)):
        data = 0x1234_5678 ^ (k * 0x0101_0101)
        assert await cpu.write(addr, data) == 0
        r = await cpu.read(addr)
        assert (r.err, r.data) == (False, data)
        assert [(s.addr, s.write) for s in ram.log[-2:]] == [(addr, True), (addr, False)]
    mcu, uart = masters["mcu"], slaves["uart"]
    for addr in (0x5000_0000, 0x5000_0FFC):
        results = await mcu.run([Beat(addr, True, 0xCAFE_0000 | addr & 0xFFFF, size=2),
                                 Beat(addr, False, size=2)])  # fmt: skip
        assert [r.err for r in results] == [False, False]
        assert results[1].data == 0xCAFE_0000 | addr & 0xFFFF
        assert uart.log[-1].addr == addr - 0x5000_0000  # the bridge's window offset
