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

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from hdl_bag.bursts import FIXED, INCR, WRAP, refusing_converter
from hdl_bag.ir import Spec
from hdl_bag.protocols import tie_value

ERROR_WORD = 8  # region end - 8 answers with a slave error
# HPROT of the AHB-Lite masters' plain traffic: cacheable, so AxCACHE[1] is high behind
# bag_ahb2axi and no converter refuses it (D104). The route test draws every other value.
TRAFFIC_HPROT = 0b1011


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
    may_refuse: bool = False  # D104: a converter on the way can answer SLVERR
    span: int = 0  # bytes of the access the narrowest bus on the way carries


def _option(spec: Spec, port: str, column: str) -> str | None:
    opt = next((o for o in spec.port_options if o.port == port), None)
    return None if opt is None else str(getattr(opt, column))


def pin_sets(spec: Spec) -> list[dict[str, bool]]:
    """The alias-enable pin settings the routing test runs with: all off, each on alone, and
    all on when there are several."""
    pins = list(spec.wrapper.extra_inputs)
    sets = [dict.fromkeys(pins, False)]
    sets += [{p: p == on for p in pins} for on in pins]  # one at a time
    if len(pins) > 1:
        sets.append(dict.fromkeys(pins, True))
    return sets


def prot_tied(spec: Spec, port: str) -> bool:
    return _option(spec, port, "prot") in ("tie-0", "tie-default")


def cache_after_tie(spec: Spec, port: str, cache: int) -> int:
    """The AxCACHE[1:0] PORT sends for CACHE: what Port_Options ties it to, if it does."""
    tie = _option(spec, port, "cache")
    return tie_value("awcache", tie) & 0b11 if tie in ("tie-0", "tie-default") else cache


def pslverr_tied(spec: Spec, port: str) -> bool:
    return _option(spec, port, "pslverr") in ("tie-0", "tie-default")


# One fabric's decision, in system addresses. route() follows them fabric by fabric; the
# formal decode properties (formal_gen.decode) evaluate them per region as their reference.


def aliased(fabric: Any, master: str, addr: int, pins: dict[str, bool]) -> int:
    """ADDR after MASTER's first enabled alias on FABRIC whose window holds it."""
    decode = next(d for d in fabric.decode if d.master == master)
    for a in decode.aliases:
        on = a.enable == "always" or pins.get(a.enable, False)
        if on and a.base <= addr < a.base + a.size:
            return int(a.to + (addr - a.base))
    return addr


def region_at(spec: Spec, fabric: Any, addr: int) -> Any | None:
    """The Memory_Map region of FABRIC holding ADDR (regions do not overlap), or None."""
    return next((r for r in spec.memory_map
                 if r.fabric == fabric.name and r.base <= addr < r.base + r.size), None)  # fmt: skip


def denied(fabric: Any, master: str, region: Any, write: bool, nonsec: bool) -> bool:
    """Whether REGION refuses MASTER's access: RO or WO, secure-only against a non-secure
    access, or the master's Access_Rules towards the region's slave."""
    deny = (write and region.access == "RO") or (not write and region.access == "WO")
    deny |= region.secure and nonsec
    ar = next((a for a in fabric.access_rules
               if a.master == master and a.slave == region.slave), None)  # fmt: skip
    if ar is not None:
        deny |= ar.access == "none"
        deny |= (write and ar.access == "RO") or (not write and ar.access == "WO")
    return deny


def fallback(fabric: Any, master: str, region: Any | None) -> str | None:
    """Where MASTER's miss (REGION None) or denial by REGION goes: its default slave, or
    None for an error. D32: a denial never reaches the slave that denied it."""
    decode = next(d for d in fabric.decode if d.master == master)
    if decode.miss != "default-slave" or decode.default_slave is None:
        return None
    if region is not None and region.slave == decode.default_slave:
        return None
    return str(decode.default_slave)


def window(spec: Spec, fabric: Any) -> int:
    """The system address FABRIC's address 0 stands for: the window base of the nearest
    pass-through N bridge on the way to it (a pass-through Y bridge keeps its upstream's),
    as route() tracks per access. Every master of a fabric sees the same translation
    (generation refuses a fabric whose masters differ), so the first master port decides."""
    bridges = {b.name: b for b in spec.bridges}
    for p in fabric.master_ports:
        b = bridges.get(p.port)
        if b is None:
            return 0
        if not b.pass_through:
            return int(b.window_base)
        return window(spec, next(f for f in spec.fabrics if f.name == b.upstream))
    return 0


def decide(spec: Spec, fabric: Any, master: str, addr: int, write: bool,
           nonsec: bool) -> str | None:  # fmt: skip
    """The slave port (slave or bridge name) FABRIC sends MASTER's access at ADDR (already
    aliased) to, or None for an error."""
    region = region_at(spec, fabric, addr)
    if region is not None and not denied(fabric, master, region, write, nonsec):
        return str(region.slave)
    return fallback(fabric, master, region)


def _axi4_downsizer_in_front(spec: Spec, port: str, fabric: Any, from_port: bool = False) -> bool:
    """Whether a dw adapter narrows the AXI4 path between FABRIC and PORT, walked in the direction
    of travel (adapters are in order from the port side; with FROM_PORT, a bridge's far end
    leads to FABRIC): a pc changes the protocol the next one sees."""
    chain = sorted((a for a in spec.adapters if a.port == port and a.fabric == fabric.name),
                   key=lambda a: a.chain_index, reverse=not from_port)  # fmt: skip
    pcs = [a for a in chain if a.type == "pc"]
    protocol = pcs[0].params["from"] if from_port and pcs else fabric.protocol
    for a in chain:
        if a.type == "pc":
            protocol = a.params["to"]
        elif a.type == "dw" and protocol == "AXI4" and int(a.params["to"]) < int(a.params["from"]):
            return True
    return False


def _downsizes_on_axi4(bridge: Any, up: Any, down: Any) -> bool:
    """Whether BRIDGE narrows from fabric UP to fabric DOWN with axi_dw_converter. From AXI4-Lite
    a bag_axi2apb instance is bag_axil2apb, on axi_lite_dw_converter, except onto 8 bits, which
    that converter cannot reach (the bag_axi2apb instance template)."""
    if up.data_width <= down.data_width:
        return False
    if bridge.template == "bag_axi2apb":
        return bool(up.protocol == "AXI4" or down.data_width == 8)
    return bridge.template in ("bag_axi2ahb", "bag_axi2axil")


def route(spec: Spec, master: str, addr: int, write: bool, nonsec: bool,
          pins: dict[str, bool]) -> Outcome:  # fmt: skip
    fabrics = {f.name: f for f in spec.fabrics}
    bridges = {b.name: b for b in spec.bridges}
    slaves = {s.name: s for s in spec.slaves}
    origin = master
    fabric = next(f for f in spec.fabrics if any(p.port == master for p in f.master_ports))
    # Bytes the access covers, relative to addr: [lo, hi). A write covers its 4 bytes
    # (added zero-strobe beats write nothing); a read can cover more (_read_cover).
    lo, hi = _read_cover(spec, master, fabric, addr) if not write else (0, 4)
    nonsec = nonsec or prot_tied(spec, master)
    masked = pslverr_tied(spec, master)  # a master-side tied PSLVERR hides errors
    offset = 0  # subtracted by pass-through N bridges on the way
    crossed: list[str] = []
    # Once a bus narrower than the bytes covered splits the access into transfers, each later
    # starts at its own address: an AXI4-Lite bus widens each to its beat, and a downsizer after
    # it starts back at the transfer's address (seed 202611250040).
    split = False
    # The narrowest bus so far, and the bytes of the access it carries (reads_per_word).
    narrowest, carried = fabric.data_width, hi - lo
    # Where the access now starts, relative to addr: a read that crossed AHB-Lite starts at its
    # aligned transfer (bag_axi2ahb_core aligns HADDR), so a downsizer later starts there; after
    # a split, at the first transfer.
    start = 0
    if fabric.data_width // 8 < hi - lo:  # the master's own converter splits it (202611250042)
        split, start = True, lo
    for _ in range(len(spec.fabrics)):
        addr = aliased(fabric, master, addr, pins)
        target = decide(spec, fabric, master, addr, write, nonsec)
        if target is None:
            decode = next(d for d in fabric.decode if d.master == master)
            return Outcome(None, masked=masked, bridges=tuple(crossed), miss=decode.miss)
        if target in bridges:
            b = bridges[target]
            # The nearest pass-through N window sets what the slaves behind it see; a
            # pass-through Y bridge keeps the offset its upstream fabric has.
            offset = offset if b.pass_through else b.window_base
            up, down = fabrics[b.upstream], fabrics[b.downstream]
            if _axi4_downsizer_in_front(spec, target, fabric) or _downsizes_on_axi4(b, up, down):
                lo = max(lo, start)  # axi_dw_downsizer starts at the address
            elif (b.template == "bag_axi2apb" and up.protocol == "AXI4-Lite"
                  and up.data_width > down.data_width):  # fmt: skip
                # bag_axil2apb's axi_lite_dw_converter (_downsizes_on_axi4) reads the whole
                # beat from its start: the transfers after it start there (review of #209,
                # generator seed 341).
                start = lo
            fabric, master = down, b.name
            if _axi4_downsizer_in_front(spec, b.name, fabric, from_port=True):
                lo = max(lo, start)  # axi_dw_downsizer starts at the address
            # After this hop's own trim: a bus narrower than what is still covered splits it.
            if not split and fabric.data_width // 8 < hi - lo:
                split, start = True, lo
            if not write and fabric.protocol == "AXI4-Lite":
                # An AXI4-Lite beat is full width: the read now covers whole aligned beats.
                lite = fabric.data_width // 8
                lo = (addr + lo) // lite * lite - addr
                hi = -(-(addr + hi) // lite) * lite - addr
            if fabric.data_width < narrowest:
                narrowest, carried = fabric.data_width, hi - lo
            if not write and fabric.protocol == "AHB-Lite":
                start = min(lo, 0)  # seed 202611240029
            # An APB3 bus has no PPROT: what crosses it is non-secure from there on (D28).
            nonsec = nonsec or prot_tied(spec, b.name) or fabric.protocol == "APB3"
            masked = masked or pslverr_tied(spec, b.name)
            crossed.append(b.name)
            continue
        word = slaves[target].data_width // 8  # a slave answers per data word
        width = slaves[target].addr_width
        visible = (addr - offset) & ((1 << width) - 1)
        if _axi4_downsizer_in_front(spec, target, fabric):
            lo = max(lo, start)  # axi_dw_downsizer starts at the address
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
            may_refuse=_may_refuse(spec, origin, tuple(crossed), target), span=carried,
        )  # fmt: skip
    raise AssertionError("bridge chain longer than the number of fabrics")


def _read_cover(spec: Spec, master: str, fabric: Any, addr: int) -> tuple[int, int]:
    """Bytes [lo, hi), relative to ADDR, a 32-bit read by MASTER covers on FABRIC.

    AXI4 masters issue narrow (size 2) transfers: the 4 addressed bytes. An AXI4-Lite beat is
    always full width: on an AXI4-Lite fabric (whose upsizers keep the full width) it covers
    the whole aligned beat, also for an AXI4 master, whose pc makes each read such a beat.
    Behind a pc onto an AXI4 fabric an AXI4-Lite beat becomes an AXI4 transfer of the Lite
    width, which covers from the address to the end of its aligned beat (AXI unaligned-transfer
    rule). route() narrows this where an AXI4 downsizer starts at the address.
    """
    m = next(x for x in spec.masters if x.name == master)
    if fabric.protocol == "AXI4-Lite" and m.protocol in ("AXI4", "AXI4-Lite"):
        lite_master = m.data_width // 8 if m.protocol == "AXI4-Lite" else 0
        beat = max(lite_master, fabric.data_width // 8)
        start = addr // beat * beat
        return start - addr, start + beat - addr
    if m.protocol != "AXI4-Lite":
        return 0, 4
    beat = m.data_width // 8
    return 0, addr // beat * beat + beat - addr


def error_words(spec: Spec, slave: str) -> set[int]:
    """Slave-visible addresses at which SLAVE answers with an error (D29 low bits).

    None for a slave whose PSLVERR is tied: such a slave never errors, by definition.
    """
    if pslverr_tied(spec, slave):
        return set()
    from hdl_bag.gen.decode import fabric_offset  # here: gen imports this module's package

    width = next(s.addr_width for s in spec.slaves if s.name == slave)
    fabric = next(f for f in spec.fabrics if any(p.port == slave for p in f.slave_ports))
    # Where the slave sees it: behind a pass-through N bridge, less the window base (seed 025).
    offset = fabric_offset(spec, fabric)
    return {(r.base + r.size - ERROR_WORD - offset) & ((1 << width) - 1)
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


def private_words(spec: Spec, m: Any, index: int, count: int, direct: bool = False,
                  unadapted: bool = False) -> list[int]:  # fmt: skip
    """COUNT words only master M uses (offset by INDEX) in the lowest region it can write and
    read back without errors; with DIRECT, of an AXI slave on M's own fabric (no bridge); with
    UNADAPTED, of a slave with no adapter in front of it."""
    axi_slaves = {s.name for s in spec.slaves if s.protocol in ("AXI4", "AXI4-Lite")}
    adapted = {a.port for a in spec.adapters}
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
            ok &= not wr.may_refuse and not rd.may_refuse
            if direct:
                ok &= not wr.bridges and wr.slave in axi_slaves
            if unadapted:
                ok &= wr.slave not in adapted
        if ok:
            return words
    return []


def midpoint_buffers(f: Any) -> bool:
    """Whether shared bus F's mid-point absorbs requests while its slave is stalled: they then
    leave in the order they arrived, not in the order the arbiter would have served them."""
    mid = f.midpoint
    return mid is not None and (mid.fifo_depth > 0 or mid.slice != "none")


def serves_by_priority(f: Any, direct: bool, same_clock: bool) -> bool:
    """Whether a fixed-priority round on F leaves in priority order: every request waits at the
    arbiter, as with no adapters (DIRECT) or no mid-point to absorb the early arrivals. Behind
    adapters the test starts each master late by how much faster its path is, so the requests
    arrive in the same cycle (#199); that needs the masters and the slave on F's clock
    (SAME_CLOCK), since through a clock crossing the arrival cycle depends on the phase."""
    return direct or (same_clock and not midpoint_buffers(f))


def on_fabric_clock(spec: Spec, f: Any, ports: list[Any], slave: str) -> bool:
    """Whether every master of PORTS and SLAVE run on fabric F's clock."""
    domains = {m.domain for m in spec.masters if m.name in {p.port for p in ports}}
    domains |= {s.domain for s in spec.slaves if s.name == slave}
    return domains <= {f.domain}


def midpoint_reads(f: Any, m: Any, buffered: int) -> int:
    """How many reads master M gets accepted on shared bus F with its slave stalled: what the
    mid-point holds (BUFFERED), as its guard allows. An AXI4-Lite guard caps a master at Max
    mst trans. An AXI4 guard admits Max mst trans per ID; with Max mst trans 1 its
    axi_demux_simple has 1-bit ID counters, full after one read, and a full counter blocks
    every ID (D80), so one read in all."""
    trans = f.max_mst_trans or buffered
    if f.protocol == "AXI4-Lite":
        return int(min(buffered, trans))
    if trans == 1:
        return 1
    return int(min(buffered, trans << (m.id_width or 0)))


def midpoint_path(spec: Spec, f: Any) -> tuple[Any, list[int], int] | None:
    """On AXI shared bus F: the master with the widest ID (so per-ID limits do not bind), words
    of an AXI slave right behind the mid-point, and how many reads the mid-point holds. Master
    and slave have no adapter: a register slice or FIFO there would hold reads of its own."""
    if f.protocol not in ("AXI4", "AXI4-Lite") or f.topology != "shared-bus" or f.midpoint is None:
        return None
    stages = {"none": 0, "cut": 1, "multicut-2": 2, "multicut-3": 3}[f.midpoint.slice]
    buffered = f.midpoint.fifo_depth + 2 * stages
    names = {p.port for p in f.master_ports}
    adapted = {a.port for a in spec.adapters}
    ms = [x for x in spec.masters if x.name in names and x.name not in adapted]
    if not ms:
        return None
    m = max(ms, key=lambda x: x.id_width or 0)
    words = private_words(spec, m, 0, buffered + 4, direct=True, unadapted=True)
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
    if not found or not axi2apb & set(found[1].bridges) or splits_word_writes(spec, found[1]):
        return None
    return found


def splits_word_writes(spec: Spec, exp: Outcome) -> bool:
    """Whether a word write that EXP routes becomes byte writes: a bag_axi2ahb fed by AXI4-Lite
    takes full-width beats, and one wider than the word is written with some strobes clear,
    which bag_axi2ahb_core turns into one AHB write per set strobe (seed 202611240029)."""
    fabrics = {f.name: f for f in spec.fabrics}
    return any(b.template == "bag_axi2ahb" and fabrics[b.upstream].protocol == "AXI4-Lite"
               and min(fabrics[b.upstream].data_width, fabrics[b.downstream].data_width) > 32
               for b in spec.bridges if b.name in exp.bridges)  # fmt: skip


def byte_write_lands(spec: Spec, s: Any, exp: Outcome) -> bool:
    """Whether a byte write that EXP routes onto APB slave S stores only that byte: an 8-bit
    slave's word is that byte; otherwise S needs PSTRB, and the path must keep the strobes
    (no PSTRB tie, no APB3 bus on the way: stores_single_bytes)."""
    return bool(s.data_width == 8 or (s.protocol == "APB4" and stores_single_bytes(spec, exp)))


def pstrb_path(spec: Spec, m: Any, s: Any) -> tuple[int, Outcome] | None:
    """Word 0x40 of S, if AXI master M reaches this 32-bit APB4 slave cleanly, on a path that
    keeps the strobes (no PSTRB tie, no APB3 bus on the way: stores_single_bytes)."""
    if m.protocol not in ("AXI4", "AXI4-Lite") or s.protocol != "APB4" or s.data_width != 32:
        return None
    found = plain_word(spec, m, s)
    return found if found and stores_single_bytes(spec, found[1]) else None


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


def _tie(spec: Spec, port: str, sig: str) -> int:
    """Value a Port_Options-tied PROT signal carries (D28: security bits non-secure)."""
    opt = next(o for o in spec.port_options if o.port == port)
    default = {"hprot": 0b0011, "pprot": 0}.get(sig, 0) if opt.prot == "tie-default" else 0
    return default | {"hnonsec": 1, "pprot": 0b010, "awprot": 0b010, "arprot": 0b010}.get(sig, 0)


def attributes(spec: Spec, m: Any, prot: int, nonsec: bool,
               bridges: tuple[str, ...]) -> tuple[int, int, int, int]:  # fmt: skip
    """(privileged, non-secure, instruction, AxCACHE[1:0]) of an access as it leaves the last
    bridge: each bridge maps them to its downstream protocol."""
    templates = {b.name: b.template for b in spec.bridges}
    downstream = {b.name: next(f.protocol for f in spec.fabrics if f.name == b.downstream)
                  for b in spec.bridges}  # fmt: skip

    def from_ahb(h: int, ns: int) -> tuple[int, int, int, int]:
        return h >> 1 & 1, ns, 1 - (h & 1), h >> 2 & 3

    def from_prot(p: int, cache: int) -> tuple[int, int, int, int]:
        return p & 1, p >> 1 & 1, p >> 2 & 1, cache

    tied = prot_tied(spec, m.name)
    if m.protocol == "AHB-Lite":
        attrs = from_ahb(_tie(spec, m.name, "hprot") if tied else prot, 1 if tied else int(nonsec))
    elif m.protocol == "APB3":  # no PPROT: a bridge takes 3'b010 (non-secure, D28)
        attrs = from_prot(0b010, 0b11)
    else:  # AXI masters leave AxCACHE at 0b0011 (cocotbext-axi; the tie default)
        sig = "awprot" if m.protocol in ("AXI4", "AXI4-Lite") else "pprot"
        attrs = from_prot(_tie(spec, m.name, sig) if tied else prot,
                          cache_after_tie(spec, m.name, 0b11))  # fmt: skip
    for b in bridges:
        priv, ns, instr, cache = attrs
        cache = {"bag_apb2axi": 0b11, "bag_apb2ahb": 0}.get(templates[b], cache)
        if downstream[b] == "AXI4-Lite":
            cache = 0b11  # AXI4-Lite has no AxCACHE: the next bridge assumes 0b0011
        attrs = (priv, ns, instr, cache_after_tie(spec, b, cache))
        if downstream[b] == "APB3":  # no PPROT: the next bridge takes 3'b010 (D28)
            attrs = from_prot(0b010, attrs[3])
        elif prot_tied(spec, b):  # the bridge's downstream end has its PROT tied
            if downstream[b] == "AHB-Lite":
                attrs = from_ahb(_tie(spec, b, "hprot"), 1)
            else:  # a PROT tie leaves the cache attributes alone
                tie = _tie(spec, b, "awprot" if downstream[b] in ("AXI4", "AXI4-Lite") else "pprot")
                attrs = from_prot(tie, attrs[3])
    return attrs


def split_refused(cache: int, size: int, addr: int, beats: int, width: int) -> bool:
    """Whether PULP axi_to_axi_lite answers SLVERR (D104): its burst splitter refuses a
    non-modifiable transaction (AxCACHE[1] low) of AxLEN 1 to 16. SIZE bytes per beat at ADDR
    reach it through axi_dw_downsizer, which makes more beats of a transfer wider than WIDTH."""
    if cache & 0b10:
        return False
    ratio = (size + width - 1) // width
    align = (addr & (size - 1) & ~(width - 1)) // width
    return 2 <= beats * ratio - align <= 17


def _pc_width(spec: Spec, port: str, fabric: Any, start: int, from_port: bool) -> int | None:
    """The data width (bits) at the AXI4 to AXI4-Lite pc adapter between PORT and FABRIC, in the
    direction of travel from a width of START, or None if there is none."""
    chain = sorted((a for a in spec.adapters if a.port == port and a.fabric == fabric.name),
                   key=lambda a: a.chain_index, reverse=not from_port)  # fmt: skip
    width = start
    for a in chain:
        if a.type == "dw":
            width = int(a.params["to"])
        elif a.type == "pc" and a.params["from"] == "AXI4":
            return width
    return None


def _splitters(spec: Spec, master: str, bridges: tuple[str, ...],
               slave: str | None) -> list[tuple[int, int, int]]:  # fmt: skip
    """Each axi_to_axi_lite on the way from MASTER to SLAVE (or, for a miss, as far as BRIDGES
    go): (the bridges crossed before it, the bytes per beat at most as it enters the adapters in
    front of it, its data width in bytes)."""
    by_name = {f.name: f for f in spec.fabrics}
    m = next(x for x in spec.masters if x.name == master)
    fabric = next(f for f in spec.fabrics if any(p.port == master for p in f.master_ports))
    out: list[tuple[int, int, int]] = []
    narrowest = m.data_width
    if (w := _pc_width(spec, master, fabric, m.data_width, True)) is not None:
        out.append((0, narrowest // 8, w // 8))
    for k, name in enumerate(bridges):
        narrowest = min(narrowest, fabric.data_width)
        b = next(x for x in spec.bridges if x.name == name)
        down = by_name[b.downstream]
        if (w := _pc_width(spec, name, fabric, fabric.data_width, False)) is not None:
            out.append((k, narrowest // 8, w // 8))
        elif fabric.protocol == "AXI4" and b.template in ("bag_axi2apb", "bag_axi2axil"):
            out.append((k, narrowest // 8, min(fabric.data_width, down.data_width) // 8))
        if (w := _pc_width(spec, name, down, down.data_width, True)) is not None:
            out.append((k + 1, narrowest // 8, w // 8))  # on the bridge's far end
        fabric = down
    narrowest = min(narrowest, fabric.data_width)
    if slave is None:
        return out
    if (w := _pc_width(spec, slave, fabric, fabric.data_width, False)) is not None:
        out.append((len(bridges), narrowest // 8, w // 8))
    return out


def _may_refuse(spec: Spec, master: str, bridges: tuple[str, ...], slave: str) -> bool:
    """Whether a converter on this path sees the master's plain traffic as non-modifiable (D104)."""
    m = next(x for x in spec.masters if x.name == master)
    prot = TRAFFIC_HPROT if m.protocol == "AHB-Lite" else 0
    return any(not attributes(spec, m, prot, False, bridges[:k])[3] & 0b10
               for k, _, _ in _splitters(spec, master, bridges, slave))  # fmt: skip


def cache_tie_refusals(spec: Spec) -> dict[tuple[str, tuple[str, ...]], list[str]]:
    """{(master, the ports with CACHE tied to 0 on its way): the slaves it reaches where a
    converter can refuse its accesses (D104)}, over every region's base, both ways."""
    out: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for m in spec.masters:
        for r in spec.memory_map:
            if r.base >= 1 << m.addr_width:
                continue
            for write in (True, False):
                e = route(spec, m.name, r.base, write, False, {})
                tied = tuple(
                    p for p in (m.name, *e.bridges) if _option(spec, p, "cache") == "tie-0"
                )
                if e.slave is not None and e.may_refuse and tied:
                    out.setdefault((m.name, tied), set()).add(e.slave)
    return {k: sorted(v) for k, v in sorted(out.items())}


def refused_access(spec: Spec, m: Any, exp: Outcome, prot: int, nonsec: bool, addr: int,
                   size: int, beats: int = 1) -> bool:  # fmt: skip
    """Whether this access of BEATS beats of SIZE bytes is answered SLVERR on its way (D104), a
    miss too: a converter in front of the decode refuses it first (seed 202611240024)."""
    return any(split_refused(attributes(spec, m, prot, nonsec, exp.bridges[:k])[3],
                             min(size, most), addr, beats, width)
               for k, most, width in _splitters(spec, m.name, exp.bridges, exp.slave))  # fmt: skip


def reads_per_word(spec: Spec, m: Any, exp: Outcome, covered: int = 0) -> int:
    """How many times a read from M can reach one slave word: a bus on the way narrower than the
    slave splits it into transfers, and a wider bus behind makes each a full beat again, so a
    word is read once per transfer of that beat at or below it. The transfers are what the
    narrowest bus carries of the access (EXP.span: a Lite master's whole beat, say; not what
    an AXI4-Lite bus widens it to after) or a burst beat of COVERED bytes, where more than
    access_bytes(M) (generator seed 327; review of #210)."""
    fabrics = {f.name: f for f in spec.fabrics}
    bridges = {b.name: b for b in spec.bridges}
    fabric = next(f for f in spec.fabrics if any(p.port == m.name for p in f.master_ports))
    widths = [fabric.data_width, *(fabrics[bridges[b].downstream].data_width for b in exp.bridges)]
    # The slave word crosses the narrowest bus as this many transfers. So does the access, but
    # it comes back whole only as far as a wider bus behind the narrowest one widens it.
    narrow = min(widths)
    after = max(widths[widths.index(narrow) :])
    return max(1, exp.word_bytes // (narrow // 8),
               min(max(access_bytes(m), exp.span, covered), after // 8) // (narrow // 8))  # fmt: skip


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
    each beat reaching S cleanly: full-width INCR, FIXED and WRAP; narrow INCR (where the path
    stores a partial write as one); and an INCR that ends exactly on a 4 KB boundary. WRAP and FIXED stay full
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
    if stores_single_bytes(spec, found[1]):
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


def region_edges(spec: Spec, m: Any) -> list[tuple[int, Outcome, Outcome]]:
    """(address, write outcome, read outcome) of the bytes at the edges of every Memory_Map
    region, for master M as a single-byte transfer, with no alias pin on: its first and last
    byte, and the bytes just before and just after it (a miss, or another region). Only AXI4
    and AHB-Lite masters, which can send a single byte at any address (AXI4-Lite and APB
    address whole words)."""
    if m.protocol not in ("AXI4", "AHB-Lite"):
        return []
    out = {}
    for r in spec.memory_map:
        for a in (r.base - 1, r.base, r.base + r.size - 1, r.base + r.size):
            if not 0 <= a < 1 << m.addr_width or a in out:
                continue
            w, rd = (
                route(spec, m.name, a, True, False, {}),
                route(spec, m.name, a, False, False, {}),
            )
            out[a] = (a, w, rd)
    return [out[a] for a in sorted(out)]


def stores_single_bytes(spec: Spec, exp: Outcome) -> bool:
    """Whether a single-byte write that EXP routes stores just that byte: the slave takes
    partial writes, no bridge on the way drives its APB fabric with PSTRB tied (a tie makes
    every write a whole word, as configured), and no APB3 bus wider than a byte is on the way
    (APB3 has no PSTRB)."""
    slave = next(s for s in spec.slaves if s.name == exp.slave)
    tied = any(_option(spec, b, "pstrb") in ("tie-0", "tie-default") for b in exp.bridges)
    fabrics = {f.name: f for f in spec.fabrics}
    downstream = {b.name: fabrics[b.downstream] for b in spec.bridges}
    apb3 = any(downstream[b].protocol == "APB3" and downstream[b].data_width > 8
               for b in exp.bridges)  # fmt: skip
    return takes_partial_writes(spec, slave) and not tied and not apb3


def writes_whole_words(spec: Spec, exp: Outcome) -> bool:
    """Whether a partial write that EXP routes stores the whole slave word, as configured: the
    slave has no strobes, or a bridge on the way has PSTRB tied."""
    return not stores_single_bytes(spec, exp)


def rewrites_words(spec: Spec, exp: Outcome) -> bool:
    """Whether EXP's write can store one slave word several times: bag_axi2ahb makes one AHB
    write per set strobe, and each of them stores the whole word on a slave with no strobes."""
    templates = {b.name: b.template for b in spec.bridges}
    through = any(templates[b] == "bag_axi2ahb" for b in exp.bridges)
    return through and writes_whole_words(spec, exp)


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
    no error, on a path where no converter can refuse them (D104)."""
    region = next((r for r in spec.memory_map if r.slave == slave.name), None)
    if region is None:
        return None
    word = region.base + 0x40
    if word >= 1 << master.addr_width:
        return None
    outs = [route(spec, master.name, word, w, False, {}) for w in (True, False)]
    ok = all(o.slave == slave.name and not (o.slave_error or o.masked or o.may_refuse)
             for o in outs)  # fmt: skip
    return (word, outs[0]) if ok else None


def split_error_word(spec: Spec, master: Any, slave: Any) -> tuple[int, Outcome] | None:
    """plain_word, if SLAVE is narrower than a word (so a word is split into beats) and can
    answer an error: a slave with PSLVERR tied never does."""
    if slave.data_width >= 32 or pslverr_tied(spec, slave.name):
        return None
    return plain_word(spec, master, slave)


def saw_access(spec: Spec, exp: Any, addr: int, write: bool) -> bool:
    """Whether a slave logging ADDR saw the access EXP expects. An access keeps its address,
    except a read that crossed AHB-Lite, which arrives at the start of the slave's word: a
    full-size AHB transfer must be aligned."""
    slave = next(s for s in spec.slaves if s.name == exp.slave)
    downstream = {b.name: b.downstream for b in spec.bridges} if exp.bridges else {}
    protocols = {f.name: f.protocol for f in spec.fabrics} if exp.bridges else {}
    crossed = {protocols[downstream[b]] for b in exp.bridges}
    if write or "AHB-Lite" not in {slave.protocol, *crossed}:
        return bool(addr == exp.address)
    return bool(addr // exp.word_bytes == exp.address // exp.word_bytes)


def access_bytes(master: Any) -> int:
    """The bytes one test access of MASTER carries: a 32-bit word, or all an 8-bit or 16-bit
    APB master can move in one transfer."""
    return min(4, int(master.data_width) // 8)


def check_write(where: str, exp: Outcome, data: bytes, written: list[tuple[int, int]],
                whole_word: bool = False, rewrites: bool = False) -> None:  # fmt: skip
    """The bytes the slave stored for a write of DATA at EXP.address: exactly those, each once.
    After an error a split access can have stored only part of it, but nothing else. A beat
    with no strobes stores nothing, so one a width converter adds is not visible here.

    With WHOLE_WORD the rest of each word written may be stored too (writes_whole_words), and
    with REWRITES a byte may be stored again, with the same value (rewrites_words).
    """
    want = {exp.address + k: b for k, b in enumerate(data)}
    got = dict(written)
    stores = set(written) if rewrites else written
    assert len(got) == len(stores), f"{where}: a byte written twice: {_bytes(written)}"
    if whole_word:
        words = {a // exp.word_bytes for a in want}
        outside = [(a, v) for a, v in got.items() if a // exp.word_bytes not in words]
        assert not outside, f"{where}: stored {_bytes(outside)}, outside the words written"
        got = {a: v for a, v in got.items() if a in want}
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


def check_read(where: str, exp: Outcome, length: int, touched: list[int],
               repeats: int = 1) -> None:  # fmt: skip
    """The beat addresses a read of LENGTH bytes at EXP.address made the slave take: all the
    words it asked for, no address twice (a read can have side effects), and nothing beyond
    what its beats can cover on the way (EXP.words). A wide slave behind a narrow fabric can
    take two addresses in one word, and one address up to REPEATS times (reads_per_word)."""
    word = exp.word_bytes
    seen = {a // word for a in touched}
    assert max(Counter(touched).values(), default=0) <= repeats, (
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
