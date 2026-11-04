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
* Handshake orderings (D38): ``Orderings`` classifies every handshake as VALID first, READY
  first or same cycle. A model controls READY only on the channels it receives (a slave's
  AW / W / AR, a master's B / R); ``handshaking.set(mode)`` sets it there: eager (READY up
  before VALID), lazy (READY only some cycles after VALID, so a DUT whose VALID waits for
  READY deadlocks and times out), random, or, on a slave, w_first (AWREADY only once WVALID
  was seen: legal for a slave, and a design whose WVALID waits for AWREADY deadlocks).

Wrapper ports follow the Wrapper sheet's naming pattern, so signals are bound through a
proxy entity that maps AXI signal names to the wrapper's port handles; a signal a
Port_Options row ties off is absent, and cocotbext-axi treats it as optional.
"""

import random
from dataclasses import dataclass
from typing import Any

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge
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
ORDERINGS = ("valid_first", "ready_first", "same_cycle")
ROUNDS = 40  # bound on the extra traffic rounds a test may run to see every ordering


class Orderings:
    """Per channel, how many handshakes happened in each VALID / READY order. Feed it one
    (valid, ready) sample per cycle."""

    def __init__(self) -> None:
        self.counts: dict[str, dict[str, int]] = {}
        self._waiting: dict[str, bool] = {}  # VALID was high last cycle, without READY
        self._ready: dict[str, bool] = {}  # READY was high last cycle

    def update(self, channel: str, valid: int, ready: int) -> None:
        if valid and ready:
            if self._waiting.get(channel):
                kind = "valid_first"
            elif self._ready.get(channel):
                kind = "ready_first"
            else:
                kind = "same_cycle"
            row = self.counts.setdefault(channel, dict.fromkeys(ORDERINGS, 0))
            row[kind] += 1
        self._waiting[channel] = bool(valid and not ready)
        self._ready[channel] = bool(ready)

    def missing(self, channels: tuple[str, ...]) -> dict[str, list[str]]:
        """Orderings not seen, per channel of CHANNELS."""
        out = {}
        for ch in channels:
            row = self.counts.get(ch, {})
            gaps = [k for k in ORDERINGS if not row.get(k)]
            if gaps:
                out[ch] = gaps
        return out


def _channel_signals(axi_bus: Any) -> dict[str, tuple[Any, Any]]:
    """Channel -> (VALID, READY) handles of a cocotbext-axi bus."""
    w, r = axi_bus.write, axi_bus.read
    return {"aw": (w.aw.awvalid, w.aw.awready), "w": (w.w.wvalid, w.w.wready),
            "b": (w.b.bvalid, w.b.bready), "ar": (r.ar.arvalid, r.ar.arready),
            "r": (r.r.rvalid, r.r.rready)}  # fmt: skip


async def _watch_orderings(clock: Any, axi_bus: Any, orderings: Orderings) -> None:
    signals = _channel_signals(axi_bus)
    while True:
        await RisingEdge(clock)
        await ReadOnly()
        for ch, (valid, ready) in signals.items():
            orderings.update(ch, int(valid.value), int(ready.value))


class Handshaking:
    """READY of the channels a model receives (see the module docstring for the modes)."""

    def __init__(self, clock: Any, sinks: dict[str, tuple[Any, Any]]) -> None:
        self.clock, self.sinks = clock, sinks  # channel -> (cocotbext sink, VALID handle)
        self.tasks: list[Any] = []
        self.aw_ready: Any = None  # a slave's write channels, for w_first
        self.w_ready: Any = None
        self.w_last: Any = None

    def set(self, mode: str, seed: int = 1) -> None:
        for task in self.tasks:
            task.cancel()
        self.tasks = []
        rng = random.Random(seed)

        def pauses() -> Any:
            while True:
                yield rng.random() < 0.5

        for sink, valid in self.sinks.values():
            sink.set_pause_generator(pauses() if mode == "random" else None)
            sink.pause = False
            if mode == "lazy":
                self.tasks.append(cocotb.start_soon(self._lazy(sink, valid)))
        if mode == "w_first":
            self.tasks.append(cocotb.start_soon(self._w_first()))

    async def _lazy(self, sink: Any, valid: Any) -> None:
        sink.pause = True
        waited = 0
        while True:
            await RisingEdge(self.clock)
            waited = waited + 1 if int(valid.value) else 0
            sink.pause = waited < 3

    async def _w_first(self) -> None:
        """Hold AWREADY until WVALID: W may run ahead of AW, never the other way round."""
        aw_sink, aw_valid = self.sinks["aw"]
        _, w_valid = self.sinks["w"]
        aw_ready, w_ready, w_last = self.aw_ready, self.w_ready, self.w_last
        aw_sink.pause = True
        ahead = 0  # last W beats taken minus AWs taken
        while True:
            await RisingEdge(self.clock)
            if int(aw_valid.value) and int(aw_ready.value):
                ahead -= 1
            if int(w_valid.value) and int(w_ready.value) and (w_last is None or int(w_last.value)):
                ahead += 1
            aw_sink.pause = not (ahead > 0 or int(w_valid.value))


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
        self.clock = clock
        self.orderings = Orderings()
        self.handshaking = Handshaking(clock, {
            "b": (self.master.write_if.b_channel, axi_bus.write.b.bvalid),
            "r": (self.master.read_if.r_channel, axi_bus.read.r.rvalid),
        })  # fmt: skip
        # A port with PROT tied has no AxPROT: cocotbext-axi then accepts only its default.
        self.has_prot = hasattr(axi_bus.write.aw, "awprot")

    def watch_orderings(self) -> None:
        cocotb.start_soon(_watch_orderings(self.clock, self.bus, self.orderings))

    def _prot(self, prot: int) -> AxiProt:
        return AxiProt(prot) if self.has_prot else AxiProt.NONSECURE

    def _user(self, user: int) -> dict[str, int]:
        return {} if self.lite else {"user": user, "size": 2}

    async def write(self, addr: int, data: int, prot: int = 0, user: int = 0) -> int:
        extra = {} if self.lite else {"user": user, "wuser": user, "size": 2}
        resp = await self.master.write(addr, data.to_bytes(4, "little"), prot=self._prot(prot),
                                       **extra)  # fmt: skip
        return int(resp.resp)

    async def read(self, addr: int, prot: int = 0, user: int = 0) -> Result:
        resp = await self.master.read(addr, 4, prot=self._prot(prot), **self._user(user))
        code = int(resp.resp)
        return Result(code != AxiResp.OKAY, int.from_bytes(resp.data, "little"), code)

    async def byte_write(self, addr: int, value: int) -> int:
        """One single-byte write (a size-0 transfer on AXI4; byte strobe on AXI4-Lite)."""
        extra = {} if self.lite else {"size": 0}
        resp = await self.master.write(addr, bytes([value]), **extra)
        return int(resp.resp)

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
    cache: int | None = None  # AxCACHE (None on AXI4-Lite)
    length: int = 0  # AxLEN
    size: int | None = None  # AxSIZE (None on AXI4-Lite: the full width)
    burst: int = 1  # AxBURST (INCR on AXI4-Lite)


class _Target:
    """Byte memory behind cocotbext-axi's slave; raising makes it answer SLVERR."""

    def __init__(self, word: int, err_addrs: set[int]) -> None:
        self.word = word
        self.err_addrs = err_addrs  # by data word; the set can change during a test
        self.err_exact: set[int] = set()  # accesses starting at exactly these addresses
        self.mem: dict[int, int] = {}
        self.written: list[tuple[int, int]] = []  # (address, value) per byte stored

    def _check(self, addr: int) -> None:
        if addr // self.word in {a // self.word for a in self.err_addrs} or addr in self.err_exact:
            raise ValueError(f"slave error at {addr:#x}")

    async def write(self, addr: int, data: bytes) -> None:
        self._check(addr)
        for i, b in enumerate(data):
            self.mem[addr + i] = b
            self.written.append((addr + i, b))

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
        self.orderings = Orderings()
        w, r = self.slave.write_if, self.slave.read_if
        self.handshaking = Handshaking(clock, {
            "aw": (w.aw_channel, axi_bus.write.aw.awvalid),
            "w": (w.w_channel, axi_bus.write.w.wvalid),
            "ar": (r.ar_channel, axi_bus.read.ar.arvalid),
        })  # fmt: skip
        self.handshaking.aw_ready = axi_bus.write.aw.awready
        self.handshaking.w_ready = axi_bus.write.w.wready
        self.handshaking.w_last = getattr(axi_bus.write.w, "wlast", None)

    def watch_orderings(self) -> None:
        cocotb.start_soon(_watch_orderings(self.clock, self.bus, self.orderings))

    def pause(self, seed: int, ratio: float = 0.4) -> None:
        """Random back-pressure: AWREADY, WREADY and ARREADY low, BVALID and RVALID delayed,
        each channel independently (so W can be taken before AW and the other way round)."""
        rng = random.Random(seed)

        def gen() -> Any:
            while True:
                yield rng.random() < ratio

        w, r = self.slave.write_if, self.slave.read_if
        for ch in (w.aw_channel, w.w_channel, w.b_channel, r.ar_channel, r.r_channel):
            ch.set_pause_generator(gen())

    @property
    def written(self) -> list[tuple[int, int]]:
        return self.target.written

    @property
    def err_addrs(self) -> set[int]:
        return self.target.err_addrs

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
                    cache = getattr(ch, f"{pre}cache", None)
                    length = getattr(ch, f"{pre}len", None)
                    size = getattr(ch, f"{pre}size", None)
                    burst = getattr(ch, f"{pre}burst", None)
                    self.log.append(Seen(int(getattr(ch, f"{pre}addr").value), write,
                                         int(prot.value) if prot is not None else 0,
                                         int(user.value) if user is not None else 0,
                                         int(cache.value) if cache is not None else None,
                                         int(length.value) if length is not None else 0,
                                         int(size.value) if size is not None else None,
                                         int(burst.value) if burst is not None else 1))  # fmt: skip
