# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Minimal in-house AHB-Lite and APB bus-functional models for cocotb smoke tests.

Phase 4 grows these into full drivers, slave models and protocol monitors. Timing
convention: drivers write right after a rising edge; everything is sampled in ReadOnly
after the edge, i.e. the values that the next rising edge will capture.

The in-house fabrics use packed-array ports (``[N-1:0][W-1:0]``), which a simulator
exposes as one flat vector. ``Slot`` gives each master or slave model its own field of
such a vector; several models can write the same vector in one time step without
clobbering each other, because the writes go through one shared shadow value.
"""

import random
from dataclasses import dataclass, field
from typing import Any

from cocotb.handle import LogicObject
from cocotb.triggers import ReadOnly, RisingEdge


def value(sig: Any) -> int:
    v = sig.value
    return v.to_unsigned() if hasattr(v, "to_unsigned") else int(v)


class _Shadow:
    _all: dict[int, "_Shadow"] = {}  # noqa: RUF012 - one registry per simulation

    def __init__(self, sig: LogicObject) -> None:
        self.sig = sig
        self.word = 0

    @classmethod
    def of(cls, sig: LogicObject) -> "_Shadow":
        key = id(sig)
        if key not in cls._all:
            cls._all[key] = _Shadow(sig)
        return cls._all[key]


class Slot:
    """Field INDEX (WIDTH bits wide) of a flat packed-array signal."""

    def __init__(self, sig: Any, index: int, width: int) -> None:
        self.sig, self.shift, self.mask = sig, index * width, (1 << width) - 1

    def get(self) -> int:
        return (value(self.sig) >> self.shift) & self.mask

    def set(self, v: int) -> None:
        sh = _Shadow.of(self.sig)
        sh.word = (sh.word & ~(self.mask << self.shift)) | ((v & self.mask) << self.shift)
        self.sig.value = sh.word


class Port:
    """Named signals of one bus port; each name maps to (signal, width) and is a Slot."""

    def __init__(self, dut: Any, index: int, signals: dict[str, tuple[str, int]]) -> None:
        self._slots = {n: Slot(getattr(dut, sig), index, w) for n, (sig, w) in signals.items()}

    def __getitem__(self, name: str) -> int:
        return self._slots[name].get()

    def __setitem__(self, name: str, v: int) -> None:
        self._slots[name].set(v)


APB = {"paddr": 0, "pprot": 3, "psel": 1, "penable": 1, "pwrite": 1, "pwdata": 0, "pstrb": 0,
       "pready": 1, "prdata": 0, "pslverr": 1}  # fmt: skip
APB_RESP = {"pready", "prdata", "pslverr"}
AHB_M = {"haddr": 0, "hburst": 3, "hmastlock": 1, "hprot": 4, "hnonsec": 1, "hsize": 3,
         "htrans": 2, "hwdata": 0, "hwrite": 1, "hrdata": 0, "hready": 1, "hresp": 1}  # fmt: skip
AHB_M_RESP = {"hrdata", "hready", "hresp"}
AHB_S = {**{k: v for k, v in AHB_M.items() if k not in {"hmastlock", "hready"}},
         "hsel": 1, "hready": 1, "hreadyout": 1}  # fmt: skip
AHB_S_RESP = {"hrdata", "hreadyout", "hresp"}


def fabric_port(
    dut: Any, side: str, index: int, kind: dict[str, int], resp: set[str], aw: int, dw: int
) -> Port:
    """Port INDEX of a fabric. side "s": master side (responses are DUT outputs);
    side "m": slave side (responses are DUT inputs). Width 0 means address or data."""
    out = {"s": resp, "m": set(kind) - resp}[side]
    sigs = {}
    for n, w in kind.items():
        width = w or (aw if "addr" in n else dw)
        width = width if n != "pstrb" else dw // 8
        sigs[n] = (f"{side}_{n}_{'o' if n in out else 'i'}", width)
    return Port(dut, index, sigs)


# ---------------------------------------------------------------------------- APB


@dataclass
class ApbResult:
    data: int
    err: bool


class ApbMaster:
    """Drives a fabric's master-side APB port (s_p* inputs)."""

    def __init__(self, clk: Any, port: Port) -> None:
        self.clk, self.p = clk, port
        for n in ("psel", "penable", "pwrite", "paddr", "pwdata", "pstrb", "pprot"):
            self.p[n] = 0

    async def _xfer(self, addr: int, write: int, data: int, strb: int, prot: int) -> ApbResult:
        await RisingEdge(self.clk)
        p = self.p
        p["paddr"], p["pwrite"], p["pwdata"], p["pstrb"], p["pprot"] = addr, write, data, strb, prot
        p["psel"], p["penable"] = 1, 0
        await RisingEdge(self.clk)
        p["penable"] = 1
        while True:
            await ReadOnly()
            if p["pready"]:
                result = ApbResult(p["prdata"], bool(p["pslverr"]))
                break
            await RisingEdge(self.clk)
        await RisingEdge(self.clk)
        p["psel"], p["penable"] = 0, 0
        return result

    async def write(self, addr: int, data: int, *, strb: int = 0xF, prot: int = 0) -> bool:
        return (await self._xfer(addr, 1, data, strb, prot)).err

    async def read(self, addr: int, *, prot: int = 0) -> ApbResult:
        return await self._xfer(addr, 0, 0, 0, prot)


@dataclass
class Access:
    addr: int
    write: bool
    data: int
    strb: int = 0
    prot: int = 0
    master: int | None = None


@dataclass
class ApbSlave:
    """Memory-model APB slave on a fabric's slave-side port (m_p* signals)."""

    clk: Any
    port: Port
    data_width: int = 32
    max_wait: int = 2
    err_addrs: set[int] = field(default_factory=set)
    mem: dict[int, int] = field(default_factory=dict)
    log: list[Access] = field(default_factory=list)

    def start(self) -> None:
        import cocotb

        self.port["pready"], self.port["prdata"], self.port["pslverr"] = 0, 0, 0
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        p, nbytes = self.port, self.data_width // 8
        ready = waits = 0
        rdata = err = 0
        while True:
            await RisingEdge(self.clk)
            p["pready"], p["prdata"], p["pslverr"] = (
                ready,
                rdata if ready else 0,
                err if ready else 0,
            )
            await ReadOnly()
            if p["psel"] and not p["penable"]:
                addr = p["paddr"] & ~(nbytes - 1)
                waits = random.randint(0, self.max_wait)
                err = int(addr in self.err_addrs)
                rdata = self.mem.get(addr, 0)
                ready = int(waits == 0)
            elif p["psel"] and p["penable"]:
                if ready:
                    addr = p["paddr"] & ~(nbytes - 1)
                    write = bool(p["pwrite"])
                    strb = p["pstrb"] if write else 0
                    self.log.append(Access(addr, write, p["pwdata"], strb, p["pprot"]))
                    if write and not err:
                        old = self.mem.get(addr, 0)
                        for b in range(nbytes):
                            if strb >> b & 1:
                                lane = 0xFF << (8 * b)
                                old = (old & ~lane) | (p["pwdata"] & lane)
                        self.mem[addr] = old
                    ready = 0
                else:
                    waits -= 1
                    ready = int(waits <= 0)
            else:
                ready = 0


# ---------------------------------------------------------------------------- AHB

IDLE, BUSY, NONSEQ, SEQ = 0, 1, 2, 3


@dataclass
class Beat:
    addr: int
    write: bool
    data: int = 0
    size: int = 2
    burst: int = 0
    trans: int = NONSEQ
    prot: int = 0b0011
    nonsec: int = 0
    lock: int = 0


@dataclass
class AhbResult:
    data: int
    err: bool


class AhbMaster:
    """Pipelined AHB-Lite master driving a fabric's master-side port (s_h* signals)."""

    def __init__(self, clk: Any, port: Port) -> None:
        self.clk, self.p = clk, port
        self._idle()
        self.p["hwdata"] = 0

    def _idle(self) -> None:
        for n in ("haddr", "hburst", "hmastlock", "hprot", "hnonsec", "hsize", "hwrite"):
            self.p[n] = 0
        self.p["htrans"] = IDLE

    def _drive(self, b: Beat) -> None:
        p = self.p
        p["haddr"], p["hwrite"], p["hsize"], p["hburst"] = b.addr, int(b.write), b.size, b.burst
        p["hprot"], p["hnonsec"], p["hmastlock"], p["htrans"] = b.prot, b.nonsec, b.lock, b.trans

    async def run(self, beats: list[Beat]) -> list[AhbResult]:
        """Issue BEATS back to back (as given: NONSEQ/SEQ/BUSY) and return one result each."""
        results: list[AhbResult] = []
        pending: Beat | None = None
        i = 0
        await RisingEdge(self.clk)
        if beats:
            self._drive(beats[0])
        while pending is not None or i < len(beats):
            await ReadOnly()
            ready = self.p["hready"]
            if ready:
                if pending is not None:
                    results.append(AhbResult(self.p["hrdata"], bool(self.p["hresp"])))
                pending = beats[i] if i < len(beats) else None
                if pending is not None and pending.trans == BUSY:
                    results.append(AhbResult(0, False))
                    pending = None
                i += 1 if i < len(beats) else 0
            await RisingEdge(self.clk)
            if ready:
                if i < len(beats):
                    self._drive(beats[i])
                else:
                    self._idle()
                if pending is not None and pending.write:
                    self.p["hwdata"] = pending.data
        return results

    async def write(self, addr: int, data: int, size: int = 2, **kw: int) -> bool:
        return (await self.run([Beat(addr, True, data, size, **kw)]))[0].err

    async def read(self, addr: int, size: int = 2, **kw: int) -> AhbResult:
        return (await self.run([Beat(addr, False, 0, size, **kw)]))[0]


@dataclass
class AhbSlave:
    """Memory-model AHB-Lite slave on a fabric's slave-side port (m_h* signals)."""

    clk: Any
    port: Port
    data_width: int = 32
    max_wait: int = 2
    err_addrs: set[int] = field(default_factory=set)
    mem: dict[int, int] = field(default_factory=dict)
    log: list[Access] = field(default_factory=list)
    trace: list[tuple[int, int]] | None = None  # shared (slave id, address) order of accesses
    ident: int = 0

    def start(self) -> None:
        import cocotb

        self.port["hreadyout"], self.port["hrdata"], self.port["hresp"] = 1, 0, 0
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        p, nbytes = self.port, self.data_width // 8
        beat: tuple[int, bool, int, int, int] | None = None  # addr, write, size, prot, nonsec
        out = (1, 0, 0)  # hreadyout, hrdata, hresp
        waits, err_phase = 0, 0
        while True:
            await RisingEdge(self.clk)
            p["hreadyout"], p["hrdata"], p["hresp"] = out
            await ReadOnly()
            # Data phase in progress.
            if beat is not None and p["hreadyout"]:
                addr, write, size, _prot, _nonsec = beat
                if err_phase == 0 and write:
                    self._write(addr, size, p["hwdata"])
                beat = None
            elif beat is not None:
                if err_phase == 1:
                    out, err_phase = (1, 0, 1), 2
                else:
                    waits -= 1
                    if waits <= 0:
                        out = (1, self._read(beat[0]), 0)
                continue
            out, err_phase = (1, 0, 0), 0
            # Address phase.
            if p["hready"] and p["hsel"] and p["htrans"] in (NONSEQ, SEQ):
                addr, write, size = p["haddr"], bool(p["hwrite"]), p["hsize"]
                beat = (addr, write, size, p["hprot"], p["hnonsec"])
                self.log.append(Access(addr, write, 0, 0, p["hprot"] | p["hnonsec"] << 4))
                if self.trace is not None:
                    self.trace.append((self.ident, addr))
                if (addr & ~(nbytes - 1)) in self.err_addrs:
                    out, err_phase = (0, 0, 1), 1
                else:
                    waits = random.randint(0, self.max_wait)
                    out = (1, self._read(addr), 0) if waits == 0 else (0, 0, 0)

    def _read(self, addr: int) -> int:
        return self.mem.get(addr & ~(self.data_width // 8 - 1), 0)

    def _write(self, addr: int, size: int, data: int) -> None:
        nbytes = self.data_width // 8
        word = addr & ~(nbytes - 1)
        old = self.mem.get(word, 0)
        for b in range(addr % nbytes, addr % nbytes + (1 << size)):
            lane = 0xFF << (8 * b)
            old = (old & ~lane) | (data & lane)
        self.mem[word] = old
        self.log[-1].data = data
