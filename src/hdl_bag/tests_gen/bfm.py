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


class _Tied:
    """A signal the DUT does not have (tied off at the wrapper): reads a constant."""

    def __init__(self, value: int) -> None:
        self.value = value

    def get(self) -> int:
        return self.value

    def set(self, v: int) -> None:
        pass


class Port:
    """Named signals of one bus port; each name maps to (signal, width) and is a Slot.

    Signals named in TIED must be absent from the DUT (tied off at the wrapper) and read
    as that value; every other signal must exist. A misnamed or dropped port fails."""

    def __init__(
        self,
        dut: Any,
        index: int,
        signals: dict[str, tuple[str, int]],
        tied: dict[str, int] | None = None,
    ) -> None:
        self._slots: dict[str, Slot | _Tied] = {}
        for n, (sig, w) in signals.items():
            if tied is not None and n in tied:
                assert not hasattr(dut, sig), f"{sig} should be tied off, but the DUT has it"
                self._slots[n] = _Tied(tied[n] & ((1 << w) - 1))
            else:
                assert hasattr(dut, sig), f"the DUT has no signal {sig}"
                self._slots[n] = Slot(getattr(dut, sig), index, w)

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


def wrapper_port(
    dut: Any,
    prefix: str,
    kind: dict[str, int],
    aw: int,
    dw: int,
    tied: dict[str, int] | None = None,
) -> Port:
    """A flat wrapper port PREFIX_<signal>. Signals listed in TIED are tied off by
    Port_Options: the wrapper must not expose them; they read as the given value."""
    sigs = {}
    for n, w in kind.items():
        width = w or (aw if "addr" in n else dw)
        sigs[n] = (f"{prefix}_{n}", width if n != "pstrb" else dw // 8)
    return Port(dut, 0, sigs, tied or {})


async def bridge_clocks(up: Any, up_ns: int, down: Any, down_ns: int, cdc: bool) -> Any:
    """Start a bridge's clocks; returns the downstream side's clock signal. Without a CDC
    both sides run from UP; with one, DOWN gets its own clock 3 ns out of phase (so a 1:1
    ratio still exercises the synchronizers)."""
    from cocotb.clock import Clock
    from cocotb.triggers import Timer

    Clock(up, up_ns, unit="ns").start()
    if not cdc:
        return up
    await Timer(3, unit="ns")
    Clock(down, down_ns, unit="ns").start()
    return down


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

    async def run(self, ops: list[tuple[int, int, int, int, int]]) -> list[ApbResult]:
        """Transfers (addr, write, data, strb, prot) back to back: each SETUP follows the
        previous ACCESS directly (PSEL stays high), as APB allows."""
        p = self.p
        results = []
        await RisingEdge(self.clk)
        for addr, write, data, strb, prot in ops:
            p["paddr"], p["pwrite"], p["pwdata"] = addr, write, data
            p["pstrb"], p["pprot"] = strb, prot
            p["psel"], p["penable"] = 1, 0
            await RisingEdge(self.clk)
            p["penable"] = 1
            while True:
                await ReadOnly()
                if p["pready"]:
                    results.append(ApbResult(p["prdata"], bool(p["pslverr"])))
                    break
                await RisingEdge(self.clk)
            await RisingEdge(self.clk)
        p["psel"], p["penable"] = 0, 0
        return results

    async def _xfer(self, addr: int, write: int, data: int, strb: int, prot: int) -> ApbResult:
        return (await self.run([(addr, write, data, strb, prot)]))[0]

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
    prot: int = 0  # APB: PPROT; AHB: HPROT | HNONSEC << 4
    size: int = 0  # AHB HSIZE
    trans: int = 0  # AHB HTRANS
    burst: int = 0  # AHB HBURST


@dataclass
class ApbSlave:
    """Memory-model APB slave on a fabric's slave-side port (m_p* signals)."""

    clk: Any
    port: Port
    data_width: int = 32
    max_wait: int = 2
    err_addrs: set[int] = field(default_factory=set)
    mem: dict[int, int] = field(default_factory=dict)  # by APB-word address
    log: list[Access] = field(default_factory=list)  # raw PADDR / PSTRB per completed access
    written: list[tuple[int, int]] = field(default_factory=list)  # (address, value) per byte stored
    trace: list[tuple[int, int]] | None = None  # shared (slave id, PADDR) order of accesses
    ident: int = 0

    def start(self) -> None:
        import cocotb

        self.port["pready"], self.port["prdata"], self.port["pslverr"] = 0, 0, 0
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        p, nbytes = self.port, self.data_width // 8
        ready = waits = 0
        rdata = err = 0
        access = False  # the coming cycle is an ACCESS cycle of this slave
        prev = (0, 0, 0)  # psel, penable, pready of the previous cycle
        prev_ctl: tuple[int, ...] = ()
        while True:
            await RisingEdge(self.clk)
            # Responses are junk unless this is the completing access cycle, so a fabric
            # that samples them at the wrong time or merges slaves fails the test.
            if ready:
                p["pready"], p["prdata"], p["pslverr"] = 1, rdata, err
            else:
                p["pready"] = 0 if access else random.getrandbits(1)
                p["prdata"] = random.getrandbits(self.data_width)
                p["pslverr"] = random.getrandbits(1)
            await ReadOnly()
            psel, penable = p["psel"], p["penable"]
            ctl = tuple(p[n] for n in ("paddr", "pwrite", "pwdata", "pstrb", "pprot"))
            # Protocol checks (APB3/APB4): PENABLE only with PSEL; ACCESS follows SETUP;
            # PSEL and PENABLE stay high and control stays stable until PREADY.
            assert not penable or psel, f"slave {self.ident}: PENABLE without PSEL"
            if psel and penable:
                assert prev[0], f"slave {self.ident}: ACCESS without SETUP"
                assert ctl == prev_ctl, f"slave {self.ident}: control changed in ACCESS"
            if prev[0] and prev[1] and not prev[2]:
                assert psel and penable, f"slave {self.ident}: access dropped before PREADY"
            prev, prev_ctl = (psel, penable, ready), ctl
            if psel and not penable:
                word = p["paddr"] & ~(nbytes - 1)
                waits = random.randint(0, self.max_wait)
                err = int(word in self.err_addrs)
                rdata = self.mem.get(word, 0)
                ready, access = int(waits == 0), True
            elif psel and penable:
                if ready:
                    addr, write, wdata = p["paddr"], bool(p["pwrite"]), p["pwdata"]
                    self.log.append(Access(addr, write, wdata, p["pstrb"], p["pprot"]))
                    if self.trace is not None:
                        self.trace.append((self.ident, addr))
                    word = addr & ~(nbytes - 1)
                    if write and not err:
                        old = self.mem.get(word, 0)
                        for b in range(nbytes):
                            if p["pstrb"] >> b & 1:
                                self.written.append((word + b, wdata >> (8 * b) & 0xFF))
                                lane = 0xFF << (8 * b)
                                old = (old & ~lane) | (wdata & lane)
                        self.mem[word] = old
                    ready, access = 0, False
                else:
                    waits -= 1
                    ready = int(waits <= 0)
            else:
                ready, access = 0, False


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
        err_first = False  # the previous cycle was the first cycle of an ERROR response
        await RisingEdge(self.clk)
        if beats:
            self._drive(beats[0])
        while pending is not None or i < len(beats):
            await ReadOnly()
            ready, resp = self.p["hready"], self.p["hresp"]
            # Response checks: ERROR is exactly two cycles (HREADY low, then high, HRESP
            # high in both); without a data phase in flight the master sees OKAY, ready.
            if err_first:
                assert ready and resp, "ERROR response not completed in its second cycle"
            elif resp:
                assert not ready, "ERROR response without its first (HREADY low) cycle"
            if pending is None:
                assert ready and not resp, "HREADY low or ERROR with no data phase in flight"
            err_first = bool(resp and not ready)
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
    err_addrs: set[int] = field(default_factory=set)  # by data word
    err_exact: set[int] = field(default_factory=set)  # transfers at exactly these addresses
    mem: dict[int, int] = field(default_factory=dict)
    log: list[Access] = field(default_factory=list)
    written: list[tuple[int, int]] = field(default_factory=list)  # (address, value) per byte stored
    trace: list[tuple[int, int]] | None = None  # shared (slave id, address) order of accesses
    ident: int = 0

    def start(self) -> None:
        import cocotb

        self.port["hreadyout"], self.port["hrdata"], self.port["hresp"] = 1, 0, 0
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        p, nbytes = self.port, self.data_width // 8
        beat: Access | None = None
        out = (1, self._junk(), 0)  # hreadyout, hrdata, hresp
        waits, err_phase = 0, 0
        prev: tuple[int, ...] | None = None  # address phase seen while HREADY was low
        while True:
            await RisingEdge(self.clk)
            p["hreadyout"], p["hrdata"], p["hresp"] = out
            await ReadOnly()
            # Protocol check: an address phase presented to this slave while HREADY is low
            # must stay unchanged until HREADY is high (AHB-Lite address/control stability).
            ctl = tuple(p[n] for n in ("hsel", "htrans", "haddr", "hwrite", "hsize", "hburst",
                                       "hprot", "hnonsec"))  # fmt: skip
            if prev is not None:
                assert ctl == prev, (
                    f"slave {self.ident}: address phase changed in a wait state: {prev} -> {ctl}"
                )
            valid = p["hsel"] and p["htrans"] in (NONSEQ, SEQ)
            prev = ctl if (valid and not p["hready"]) else None
            # Data phase in progress.
            if beat is not None and p["hreadyout"]:
                if err_phase == 0 and beat.write:
                    self._write(beat, p["hwdata"])
                beat = None
            elif beat is not None:
                if err_phase == 1:
                    out, err_phase = (1, self._junk(), 1), 2
                else:
                    waits -= 1
                    out = (1, self._read(beat.addr), 0) if waits <= 0 else (0, self._junk(), 0)
                continue
            out, err_phase = (1, self._junk(), 0), 0
            # Address phase.
            if p["hready"] and valid:
                addr = p["haddr"]
                beat = Access(
                    addr,
                    bool(p["hwrite"]),
                    0,
                    0,
                    p["hprot"] | p["hnonsec"] << 4,
                    p["hsize"],
                    p["htrans"],
                    p["hburst"],
                )
                self.log.append(beat)
                if self.trace is not None:
                    self.trace.append((self.ident, addr))
                if (addr & ~(nbytes - 1)) in self.err_addrs or addr in self.err_exact:
                    out, err_phase = (0, self._junk(), 1), 1
                else:
                    waits = random.randint(0, self.max_wait)
                    out = (1, self._read(addr), 0) if waits == 0 else (0, self._junk(), 0)

    def _junk(self) -> int:
        return random.getrandbits(self.data_width)

    def _read(self, addr: int) -> int:
        return self.mem.get(addr & ~(self.data_width // 8 - 1), 0)

    def _write(self, beat: Access, data: int) -> None:
        nbytes = self.data_width // 8
        word = beat.addr & ~(nbytes - 1)
        old = self.mem.get(word, 0)
        for b in range(beat.addr % nbytes, beat.addr % nbytes + (1 << beat.size)):
            self.written.append((word + b, data >> (8 * b) & 0xFF))
            lane = 0xFF << (8 * b)
            old = (old & ~lane) | (data & lane)
        self.mem[word] = old
        beat.data = data
