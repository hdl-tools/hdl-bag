# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Reference model: where does an access from a master go? Written from the IR only.

The spec semantics (workbook guide + owner decisions), not the generator's tables:
per fabric, apply the master's enabled alias, find the Memory_Map region (from
spec.memory_map, not the resolver's addr_rules), then deny on RO/WO, secure-only with a
non-secure access, or Access_Rules; a miss or denial goes to the master's default slave
or returns an error, and a denial by the default slave itself returns an error (D32). A bridge forwards to its downstream fabric (as master). Memory_Map
addresses are system addresses throughout; a pass-through N bridge only changes what
the slaves behind it see (the offset from its window base). A slave sees the low bits
of its address (D29).

Error injection: the word at ERROR_WORD bytes below each region's end makes its slave
answer with an error, which reaches the master unless the slave's PSLVERR is tied.

Two semantics are modelled but cannot occur in a check-clean spec, so the corpus does
not reach them: a PROT-tied port reaching a secure-only region (SEC-PROT-TIED forbids
it; D28 makes it non-secure) and a master-side tied PSLVERR (PORTOPT-ERROR-MASKED warns,
and the corpus is warning-free).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from hdl_bag.bursts import FIXED, INCR, WRAP, refusing_converter
from hdl_bag.ir import Spec

ERROR_WORD = 8  # region end - 8 answers with a slave error


@dataclass(frozen=True)
class Outcome:
    slave: str | None  # None: the fabric answers (error, or a masked one)
    address: int = 0  # the address as the slave sees it
    masked: bool = False  # an error that never reaches the master: it sees OKAY
    slave_error: bool = False  # the slave answers with an error that reaches the master
    nonsec: bool = False  # the non-secure bit as the slave sees it
    bridges: tuple[str, ...] = ()  # bridges crossed on the way
    miss: str | None = None  # fabric error: the failing fabric's miss response (DECERR, ...)
    hits_error_word: bool = False  # the slave answers with an error, masked or not
    word_bytes: int = 4  # the slave's data width
    words: frozenset[int] = frozenset()  # slave words (index) the access may reach


def _option(spec: Spec, port: str, column: str) -> str | None:
    opt = next((o for o in spec.port_options if o.port == port), None)
    return None if opt is None else str(getattr(opt, column))


def prot_tied(spec: Spec, port: str) -> bool:
    return _option(spec, port, "prot") in ("tie-0", "tie-default")


def pslverr_tied(spec: Spec, port: str) -> bool:
    return _option(spec, port, "pslverr") in ("tie-0", "tie-default")


def route(spec: Spec, master: str, addr: int, write: bool, nonsec: bool,
          pins: dict[str, bool]) -> Outcome:  # fmt: skip
    fabrics = {f.name: f for f in spec.fabrics}
    bridges = {b.name: b for b in spec.bridges}
    slaves = {s.name: s for s in spec.slaves}
    fabric = next(f for f in spec.fabrics if any(p.port == master for p in f.master_ports))
    # Bytes the access covers, relative to addr: [lo, hi). A write covers its 4 bytes
    # (added zero-strobe beats write nothing); a read can cover more (_read_cover).
    lo, hi = _read_cover(spec, master, fabric, addr) if not write else (0, 4)
    nonsec = nonsec or prot_tied(spec, master)
    masked = pslverr_tied(spec, master)  # a master-side tied PSLVERR hides errors
    offset = 0  # subtracted by pass-through N bridges on the way
    crossed: list[str] = []
    for _ in range(len(spec.fabrics)):
        decode = next(d for d in fabric.decode if d.master == master)
        for a in decode.aliases:
            on = a.enable == "always" or pins.get(a.enable, False)
            if on and a.base <= addr < a.base + a.size:
                addr = a.to + (addr - a.base)
                break
        target = None
        region = next((r for r in spec.memory_map
                       if r.fabric == fabric.name and r.base <= addr < r.base + r.size), None)  # fmt: skip
        if region is not None:
            denied = (write and region.access == "RO") or (not write and region.access == "WO")
            denied |= region.secure and nonsec
            ar = next((a for a in fabric.access_rules
                       if a.master == master and a.slave == region.slave), None)  # fmt: skip
            if ar is not None:
                denied |= ar.access == "none"
                denied |= (write and ar.access == "RO") or (not write and ar.access == "WO")
            target = None if denied else region.slave
        if target is None:
            to_default = decode.miss == "default-slave" and decode.default_slave is not None
            # D32: a denial never reaches the slave that denied it.
            if to_default and region is not None and region.slave == decode.default_slave:
                to_default = False
            if not to_default:
                return Outcome(None, masked=masked, bridges=tuple(crossed), miss=decode.miss)
            target = decode.default_slave
            assert target is not None  # to_default implies a default slave
        if target in bridges:
            b = bridges[target]
            # The nearest pass-through N window sets what the slaves behind it see; a
            # pass-through Y bridge keeps the offset its upstream fabric has.
            offset = offset if b.pass_through else b.window_base
            fabric, master = fabrics[b.downstream], b.name
            if not write and fabric.protocol == "AXI4-Lite":
                # An AXI4-Lite beat is full width: the read now covers whole aligned beats.
                lite = fabric.data_width // 8
                lo = (addr + lo) // lite * lite - addr
                hi = -(-(addr + hi) // lite) * lite - addr
            elif b.template == "bag_axi2apb" and fabric.data_width == 8:
                # From AXI4-Lite this path runs on AXI4 ports at full size: axi_dw_downsizer
                # starts at the address, so the bytes of the beat below it are not accessed.
                lo = max(lo, 0)
            nonsec = nonsec or prot_tied(spec, b.name)
            masked = masked or pslverr_tied(spec, b.name)
            crossed.append(b.name)
            continue
        word = slaves[target].data_width // 8  # a slave answers per data word
        width = slaves[target].addr_width
        visible = (addr - offset) & ((1 << width) - 1)
        # The slave decides on the address it sees (a redirected miss can alias its error word).
        # A read reaches every slave word of the master's beat (SPAN bytes): width
        # converters read all of it.
        # The access reaches every slave word its byte interval touches.
        first = (visible + lo) // word * word
        words = {a // word for a in range(first, visible + hi, word)}
        error = bool(words & {e // word for e in error_words(spec, target)})
        return Outcome(
            target, visible, masked=masked, slave_error=error and not masked, nonsec=nonsec,
            bridges=tuple(crossed), hits_error_word=error, word_bytes=word, words=frozenset(words),
        )  # fmt: skip
    raise AssertionError("bridge chain longer than the number of fabrics")


def _read_cover(spec: Spec, master: str, fabric: Any, addr: int) -> tuple[int, int]:
    """Bytes [lo, hi), relative to ADDR, a 32-bit read by MASTER covers on FABRIC.

    AXI4 masters issue narrow (size 2) transfers: the 4 addressed bytes. An AXI4-Lite beat is
    always full width: on an AXI4-Lite fabric (whose upsizers keep the full width) it covers
    the whole aligned beat; behind a pc onto an AXI4 fabric it becomes an AXI4 transfer of
    the Lite width, which covers from the address to the end of its aligned beat (AXI
    unaligned-transfer rule). Width converters further on read all of what is covered.
    """
    m = next(x for x in spec.masters if x.name == master)
    if m.protocol != "AXI4-Lite":
        return 0, 4
    beat = m.data_width // 8
    if fabric.protocol == "AXI4-Lite":
        beat = max(beat, fabric.data_width // 8)
        start = addr // beat * beat
        return start - addr, start + beat - addr
    return 0, addr // beat * beat + beat - addr


def error_words(spec: Spec, slave: str) -> set[int]:
    """Slave-visible addresses at which SLAVE answers with an error (D29 low bits).

    None for a slave whose PSLVERR is tied: such a slave never errors, by definition.
    """
    if pslverr_tied(spec, slave):
        return set()
    width = next(s.addr_width for s in spec.slaves if s.name == slave)
    return {(r.base + r.size - ERROR_WORD) & ((1 << width) - 1)
            for r in spec.memory_map if r.slave == slave}  # fmt: skip


# ------------------------------------------------------------------- what each test can check
# Each returns what its test would work on, so a test applies exactly when this finds something.


def reach(spec: Spec, region: Any, ports: list[Any]) -> list[Any]:
    """The PORTS that can address REGION (16 bytes per port) and write it directly."""
    widths = {m.name: m.addr_width for m in spec.masters}
    return [p for p in ports if region.base + 16 * len(ports) < 1 << widths[p.port]
            and route(spec, p.port, region.base, True, False, {}).slave == region.slave
            and not route(spec, p.port, region.base, True, False, {}).bridges]  # fmt: skip


def path_domains(spec: Spec, m: Any, exp: Outcome) -> set[str]:
    """The clock domains an access from M to its target passes through: M's, its fabric's, each
    bridge's downstream fabric's and the slave's."""
    fabrics = {f.name: f for f in spec.fabrics}
    bridges = {b.name: b for b in spec.bridges}
    fabric = next(f for f in spec.fabrics if any(p.port == m.name for p in f.master_ports))
    out = {m.domain, fabric.domain, *(fabrics[bridges[b].downstream].domain for b in exp.bridges)}
    if exp.slave is not None:
        out.add(next(s.domain for s in spec.slaves if s.name == exp.slave))
    return out


def crossing_masters(spec: Spec) -> list[str]:
    """Masters whose own traffic words (private_words, as _traffic uses them) cross a clock
    domain boundary."""
    out = []
    for i, m in enumerate(spec.masters):
        words = private_words(spec, m, i, 8)
        if words and len(path_domains(spec, m, route(spec, m.name, words[0], True, False, {}))) > 1:
            out.append(m.name)
    return out


def blocking_path(spec: Spec, f: Any) -> tuple[Any, Any, int, tuple[Any, Any, int]] | None:
    """On AXI fabric F: (a, x, word, (b, y, word)), where master A reaches slave X and a different
    master B reaches slave Y, both directly, with no slave-side adapter that could take a stalled
    request off the head of the queue."""
    if f.protocol not in ("AXI4", "AXI4-Lite"):
        return None
    adapted = {a.port for a in spec.adapters}
    on_fabric = {p.port for p in f.slave_ports}
    slaves = [s for s in spec.slaves if s.name in on_fabric and s.name not in adapted
              and s.protocol in ("AXI4", "AXI4-Lite")]  # fmt: skip
    ports = [p.port for p in _master_ports(spec, f)]
    masters = [m for m in spec.masters if m.name in ports]

    def word(m: Any, s: Any) -> int | None:
        found = plain_word(spec, m, s)
        return found[0] if found and not found[1].bridges else None

    for x in slaves:
        for y in slaves:
            if x is y:
                continue
            for a in masters:
                for b in masters:
                    if (
                        a is not b
                        and (wa := word(a, x)) is not None
                        and (wb := word(b, y)) is not None
                    ):
                        return a, x, wa, (b, y, wb)
    return None


def forced_resp_paths(spec: Spec) -> list[tuple[Any, Any, int]]:
    """(master, AXI slave, word): each master path to an AXI slave that a forced response can
    travel back along."""
    out = []
    for m in spec.masters:
        for s in spec.slaves:
            if s.protocol in ("AXI4", "AXI4-Lite") and (found := plain_word(spec, m, s)):
                out.append((m, s, found[0]))
    return out


def exokay_paths(spec: Spec) -> list[tuple[Any, Any, int]]:
    """Those an exclusive access reaches unchanged: AXI4 master and slave on one fabric, with no
    width or protocol converter, and AxLOCK driven at both ports."""
    converters = {a.port for a in spec.adapters if a.type in ("dw", "pc")}
    tied = {o.port for o in spec.port_options if o.lock in ("tie-0", "tie-default")}
    return [
        (m, s, word)
        for m, s, word in forced_resp_paths(spec)
        if m.protocol == "AXI4" and s.protocol == "AXI4"
        and not {m.name, s.name} & (converters | tied)
        and not route(spec, m.name, word, True, False, {}).bridges
    ]  # fmt: skip


def _master_ports(spec: Spec, fabric: Any) -> list[Any]:
    names = {m.name for m in spec.masters}
    return [p for p in sorted(fabric.master_ports, key=lambda p: p.index) if p.port in names]


def contention(spec: Spec, fabric: Any, secure_too: bool = False) -> tuple[Any, list[Any]] | None:
    """A slave region on FABRIC that several masters write directly, and those masters (by
    Port index)."""
    ports = _master_ports(spec, fabric)
    regions = [
        r for r in spec.memory_map if r.fabric == fabric.name and (secure_too or not r.secure)
    ]
    if len(ports) < 2 or not regions:
        return None
    region = max(regions, key=lambda r: len(reach(spec, r, ports)))
    ports = reach(spec, region, ports)
    return (region, ports) if len(ports) >= 2 else None


def private_words(spec: Spec, m: Any, index: int, count: int, direct: bool = False) -> list[int]:
    """COUNT words only master M uses (offset by INDEX) in the lowest region it can write and
    read back without errors; with DIRECT, of an AXI slave on M's own fabric (no bridge)."""
    axi_slaves = {s.name for s in spec.slaves if s.protocol in ("AXI4", "AXI4-Lite")}
    for r in sorted(spec.memory_map, key=lambda r: r.base):
        words = [r.base + 0x100 * index + 4 * k for k in range(count)]
        if words[-1] >= min(1 << m.addr_width, r.base + r.size - 16):
            continue
        ok = True
        for a in words:
            wr = route(spec, m.name, a, True, False, {})
            rd = route(spec, m.name, a, False, False, {})
            # Both reach one slave word without error (a denied write that a default slave
            # takes would not read back).
            ok &= wr.slave is not None and not wr.slave_error and not wr.masked
            ok &= (wr.slave, wr.address, wr.slave_error) == (rd.slave, rd.address, rd.slave_error)
            if direct:
                ok &= not wr.bridges and wr.slave in axi_slaves
        if ok:
            return words
    return []


def midpoint_path(spec: Spec, f: Any) -> tuple[Any, list[int], int] | None:
    """On AXI shared bus F: the master with the widest ID (so per-ID limits do not bind), words
    of an AXI slave right behind the mid-point, and how many reads the mid-point holds."""
    if f.protocol not in ("AXI4", "AXI4-Lite") or f.topology != "shared-bus" or f.midpoint is None:
        return None
    stages = {"none": 0, "cut": 1, "multicut-2": 2, "multicut-3": 3}[f.midpoint.slice]
    buffered = f.midpoint.fifo_depth + 2 * stages
    names = {p.port for p in f.master_ports}
    ms = [x for x in spec.masters if x.name in names]
    if not ms:
        return None
    m = max(ms, key=lambda x: x.id_width or 0)
    words = private_words(spec, m, 0, buffered + 4, direct=True)
    return (m, words, buffered) if words else None


def crossbar_path(spec: Spec, f: Any, m: Any) -> tuple[list[int], Outcome] | None:
    """Words of an AXI4 slave on crossbar F that master M reaches with no adapter between."""
    adapted = {a.port for a in spec.adapters}
    if f.template != "bag_axi_xbar" or not f.max_mst_trans:
        return None
    ports = {p.port for p in f.master_ports} | {p.port for p in f.slave_ports}
    if m.name not in ports or m.name in adapted or not m.id_width:
        return None
    words = private_words(spec, m, 0, f.max_mst_trans + 2, direct=True)
    exp = route(spec, m.name, words[0], False, False, {}) if words else None
    if exp is None or exp.slave not in ports or exp.slave in adapted:
        return None
    return words, exp


def narrow_apb_path(spec: Spec, m: Any, s: Any) -> tuple[int, Outcome] | None:
    """Word 0x40 of S, if AXI master M reaches this APB slave narrower than 32 bits, cleanly,
    through a bag_axi2apb."""
    if (
        m.protocol not in ("AXI4", "AXI4-Lite")
        or s.protocol not in ("APB3", "APB4")
        or s.data_width >= 32
    ):
        return None
    found = plain_word(spec, m, s)
    axi2apb = {b.name for b in spec.bridges if b.template == "bag_axi2apb"}
    return found if found and axi2apb & set(found[1].bridges) else None


def pstrb_path(spec: Spec, m: Any, s: Any) -> tuple[int, Outcome] | None:
    """Word 0x40 of S, if AXI master M reaches this 32-bit APB4 slave cleanly."""
    if m.protocol not in ("AXI4", "AXI4-Lite") or s.protocol != "APB4" or s.data_width != 32:
        return None
    return plain_word(spec, m, s)


# --------------------------------------------------------------------------------------- bursts


def burst_path(spec: Spec, m: Any, index: int) -> tuple[list[int], int | None]:
    """What the plain burst test sends for AXI4 master M, INDEX in spec.masters: 4 words it can
    write and read back (or none), and a probe address that misses on M's own fabric (or None)."""
    if m.protocol != "AXI4":
        return [], None
    miss = next((a for a in probes(spec) if a + 16 < 1 << m.addr_width and all(
        route(spec, m.name, a + 4 * k, w, False, {}).slave is None
        and not route(spec, m.name, a + 4 * k, w, False, {}).bridges
        for k in range(4) for w in (True, False))), None)  # fmt: skip
    return private_words(spec, m, index, 4), miss


def burst_addresses(addr: int, size: int, beats: int, burst: int) -> list[int]:
    """The address of each beat of an AXI burst (AMBA AXI A3.4.1), first beat as given."""
    step = 1 << size
    aligned = addr // step * step
    if burst == FIXED:
        return [addr] * beats
    if burst == INCR:
        return [addr, *(aligned + n * step for n in range(1, beats))]
    assert burst == WRAP and addr == aligned and beats in (2, 4, 8, 16), (addr, size, beats)
    span = step * beats
    block = addr // span * span
    return [block + (addr - block + n * step) % span for n in range(beats)]


def burst_bytes(addr: int, size: int, beats: int, burst: int, data: bytes) -> list[tuple[int, int]]:
    """(address, value) of every byte a burst of aligned beats writes, in order."""
    step = 1 << size
    assert len(data) == step * beats and addr % step == 0, (addr, size, beats, len(data))
    out = []
    for n, a in enumerate(burst_addresses(addr, size, beats, burst)):
        out += [(a + k, data[n * step + k]) for k in range(step)]
    return out


def check_burst_write(
    where: str, want: list[tuple[int, int]], written: list[tuple[int, int]]
) -> None:
    """Exactly these bytes stored, as many times as written: a FIXED burst repeats addresses."""
    assert sorted(written) == sorted(want), (
        f"{where}: stored {_bytes(written)}, wrote {_bytes(want)}"
    )


def burst_refused(spec: Spec, m: Any, exp: Outcome, burst: int, beats: int) -> bool:
    """Whether a converter on the way answers this AXI4 burst with SLVERR (hdl_bag.bursts, which
    bag check's WRAP warning uses too)."""
    return refusing_converter(spec, m.name, exp.bridges, exp.slave, burst, beats) is not None


def widest_on_path(spec: Spec, m: Any, exp: Outcome) -> int:
    """Bytes in the widest bus between M and its target: M's fabric, each bridge's downstream
    fabric, and the slave."""
    fabrics = {f.name: f for f in spec.fabrics}
    bridges = {b.name: b for b in spec.bridges}
    fabric = next(f for f in spec.fabrics if any(p.port == m.name for p in f.master_ports))
    widths = [fabric.data_width, *(fabrics[bridges[b].downstream].data_width for b in exp.bridges)]
    return max(max(widths) // 8, exp.word_bytes)


def burst_shapes(spec: Spec, m: Any, s: Any) -> list[tuple[str, int, int, int, int]]:
    """(name, address, burst, size, beats) of the bursts AXI4 master M can send to slave S,
    each beat reaching S cleanly: full-width INCR, FIXED and WRAP; narrow INCR (where S can take
    a partial write); and an INCR that ends exactly on a 4 KB boundary. WRAP and FIXED stay full
    width because cocotbext-axi places lanes as if a burst only increments."""
    found = plain_word(spec, m, s) if m.protocol == "AXI4" else None
    if found is None:
        return []
    word = found[0]
    bus = m.data_width // 8
    full = bus.bit_length() - 1
    block = word // (4 * bus) * (4 * bus)
    shapes = [
        ("incr", word // bus * bus, INCR, full, 4),
        ("incr-long", word // bus * bus, INCR, full, 8),
        ("fixed", word // bus * bus, FIXED, full, 2),
        ("wrap", block + bus, WRAP, full, 4),
        ("4k-edge", (word | 0xFFF) + 1 - 4 * bus, INCR, full, 4),
    ]
    if takes_partial_writes(spec, s):
        shapes += [(f"narrow-{1 << z}", word, INCR, z, 4) for z in range(full) if z <= 2]

    def clean(addr: int, size: int, beats: int, burst: int) -> bool:
        last = addr + (beats << size)
        if addr >> 12 != (last - 1) >> 12 or last > 1 << m.addr_width:
            return False
        for a in burst_addresses(addr, size, beats, burst):
            for write in (True, False):
                for b in range(a, a + (1 << size), 4):
                    o = route(spec, m.name, b, write, False, {})
                    if (
                        o.slave != s.name
                        or o.slave_error
                        or o.masked
                        or o.bridges != found[1].bridges
                    ):
                        return False
        return True

    return [x for x in shapes if clean(x[1], x[3], x[4], x[2])]


def takes_partial_writes(spec: Spec, slave: Any) -> bool:
    """False when a partial write onto SLAVE's word cannot be expressed: an APB slave wider
    than a byte with no PSTRB (APB3, or PSTRB tied)."""
    if slave.data_width == 8 or slave.protocol not in ("APB3", "APB4"):
        return True
    return slave.protocol == "APB4" and _option(spec, slave.name, "pstrb") not in (
        "tie-0",
        "tie-default",
    )


def plain_word(spec: Spec, master: Any, slave: Any) -> tuple[int, Outcome] | None:
    """Word 0x40 of SLAVE, and its route, if a secure write and read from MASTER reach it with
    no error."""
    word = next(r for r in spec.memory_map if r.slave == slave.name).base + 0x40
    if word >= 1 << master.addr_width:
        return None
    outs = [route(spec, master.name, word, w, False, {}) for w in (True, False)]
    ok = all(o.slave == slave.name and not (o.slave_error or o.masked) for o in outs)
    return (word, outs[0]) if ok else None


def check_write(where: str, exp: Outcome, data: bytes, written: list[tuple[int, int]]) -> None:
    """The bytes the slave stored for a write of DATA at EXP.address: exactly those, each once.
    After an error a split access can have stored only part of it, but nothing else. A beat
    with no strobes stores nothing, so one a width converter adds is not visible here."""
    want = {exp.address + k: b for k, b in enumerate(data)}
    got = dict(written)
    assert len(got) == len(written), f"{where}: a byte written twice: {_bytes(written)}"
    if exp.hits_error_word:
        assert got.items() <= want.items(), (
            f"{where}: stored {_bytes(written)}, wrote {_bytes(want.items())}"
        )
    else:
        assert got == want, f"{where}: stored {_bytes(written)}, wrote {_bytes(want.items())}"


def check_split_write_error(where: str, address: int, data: bytes, step: int, failed: int,
                            written: list[tuple[int, int]]) -> None:  # fmt: skip
    """A write of DATA at ADDRESS, split into beats of STEP bytes, whose beat at FAILED answered
    with an error: every beat before it stored, it did not, and nothing else did. Beats after
    it may or may not have run."""
    want = {address + k: b for k, b in enumerate(data)}
    got = dict(written)
    assert len(got) == len(written), f"{where}: a byte written twice: {_bytes(written)}"
    assert got.items() <= want.items(), (
        f"{where}: stored {_bytes(written)}, wrote {_bytes(want.items())}"
    )
    before = {a: b for a, b in want.items() if a < failed}
    assert before.items() <= got.items(), f"{where}: beats before the error lost: {_bytes(written)}"
    assert not any(failed <= a < failed + step for a in got), (
        f"{where}: stored the failed beat: {_bytes(written)}"
    )


def check_read(where: str, exp: Outcome, length: int, touched: list[int]) -> None:
    """The beat addresses a read of LENGTH bytes at EXP.address made the slave take: all the
    words it asked for, no address twice (a read can have side effects), and nothing beyond
    what its beats can cover on the way (EXP.words). A wide slave behind a narrow fabric can
    take two addresses in one word."""
    word = exp.word_bytes
    seen = {a // word for a in touched}
    assert len(set(touched)) == len(touched), (
        f"{where}: read an address twice: {[hex(a) for a in touched]}"
    )
    need = {a // word for a in range(exp.address, exp.address + length)}
    assert need <= seen, f"{where}: read words {_words(seen, word)}, needs {_words(need, word)}"
    extra = seen - exp.words - need
    assert not extra, f"{where}: read words {_words(extra, word)}, beyond {_words(exp.words, word)}"


def _bytes(rows: Iterable[tuple[int, int]]) -> list[tuple[str, str]]:
    return [(hex(a), f"{v:02x}") for a, v in sorted(rows)]


def _words(words: Iterable[int], size: int) -> list[str]:
    return [hex(w * size) for w in sorted(words)]


def probes(spec: Spec) -> list[int]:
    """Interesting addresses: per region its first, middle and last word, the error word,
    the words just before and after it, so the first and last word of every hole; the first
    and last word of the address space; both edges of every alias window."""
    out: set[int] = {0}
    out |= {(1 << m.addr_width) - 4 for m in spec.masters}
    for r in spec.memory_map:
        out |= {r.base, r.base + r.size // 2, r.base + r.size - 4, r.base + r.size - ERROR_WORD,
                r.base + r.size, r.base - 4}  # fmt: skip
    for f in spec.fabrics:
        for d in f.decode:
            for a in d.aliases:
                out |= {a.base - 4, a.base, a.base + a.size - 4, a.base + a.size}
    top = max((1 << m.addr_width for m in spec.masters), default=1 << 32)
    return sorted(a for a in out if 0 <= a < top)
