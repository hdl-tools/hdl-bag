# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""cocotb tests for bag_axi2ahb (top: tests/rtl/tb/tb_axi2ahb.sv).

The configuration comes from the environment: ADW / HDW data widths, LITE (1: an AXI4-Lite
master on the Lite subset), IDW (ID width), OFFSET (AddrOffset), CDC (AsyncCdc), ACLK_NS /
HCLK_NS clock periods. Two monitors run throughout:
* AhbRules: SEQ only right after a transfer of the same INCR burst, at the next address,
  with the same size and direction, never at a 1 KB boundary; addresses aligned to their
  size; no BUSY. It counts IDLE-then-NONSEQ restarts inside a burst.
* AxiWatch on the upstream port: VALID held with a stable payload until READY (the bridge's
  B and R included), and every R / B beat with its response.
"""

import os
import random
from typing import Any

import cocotb
from axi_watch import AXI4_SIGS, LITE_SIGS, AxiWatch
from bfm import AHB_S, AHB_S_RESP, NONSEQ, SEQ, AhbSlave, bridge_clocks, fabric_port, value
from bfm_axi import ROUNDS, Handshaking, bus
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge
from cocotbext.axi import AxiBurstType, AxiLiteMaster, AxiLockType, AxiMaster, AxiProt, AxiResp

ADW = int(os.environ.get("ADW", "32"))
HDW = int(os.environ.get("HDW", "32"))
LITE = os.environ.get("LITE", "0") == "1"
IDW = int(os.environ.get("IDW", "4"))
OFFSET = int(os.environ.get("OFFSET", "0"), 0)
CDC = os.environ.get("CDC", "0") == "1"
ACLK_NS = int(os.environ.get("ACLK_NS", "10"))
HCLK_NS = int(os.environ.get("HCLK_NS", "10"))
BASE = 0x4000_0000
AB, HB = ADW // 8, HDW // 8
FULL_SIZE = AB.bit_length() - 1
INCR = 0b001
SLVERR = 2


class AhbRules:
    """Checks the bridge's AHB master output, transfer by transfer."""

    def __init__(self, dut: Any, clk: Any) -> None:
        self.dut, self.clk = dut, clk
        self.transfers = 0
        self.restarts = 0  # NONSEQ continuing an INCR burst's addresses after an IDLE
        cocotb.start_soon(self._run())

    def _s(self, n: str) -> int:
        return value(getattr(self.dut, n))

    async def _run(self) -> None:
        prev: tuple[int, ...] | None = None  # trans, addr, size, write, burst taken last cycle
        last: tuple[int, ...] | None = None  # the last transfer (not IDLE) taken
        gap = False  # an IDLE since LAST
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            if not self._s("m_hreadyout_i"):
                continue
            cur = tuple(self._s(f"m_{n}_o") for n in ("htrans", "haddr", "hsize", "hwrite",
                                                      "hburst"))  # fmt: skip
            trans, addr, size, write, burst = cur
            assert trans != 1, "the bridge never issues BUSY"
            if trans in (NONSEQ, SEQ):
                self.transfers += 1
                assert addr % (1 << size) == 0, f"unaligned AHB transfer {cur}"
            if trans == SEQ:
                assert prev is not None and prev[0] in (NONSEQ, SEQ), f"SEQ after IDLE: {cur}"
                assert burst == prev[4] == INCR, f"SEQ outside an INCR burst: {prev} -> {cur}"
                assert (size, write) == (prev[2], prev[3]), f"SEQ changed size/dir: {prev} {cur}"
                assert addr == prev[1] + (1 << size), f"SEQ not at the next address: {prev} {cur}"
                assert addr % 1024 != 0, f"burst crosses a 1 KB boundary: {cur}"
            resumes = last is not None and burst == last[4] == INCR and (size, write) == (
                last[2], last[3]) and addr == last[1] + (1 << size)  # fmt: skip
            if trans == NONSEQ and gap and resumes:
                self.restarts += 1
            if trans in (NONSEQ, SEQ):
                last, gap = cur, False
            else:
                gap = True
            prev = cur


class RawAxi:
    """Drives the upstream AXI4 port directly, for strobe patterns cocotbext-axi cannot
    make (sparse, none). One transaction at a time: AW, then the W beats, then B."""

    def __init__(self, dut: Any) -> None:
        self.dut = dut
        for n in ("awvalid", "wvalid", "arvalid", "awid", "awaddr", "awlen", "awsize",
                  "awburst", "awlock", "awcache", "awprot", "awqos", "awregion", "wdata",
                  "wstrb", "wlast"):  # fmt: skip
            getattr(dut, f"s_{n}").value = 0
        dut.s_bready.value = 1
        dut.s_rready.value = 1

    async def _handshake(self, ch: str) -> None:
        """Hold VALID until the edge that sees READY (sampled before that edge), then drop it."""
        while True:
            await ReadOnly()
            ready = value(getattr(self.dut, f"s_{ch}ready"))
            await RisingEdge(self.dut.aclk_i)
            if ready:
                break
        getattr(self.dut, f"s_{ch}valid").value = 0

    async def write(self, addr: int, beats: list[tuple[int, int]], size: int = FULL_SIZE) -> int:
        d = self.dut
        d.s_awaddr.value, d.s_awlen.value, d.s_awsize.value = addr, len(beats) - 1, size
        d.s_awburst.value, d.s_awcache.value, d.s_awvalid.value = 1, 0b0011, 1
        await self._handshake("aw")
        for k, (data, strb) in enumerate(beats):
            d.s_wdata.value, d.s_wstrb.value = data, strb
            d.s_wlast.value, d.s_wvalid.value = int(k == len(beats) - 1), 1
            await self._handshake("w")
        while True:
            await ReadOnly()
            if value(d.s_bvalid):
                resp = value(d.s_bresp)
                break
            await RisingEdge(d.aclk_i)
        await RisingEdge(d.aclk_i)
        return resp


async def _setup(dut: Any, raw: bool = False) -> tuple[Any, AhbSlave, AhbRules, AxiWatch]:
    hclk = await bridge_clocks(dut.aclk_i, ACLK_NS, dut.hclk_i, HCLK_NS, CDC)
    dut.arst_ni.value = 0
    dut.hrst_ni.value = 0
    slave = AhbSlave(hclk, fabric_port(dut, "m", 0, AHB_S, AHB_S_RESP, 32, HDW), data_width=HDW)
    slave.start()
    rules = AhbRules(dut, hclk)
    watch = AxiWatch(dut, dut.aclk_i, "s", data_width=ADW)
    if raw:
        master: Any = RawAxi(dut)
    else:
        sigs = {s: f"s_{s}" for s in (LITE_SIGS if LITE else AXI4_SIGS)}
        master = (AxiLiteMaster if LITE else AxiMaster)(bus(dut, sigs, LITE), dut.aclk_i)
    await ClockCycles(dut.aclk_i, 4)
    dut.arst_ni.value = 1
    dut.hrst_ni.value = 1
    await ClockCycles(dut.aclk_i, 4)
    return master, slave, rules, watch


def _mem(s: AhbSlave, addr: int, n: int) -> bytes:
    """N bytes of the AHB slave's memory from ADDR (an AHB address)."""
    return bytes((s.mem.get((a := addr + i) & ~(HB - 1), 0) >> (8 * (a % HB))) & 0xFF
                 for i in range(n))  # fmt: skip


def _data(n: int, seed: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.getrandbits(8) for _ in range(n))


def _pauses(seed: int, ratio: float) -> Any:
    rng = random.Random(seed)
    while True:
        yield rng.random() < ratio


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def words_cross_with_offset(dut: Any) -> None:
    m, s, _, w = await _setup(dut)
    for addr in (BASE, (BASE + 0x104) & ~(AB - 1), BASE + 0x1000 - AB, BASE + 0x2340):
        data = _data(AB, addr)
        assert (await m.write(addr, data)).resp == AxiResp.OKAY
        assert _mem(s, addr - OFFSET, AB) == data
        r = await m.read(addr, AB)
        assert (r.data, r.resp) == (data, AxiResp.OKAY)
    assert len(w.b) == 4 and len(w.r) == 4


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def byte_writes_and_narrow_reads(dut: Any) -> None:
    """Single-byte writes (a size-0 transfer, or a byte strobe on AXI4-Lite) on every lane;
    byte reads on every lane."""
    m, s, _, _ = await _setup(dut)
    word = BASE + 0x300
    assert (await m.write(word, b"\xff" * AB)).resp == AxiResp.OKAY
    for b in range(0, AB, 3):
        extra = {} if LITE else {"size": 0}
        assert (await m.write(word + b, bytes([b + 1]), **extra)).resp == AxiResp.OKAY
    expect = bytes((b + 1) if b % 3 == 0 else 0xFF for b in range(AB))
    assert _mem(s, word - OFFSET, AB) == expect
    if not LITE:
        for b in range(AB):
            r = await m.read(word + b, 1, size=0)
            assert r.data == expect[b : b + 1], b


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def sparse_and_zero_strobes(dut: Any) -> None:
    """Strobe patterns cocotbext-axi cannot make: with equal widths a partial beat is one
    byte write per set strobe and a beat without strobes issues nothing; the memory always
    ends up right, and a burst mixing such beats keeps the AHB rules."""
    m, s, rules, _ = await _setup(dut, raw=True)
    word = BASE + 0x380
    full = (1 << AB) - 1
    patterns = [p & full for p in (0b1010, 0b1001, 0, 0b0110, 0b1000_0001)]
    for strb in patterns:
        assert await m.write(word, [(int.from_bytes(b"\xff" * AB, "little"), full)]) == 0
        n = len(s.log)
        data = int.from_bytes(bytes(range(1, AB + 1)), "little")
        assert await m.write(word, [(data, strb)]) == 0, strb
        lanes = [b for b in range(AB) if strb >> b & 1]
        if AB == HB and strb != full:
            assert [(a.addr, a.size) for a in s.log[n:]] == [
                (word - OFFSET + b, 0) for b in lanes
            ], strb
        expect = bytes(b + 1 if b in lanes else 0xFF for b in range(AB))
        assert _mem(s, word - OFFSET, AB) == expect, strb
    if LITE:
        return
    beats = [(int.from_bytes(_data(AB, k), "little"), st)
             for k, st in enumerate((full, patterns[0], 0, full, full, full))]  # fmt: skip
    base = BASE + 0x400
    assert await m.write(base, [(0, full)] * len(beats)) == 0
    assert await m.write(base, beats) == 0
    for k, (data, strb) in enumerate(beats):
        got = _mem(s, base - OFFSET + k * AB, AB)
        want = bytes((data >> (8 * b)) & 0xFF if strb >> b & 1 else 0 for b in range(AB))
        assert got == want, k
    assert rules.transfers > 0


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def incr_bursts_map_to_ahb_incr(dut: Any) -> None:
    m, s, _, _ = await _setup(dut)
    if LITE:
        return
    addr, n = BASE + 0x1000, 16 * AB
    data = _data(n, 1)
    for write in (True, False):
        start = len(s.log)
        if write:
            assert (await m.write(addr, data, size=FULL_SIZE)).resp == AxiResp.OKAY
            assert _mem(s, addr - OFFSET, n) == data
        else:
            r = await m.read(addr, n, size=FULL_SIZE)
            assert (r.data, r.resp) == (data, AxiResp.OKAY)
        log = s.log[start:]
        assert all(a.burst == INCR and a.write == write for a in log)
        seq = sum(a.trans == SEQ for a in log)
        # Beats keep coming unless a slower upstream clock or a width converter starves the
        # AHB side of write data (IDLE, then NONSEQ: legal).
        fed = not write or (AB == HB and ACLK_NS <= HCLK_NS)
        if AB == HB:  # one AHB transfer per beat
            assert [a.addr for a in log] == [addr - OFFSET + i * AB for i in range(16)]
            assert log[0].trans == NONSEQ
        if fed:
            assert seq >= 8 if AB == HB else seq >= 1, (write, seq)


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def bursts_restart_at_1kb(dut: Any) -> None:
    m, s, _, _ = await _setup(dut)
    if LITE:
        return
    addr = BASE + 0x1400 - 4 * AB  # 8 beats across the 1 KB boundary at 0x...400
    data = _data(8 * AB, 2)
    start = len(s.log)
    assert (await m.write(addr, data, size=FULL_SIZE)).resp == AxiResp.OKAY
    at = [a for a in s.log[start:] if (a.addr % 1024) == 0]
    assert at and all(a.trans == NONSEQ for a in at)  # the monitor checks the rest
    r = await m.read(addr, 8 * AB, size=FULL_SIZE)
    assert r.data == data


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def unaligned_and_narrow_incr(dut: Any) -> None:
    m, s, _, _ = await _setup(dut)
    if LITE:
        return
    for addr, n, size in ((BASE + 0x2001, 3 * AB + 2, AB), (BASE + 0x2102, 10, 2),
                          (BASE + 0x2201, 7, 1), (BASE + 0x2303, 5, 0)):  # fmt: skip
        size = min(size, AB)
        data = _data(n, addr)
        assert (await m.write(addr, data, size=size.bit_length() - 1)).resp == AxiResp.OKAY
        assert _mem(s, addr - OFFSET, n) == data, hex(addr)
        r = await m.read(addr, n, size=size.bit_length() - 1)
        assert r.data == data, hex(addr)


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def wrap_and_fixed_bursts(dut: Any) -> None:
    """WRAP and FIXED become single AHB transfers in the AXI address order. cocotbext-axi
    does not compute WRAP addresses, so the stimulus is full-width and aligned (every lane
    strobed, beat k carrying data[k]); the bridge's wrapped order is checked on the AHB log.
    With different widths PULP axi_dw_converter may answer SLVERR (documented limit): the
    test then requires SLVERR or correct data, never a hang."""
    m, s, _, _ = await _setup(dut)
    if LITE:
        return
    boundary = BASE + 0x3000
    addr = boundary + 2 * AB  # a 4-beat WRAP starting at beat 2 of its boundary
    assert addr % AB == 0
    data = _data(4 * AB, 3)
    order = [2, 3, 0, 1]
    start = len(s.log)
    wr = await m.write(addr, data, size=FULL_SIZE, burst=AxiBurstType.WRAP)
    if AB != HB:
        if wr.resp == SLVERR:
            return
        r = await m.read(addr, 4 * AB, size=FULL_SIZE, burst=AxiBurstType.WRAP)
        assert r.resp == SLVERR or r.data == data
        return
    assert wr.resp == AxiResp.OKAY
    assert [a.addr for a in s.log[start:]] == [boundary - OFFSET + k * AB for k in order]
    assert all((a.trans, a.burst) == (NONSEQ, 0) for a in s.log[start:])
    for i, k in enumerate(order):
        assert _mem(s, boundary - OFFSET + k * AB, AB) == data[i * AB : (i + 1) * AB]
    start = len(s.log)
    r = await m.read(addr, 4 * AB, size=FULL_SIZE, burst=AxiBurstType.WRAP)
    assert (r.data, r.resp) == (data, AxiResp.OKAY)
    assert [a.addr for a in s.log[start:]] == [boundary - OFFSET + k * AB for k in order]
    start = len(s.log)
    wr = await m.write(boundary, data, size=FULL_SIZE, burst=AxiBurstType.FIXED)
    assert wr.resp == AxiResp.OKAY
    assert [a.addr for a in s.log[start:]] == [boundary - OFFSET] * 4
    assert all((a.trans, a.burst) == (NONSEQ, 0) for a in s.log[start:])
    assert _mem(s, boundary - OFFSET, AB) == data[3 * AB :]


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def hresp_error_becomes_slverr(dut: Any) -> None:
    """HRESP ERROR is SLVERR on exactly the R beats that read the failing word, and on B; the
    burst still completes. Errors on the first and on the last beat too."""
    m, s, _, w = await _setup(dut)
    bad = BASE + 0x4000 + 2 * AB
    s.err_addrs.add((bad - OFFSET) & ~(HB - 1))
    assert (await m.write(bad, _data(AB, 4))).resp == AxiResp.SLVERR
    assert (await m.read(bad, AB)).resp == AxiResp.SLVERR
    s.err_addrs.clear()
    if not LITE:
        base = BASE + 0x4000
        for k in (0, 2, 3):  # the beat that fails
            bad = base + k * AB
            word = (bad - OFFSET) & ~(HB - 1)
            s.err_addrs = {word}
            start = len(s.log)
            wr = await m.write(base, _data(4 * AB, 5), size=FULL_SIZE)
            assert wr.resp == AxiResp.SLVERR, k
            covered = {b for a in s.log[start:] for b in range(a.addr, a.addr + (1 << a.size))}
            assert covered == set(range(base - OFFSET, base - OFFSET + 4 * AB)), k
            nr = len(w.r)
            r = await m.read(base, 4 * AB, size=FULL_SIZE)
            assert r.resp == AxiResp.SLVERR, k
            beats = w.r[nr:]
            assert len(beats) == 4 and beats[-1].last and not any(b.last for b in beats[:-1])
            failing = {i for i, b in enumerate(beats) if b.resp == SLVERR}
            lo = base - OFFSET
            touching = {i for i in range(4) if lo + i * AB < word + HB and word < lo + (i + 1) * AB}
            assert k in failing and failing <= touching, (k, failing, touching)
        s.err_addrs = set()
    assert (await m.read(BASE + 0x4100, AB)).resp == AxiResp.OKAY


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def hprot_from_axprot_axcache(dut: Any) -> None:
    """HPROT = {AxCACHE[1], AxCACHE[0], AxPROT[0], !AxPROT[2]}; HNONSEC = AxPROT[1], on every
    AHB transfer of the write and of the read."""
    m, s, _, _ = await _setup(dut)
    for prot in range(8):
        for cache in (0, 1, 2, 3) if not LITE else (3,):
            extra = {} if LITE else {"cache": cache}
            start = len(s.log)
            wr = await m.write(BASE + 0x600, _data(AB, prot), prot=AxiProt(prot), **extra)
            mid = len(s.log)
            r = await m.read(BASE + 0x600, AB, prot=AxiProt(prot), **extra)
            assert (wr.resp, r.resp) == (AxiResp.OKAY, AxiResp.OKAY)
            hprot = cache << 2 | (prot & 1) << 1 | (1 - (prot >> 2 & 1))
            expect = hprot | (prot >> 1 & 1) << 4
            writes, reads = s.log[start:mid], s.log[mid:]
            assert writes and all(a.write and a.prot == expect for a in writes), (prot, cache)
            assert reads and all(not a.write and a.prot == expect for a in reads), (prot, cache)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def outstanding_ids_and_backpressure(dut: Any) -> None:
    """Concurrent transactions with different IDs, AW and AR in the same cycle, R and B
    back-pressure: each completes with its own data, and the AHB side runs them one at a
    time (each burst's transfers contiguous)."""
    m, s, _, _ = await _setup(dut)
    if LITE:
        return
    m.read_if.r_channel.set_pause_generator(_pauses(7, 0.4))
    m.write_if.b_channel.set_pause_generator(_pauses(8, 0.4))
    ids = 1 << IDW
    blocks = [(BASE + 0x5000 + 0x100 * k, _data(4 * AB, 10 + k)) for k in range(6)]
    for a, d in blocks:  # known contents first
        assert (await m.write(a, d, size=FULL_SIZE)).resp == AxiResp.OKAY
    new = [_data(4 * AB, 20 + k) for k in range(3)]
    # Three writes (new data into blocks 0-2) and three reads (blocks 3-5) at once.
    evs = [m.init_write(blocks[k][0], new[k], awid=k % ids, size=FULL_SIZE) for k in range(3)]
    evs += [m.init_read(blocks[k][0], 4 * AB, arid=(k + 1) % ids, size=FULL_SIZE)
            for k in range(3, 6)]  # fmt: skip
    for k, ev in enumerate(evs):
        await ev.wait()
        assert ev.data.resp == AxiResp.OKAY, k
        if k >= 3:
            assert ev.data.data == blocks[k][1], k
    for k in range(3):
        assert _mem(s, blocks[k][0] - OFFSET, 4 * AB) == new[k], k
    # Serialized: no two bursts interleave on the AHB side.
    owner = [(a.addr - (BASE - OFFSET) - 0x5000) // 0x100 for a in s.log
             if 0 <= a.addr - (BASE - OFFSET) - 0x5000 < 0x600]  # fmt: skip
    runs = [owner[i] for i in range(len(owner)) if i == 0 or owner[i] != owner[i - 1]]
    for blk in range(6):  # one run for the first write, one per later access
        assert runs.count(blk) == 2, (blk, runs)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def write_gaps_and_full_read_buffer(dut: Any) -> None:
    """Write data with gaps, and a read whose R channel stalls long enough to fill the
    bridge's R buffer: the data is right, and (equal widths: the core sees the beats as sent;
    a width converter reshapes them) the AHB burst idles and restarts with NONSEQ."""
    m, s, rules, _ = await _setup(dut)
    if LITE:
        return
    addr, n = BASE + 0x6000, 16 * AB
    data = _data(n, 30)
    m.write_if.w_channel.set_pause_generator(_pauses(31, 0.5))
    before = rules.restarts
    assert (await m.write(addr, data, size=FULL_SIZE)).resp == AxiResp.OKAY
    assert _mem(s, addr - OFFSET, n) == data
    assert AB != HB or rules.restarts > before, "no IDLE-then-NONSEQ restart in a gappy write"
    m.write_if.w_channel.set_pause_generator(None)
    m.read_if.r_channel.set_pause_generator(_pauses(32, 0.8))
    before = rules.restarts
    r = await m.read(addr, n, size=FULL_SIZE)
    assert (r.data, r.resp) == (data, AxiResp.OKAY)
    assert AB != HB or rules.restarts > before, "no restart after the R buffer filled"


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def exclusive_accesses_get_okay(dut: Any) -> None:
    """AxLOCK: the bridge has no exclusive monitor, so an exclusive access gets OKAY (the
    exclusive fails), never EXOKAY; like any slave without exclusive support it performs
    the access (AXI: such a slave ignores AxLOCK)."""
    m, s, _, _ = await _setup(dut)
    if LITE:
        return
    addr = BASE + 0x7000
    lock = AxiLockType.EXCLUSIVE
    r = await m.read(addr, AB, size=FULL_SIZE, lock=lock)
    assert r.resp == AxiResp.OKAY
    wr = await m.write(addr, _data(AB, 40), size=FULL_SIZE, lock=lock)
    assert wr.resp == AxiResp.OKAY
    assert _mem(s, addr - OFFSET, AB) == _data(AB, 40)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def every_handshake_ordering(dut: Any) -> None:
    """VALID first, READY first and same cycle on B and R, where the master model answers
    (D38); lazy READY also proves the bridge's BVALID / RVALID do not wait for READY."""
    m, _, _, w = await _setup(dut)
    sinks = {"b": (m.write_if.b_channel, dut.s_bvalid), "r": (m.read_if.r_channel, dut.s_rvalid)}
    handshaking = Handshaking(dut.aclk_i, sinks)

    async def traffic() -> None:
        for k in range(16):
            addr = BASE + 0x8000 + AB * k
            data = _data(AB, 50 + k)
            assert (await m.write(addr, data)).resp == AxiResp.OKAY
            r = await m.read(addr, AB)
            assert (r.data, r.resp) == (data, AxiResp.OKAY)

    for mode in ("eager", "lazy", "random"):
        handshaking.set(mode, seed=23)
        await traffic()
    for _ in range(ROUNDS):  # same-cycle handshakes come by chance: run until seen
        if not w.orderings.missing(("b", "r")):
            break
        await traffic()
    assert w.orderings.missing(("b", "r")) == {}, w.orderings.counts
