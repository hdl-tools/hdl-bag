# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""AXI monitor for a smoke-test top's AXI ports (PREFIX_<signal>, all AXI4 fields present).

It logs every AW / AR handshake and every R / B beat, and checks the AXI handshake rules on
all five channels: once VALID is high it stays high, with its payload unchanged, until
READY. With ``single_beat`` (bridges that issue single-beat transfers) it also checks
AxLEN = 0, WLAST = 1, AxSIZE within the bus and WSTRB within the transfer's byte lanes.
"""

from dataclasses import dataclass
from typing import Any

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from hdl_bag.tests_gen.bfm import value
from hdl_bag.tests_gen.bfm_axi import AXI4_IDS, Orderings

# AXI signal names of a Lite and an AXI4 slave model on the m_* ports.
LITE_SIGS = ("awaddr", "awprot", "awvalid", "awready", "wdata", "wstrb", "wvalid", "wready",
             "bresp", "bvalid", "bready", "araddr", "arprot", "arvalid", "arready", "rdata",
             "rresp", "rvalid", "rready")  # fmt: skip
AXI4_SIGS = (*LITE_SIGS, *AXI4_IDS, "awlen", "awsize", "awburst", "awlock", "awcache", "awqos",
             "awregion", "wlast", "arlen", "arsize", "arburst", "arlock", "arcache", "arqos",
             "arregion", "rlast")  # fmt: skip

PAYLOAD = {
    "aw": ("awid", "awaddr", "awlen", "awsize", "awburst", "awlock", "awcache", "awprot"),
    "w": ("wdata", "wstrb", "wlast"),
    "b": ("bid", "bresp"),
    "ar": ("arid", "araddr", "arlen", "arsize", "arburst", "arlock", "arcache", "arprot"),
    "r": ("rid", "rdata", "rresp", "rlast"),
}


@dataclass(frozen=True)
class Ax:
    write: bool
    addr: int
    len: int
    size: int
    burst: int
    cache: int
    prot: int
    id: int
    lock: int


@dataclass(frozen=True)
class Beat:
    id: int
    resp: int
    last: int = 1


class AxiWatch:
    def __init__(self, dut: Any, clk: Any, prefix: str = "m", *, data_width: int = 32,
                 single_beat: bool = False) -> None:  # fmt: skip
        self.dut, self.clk, self.p = dut, clk, prefix
        self.bytes, self.single = data_width // 8, single_beat
        self.log: list[Ax] = []
        self.r: list[Beat] = []
        self.b: list[Beat] = []
        self.orderings = Orderings()
        cocotb.start_soon(self._run())

    def _sig(self, name: str) -> int:
        return value(getattr(self.dut, f"{self.p}_{name}"))

    async def _run(self) -> None:
        held: dict[str, tuple[int, ...]] = {}  # channel -> payload while VALID waits
        aws: list[Ax] = []  # AWs and W strobes not yet paired (in order: single beats)
        ws: list[int] = []
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            for ch, fields in PAYLOAD.items():
                valid, ready = self._sig(f"{ch}valid"), self._sig(f"{ch}ready")
                self.orderings.update(ch, valid, ready)
                payload = tuple(self._sig(f) for f in fields)
                if ch in held:
                    assert valid, f"{self.p}: {ch.upper()}VALID dropped before READY"
                    assert payload == held[ch], f"{self.p}: {ch} payload changed before READY"
                if valid and not ready:
                    held[ch] = payload
                else:
                    held.pop(ch, None)
                if not (valid and ready):
                    continue
                if ch in ("aw", "ar"):
                    ax = Ax(
                        ch == "aw",
                        *(
                            self._sig(f"{ch}{f}")
                            for f in ("addr", "len", "size", "burst", "cache", "prot", "id", "lock")
                        ),
                    )
                    self.log.append(ax)
                    if self.single:
                        assert ax.len == 0 and 1 << ax.size <= self.bytes, ax
                    if ch == "aw" and self.single:
                        aws.append(ax)
                elif ch == "w" and self.single:
                    assert self._sig("wlast"), f"{self.p}: single beat without WLAST"
                    ws.append(self._sig("wstrb"))
                elif ch == "b":
                    self.b.append(Beat(self._sig("bid"), self._sig("bresp")))
                elif ch == "r":
                    self.r.append(Beat(self._sig("rid"), self._sig("rresp"), self._sig("rlast")))
            while aws and ws:  # strobes only within the transfer's byte lanes
                ax, strb = aws.pop(0), ws.pop(0)
                lane = ax.addr % self.bytes & ~((1 << ax.size) - 1)
                lanes = ((1 << (1 << ax.size)) - 1) << lane
                assert strb & ~lanes == 0, (hex(strb), ax)
