# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""AXI4 / AXI4-Lite test models on cocotbext-axi, with the in-house BFMs' interface.

* ``AxiPortMaster``: ``write(addr, data, prot) -> resp`` (the AXI response code; 0 = OKAY,
  so it reads as an error flag) and ``read(addr, prot) -> result`` with ``.err`` /
  ``.data`` / ``.resp``: one 32-bit access (a narrow transfer on a wider bus), like
  ``bfm.ApbMaster``. ``burst_write`` / ``burst_read`` issue INCR bursts.
* ``AxiPortSlave``: a memory that answers SLVERR at the given slave-visible addresses and
  logs every AW / AR handshake (address, write, PROT, USER) like ``bfm.AhbSlave``; its AW
  and AR channels can be stalled (``stall``).

Wrapper ports follow the Wrapper sheet's naming pattern, so signals are bound through a
proxy entity that maps AXI signal names to the wrapper's port handles; a signal a
Port_Options row ties off is absent, and cocotbext-axi treats it as optional.
"""

from dataclasses import dataclass
from typing import Any

import cocotb
from cocotb.triggers import RisingEdge
from cocotb.types import LogicArray
from cocotbext.axi import (
    AxiBus,
    AxiLiteBus,
    AxiLiteMaster,
    AxiLiteSlave,
    AxiMaster,
    AxiProt,
    AxiResp,
    AxiSlave,
)


class _Absent:
    """An inert 1-bit stand-in for a signal the port does not have (an AXI4 port without
    IDs: cocotbext-axi requires the ID signals). Writes are kept, reads give them back."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._value = LogicArray.from_unsigned(0, 1)

    @property
    def value(self) -> LogicArray:
        return self._value

    @value.setter
    def value(self, value: Any) -> None:
        try:
            bit = int(value) & 1
        except ValueError:  # X / Z driven while idle
            bit = 0
        self._value = LogicArray.from_unsigned(bit, 1)

    def __len__(self) -> int:
        return 1

    def setimmediatevalue(self, value: Any) -> None:
        self.value = value


class _Entity:
    """Stands in for the DUT: attribute <axi signal> is the wrapper port carrying it."""

    def __init__(self, dut: Any, signals: dict[str, str], absent: tuple[str, ...]) -> None:
        self._log = dut._log
        self._name = dut._name
        for sig, port in signals.items():
            setattr(self, sig, getattr(dut, port))
        for sig in absent:
            setattr(self, sig, _Absent(sig))


AXI4_IDS = ("awid", "bid", "arid", "rid")


def bus(dut: Any, signals: dict[str, str], lite: bool) -> Any:
    """cocotbext-axi bus for one wrapper port; SIGNALS maps AXI names to port names. An AXI4
    port without IDs gets inert ID stand-ins."""
    absent = () if lite else tuple(s for s in AXI4_IDS if s not in signals)
    entity = _Entity(dut, signals, absent)
    return (AxiLiteBus if lite else AxiBus).from_entity(entity)


@dataclass(frozen=True)
class Result:
    err: bool
    data: int = 0
    resp: int = 0


class AxiPortMaster:
    def __init__(self, clock: Any, axi_bus: Any, lite: bool, reset: Any = None,
                 reset_active_level: bool = False) -> None:  # fmt: skip
        cls = AxiLiteMaster if lite else AxiMaster
        self.master = cls(axi_bus, clock, reset=reset, reset_active_level=reset_active_level)
        self.bus = axi_bus
        self.lite = lite

    def _user(self, user: int) -> dict[str, int]:
        return {} if self.lite else {"user": user, "size": 2}

    async def write(self, addr: int, data: int, prot: int = 0, user: int = 0) -> int:
        extra = {} if self.lite else {"user": user, "wuser": user, "size": 2}
        resp = await self.master.write(addr, data.to_bytes(4, "little"), prot=AxiProt(prot),
                                       **extra)  # fmt: skip
        return int(resp.resp)

    async def read(self, addr: int, prot: int = 0, user: int = 0) -> Result:
        resp = await self.master.read(addr, 4, prot=AxiProt(prot), **self._user(user))
        code = int(resp.resp)
        return Result(code != AxiResp.OKAY, int.from_bytes(resp.data, "little"), code)

    async def burst_write(self, addr: int, data: bytes) -> int:
        """One INCR burst of 32-bit beats (AXI4 only)."""
        resp = await self.master.write(addr, data, size=2)
        return int(resp.resp)

    async def burst_read(self, addr: int, length: int) -> Result:
        resp = await self.master.read(addr, length, size=2)
        code = int(resp.resp)
        return Result(code != AxiResp.OKAY, int.from_bytes(resp.data, "little"), code)


@dataclass(frozen=True)
class Seen:
    addr: int
    write: bool
    prot: int
    user: int = 0


class _Target:
    """Byte memory behind cocotbext-axi's slave; raising makes it answer SLVERR."""

    def __init__(self, word: int, err_addrs: set[int]) -> None:
        self.word = word
        self.err_words = {a // word for a in err_addrs}
        self.mem: dict[int, int] = {}

    def _check(self, addr: int) -> None:
        if addr // self.word in self.err_words:
            raise ValueError(f"slave error at {addr:#x}")

    async def write(self, addr: int, data: bytes) -> None:
        self._check(addr)
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    async def read(self, addr: int, length: int) -> bytes:
        self._check(addr)
        return bytes(self.mem.get(addr + i, 0) for i in range(length))


class AxiPortSlave:
    def __init__(self, clock: Any, axi_bus: Any, lite: bool, data_width: int,
                 err_addrs: set[int], reset: Any = None,
                 reset_active_level: bool = False) -> None:  # fmt: skip
        self.clock = clock
        self.bus = axi_bus
        self.target = _Target(data_width // 8, err_addrs)
        cls = AxiLiteSlave if lite else AxiSlave
        self.slave = cls(
            axi_bus, clock, reset=reset, target=self.target, reset_active_level=reset_active_level
        )
        self.log: list[Seen] = []

    def stall(self, on: bool) -> None:
        """Hold the AW and AR channels not ready (requests queue up in the fabric)."""
        self.slave.write_if.aw_channel.pause = on
        self.slave.read_if.ar_channel.pause = on

    def start(self) -> None:
        cocotb.start_soon(self._monitor())

    async def _monitor(self) -> None:
        aw, ar = self.bus.write.aw, self.bus.read.ar
        while True:
            await RisingEdge(self.clock)
            for ch, write, pre in ((aw, True, "aw"), (ar, False, "ar")):
                if int(getattr(ch, f"{pre}valid").value) and int(getattr(ch, f"{pre}ready").value):
                    prot = getattr(ch, f"{pre}prot", None)
                    user = getattr(ch, f"{pre}user", None)
                    self.log.append(Seen(int(getattr(ch, f"{pre}addr").value), write,
                                         int(prot.value) if prot is not None else 0,
                                         int(user.value) if user is not None else 0))  # fmt: skip
