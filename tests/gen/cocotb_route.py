# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""IR-driven tests through a generated top (flat ports, or the flat harness).

The config comes from BAG_IR.

* Route: every master drives every probe address (route_model), read and write, with
  the alias pins all off, one at a time and all on, secure and non-secure where it
  matters, with random PROT attribute bits; the reference model says which slave must
  see the access, at which address and with which PROT, whether it answers with an
  error, or what the fabric answers. Data written is read back.
* Arbitration: on every AHB-Lite fabric and AXI shared bus with several masters, all
  masters request the same slave in the same cycle; fixed priority serves them in
  priority order, round-robin serves each once. (An AXI crossbar serves each once.)

AHB-Lite and APB ports use the in-house BFMs (bfm.py); AXI4 and AXI4-Lite ports use
cocotbext-axi through bfm_axi.py, which offers the same interface.
"""

import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cocotb
from bfm import AHB_M, AHB_S, APB, AhbMaster, AhbSlave, ApbMaster, ApbSlave, Beat, Port
from bfm_axi import ROUNDS, AxiPortMaster, AxiPortSlave, bus
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Combine, ReadOnly, RisingEdge
from route_model import error_words, probes, prot_tied, route

from hdl_bag.ir import Spec, load
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import OPTIONAL_SIGNAL_NAMES, PROTO_TOKEN, Protocol, port_signals

SPEC = load(Path(os.environ["BAG_IR"]))
COLUMNS = {"prot": "PROT", "lock": "LOCK / excl", "pstrb": "PSTRB", "pslverr": "PSLVERR"}
AXI = ("AXI4", "AXI4-Lite")


def _axi_bus(dut: Any, spec: Spec, name: str, proto: str, slave: bool, idw: int | None,
             uw: int) -> Any:  # fmt: skip
    """cocotbext-axi bus for an AXI wrapper port; Port_Options-tied signals are absent."""
    prefix = next((p.prefix for p in spec.wrapper.ports if p.port == name), None)
    protocol = Protocol(proto)
    opt = next((o for o in spec.port_options if o.port == name), None)
    tied: set[str] = set()
    for fld in ("prot", "qos", "region", "cache", "lock"):
        if opt is not None and getattr(opt, fld) in ("tie-0", "tie-default"):
            tied |= set(OPTIONAL_SIGNAL_NAMES[fld.upper() if fld != "lock" else "LOCK / excl"])
    present = port_signals(protocol, slave, has_id=bool(idw), has_user=uw > 0)
    signals = {s: flat_signal_name(spec.wrapper.signal_pattern, name, PROTO_TOKEN[protocol],
                                   s, prefix)
               for s in present if s not in tied}  # fmt: skip
    return bus(dut, signals, proto == "AXI4-Lite")


def _port(dut: Any, spec: Spec, name: str, proto: str, slave: bool, aw: int, dw: int) -> Port:
    """BFM port for a wrapper port, with Port_Options-tied signals marked absent."""
    prefix = next((p.prefix for p in spec.wrapper.ports if p.port == name), None)
    protocol = Protocol(proto)
    kind = AHB_S if proto == "AHB-Lite" and slave else AHB_M if proto == "AHB-Lite" else APB
    present = set(port_signals(protocol, slave))
    opt = next((o for o in spec.port_options if o.port == name), None)
    tied: dict[str, int] = {}
    for fld, column in COLUMNS.items():
        if opt is not None and getattr(opt, fld) in ("tie-0", "tie-default"):
            tied |= {s: (-1 if s == "pstrb" else 0) for s in OPTIONAL_SIGNAL_NAMES[column]}
    signals = {}
    for sig, w in kind.items():
        if sig not in present:
            tied.setdefault(sig, -1 if sig == "pstrb" else 0)  # not in this protocol (APB3)
        width = w or (aw if "addr" in sig else dw)
        width = dw // 8 if sig == "pstrb" else width
        signals[sig] = (flat_signal_name(spec.wrapper.signal_pattern, name,
                                         PROTO_TOKEN[protocol], sig, prefix), width)  # fmt: skip
    return Port(dut, 0, signals, {s: v for s, v in tied.items() if s in signals})


async def _bring_up(dut: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    clocks = {}
    for i, c in enumerate(sorted(SPEC.clocks, key=lambda c: c.name)):
        Clock(getattr(dut, c.name), (10, 14, 26, 34)[i % 4], unit="ns").start()
        clocks[c.domain] = getattr(dut, c.name)
    for r in SPEC.resets:
        getattr(dut, r.name).value = 0 if r.polarity == "active-low" else 1
    for x in SPEC.wrapper.extra_inputs:
        getattr(dut, x).value = 0
    resets = {r.domain: r for r in SPEC.resets}

    def reset_of(domain: str) -> tuple[Any, bool]:
        r = resets[domain]
        return getattr(dut, r.name), r.polarity != "active-low"

    slaves: dict[str, Any] = {}
    for s in SPEC.slaves:
        if s.protocol in AXI:
            axi = _axi_bus(dut, SPEC, s.name, s.protocol, True, s.id_width, s.user_width)
            rst, level = reset_of(s.domain)
            axi_slave = AxiPortSlave(clocks[s.domain], axi, s.protocol == "AXI4-Lite",
                                     s.data_width, error_words(SPEC, s.name), rst, level)  # fmt: skip
            axi_slave.start()
            slaves[s.name] = axi_slave
            continue
        port = _port(dut, SPEC, s.name, s.protocol, True, s.addr_width, s.data_width)
        model = (AhbSlave if s.protocol == "AHB-Lite" else ApbSlave)(
            clocks[s.domain], port, data_width=s.data_width, max_wait=1,
            err_addrs=error_words(SPEC, s.name))  # fmt: skip
        model.start()
        slaves[s.name] = model
    masters: dict[str, Any] = {}
    for m in SPEC.masters:
        if m.protocol in AXI:
            axi = _axi_bus(dut, SPEC, m.name, m.protocol, False, m.id_width, m.user_width)
            rst, level = reset_of(m.domain)
            masters[m.name] = AxiPortMaster(clocks[m.domain], axi, m.protocol == "AXI4-Lite",
                                            rst, level)  # fmt: skip
            # A port lacks AxPROT only because its PROT is tied, never by a generator slip.
            assert masters[m.name].has_prot == (not prot_tied(SPEC, m.name)), m.name
            continue
        port = _port(dut, SPEC, m.name, m.protocol, False, m.addr_width, m.data_width)
        cls = AhbMaster if m.protocol == "AHB-Lite" else ApbMaster
        masters[m.name] = cls(clocks[m.domain], port)
    for _ in range(3):  # hold reset for a few cycles of every clock
        for clk in clocks.values():
            await ClockCycles(clk, 2)
    for r in SPEC.resets:
        getattr(dut, r.name).value = 1 if r.polarity == "active-low" else 0
    for clk in clocks.values():
        await ClockCycles(clk, 4)
    return masters, slaves, clocks


def _tie(spec: Spec, port: str, sig: str) -> int:
    """Value a Port_Options-tied PROT signal carries (D28: security bits non-secure)."""
    opt = next(o for o in spec.port_options if o.port == port)
    default = {"hprot": 0b0011, "pprot": 0}.get(sig, 0) if opt.prot == "tie-default" else 0
    return default | {"hnonsec": 1, "pprot": 0b010, "awprot": 0b010, "arprot": 0b010}.get(sig, 0)


def _attributes(m: Any, prot: int, nonsec: bool,
                bridges: tuple[str, ...]) -> tuple[int, int, int, int]:  # fmt: skip
    """(privileged, non-secure, instruction, AxCACHE[1:0]) of an access as it leaves the last
    bridge: each bridge maps them to its downstream protocol."""
    templates = {b.name: b.template for b in SPEC.bridges}
    downstream = {b.name: next(f.protocol for f in SPEC.fabrics if f.name == b.downstream)
                  for b in SPEC.bridges}  # fmt: skip

    def from_ahb(h: int, ns: int) -> tuple[int, int, int, int]:
        return h >> 1 & 1, ns, 1 - (h & 1), h >> 2 & 3

    def from_prot(p: int, cache: int) -> tuple[int, int, int, int]:
        return p & 1, p >> 1 & 1, p >> 2 & 1, cache

    tied = prot_tied(SPEC, m.name)
    if m.protocol == "AHB-Lite":
        attrs = from_ahb(_tie(SPEC, m.name, "hprot") if tied else prot, 1 if tied else int(nonsec))
    elif m.protocol == "APB3":  # no PPROT: a bridge takes 3'b010 (non-secure, D28)
        attrs = from_prot(0b010, 0b11)
    else:  # AXI masters leave AxCACHE at 0b0011 (cocotbext-axi; the tie default)
        sig = "awprot" if m.protocol in AXI else "pprot"
        attrs = from_prot(_tie(SPEC, m.name, sig) if tied else prot, 0b11)
    for b in bridges:
        priv, ns, instr, cache = attrs
        cache = {"bag_apb2axi": 0b11, "bag_apb2ahb": 0}.get(templates[b], cache)
        if downstream[b] == "AXI4-Lite":
            cache = 0b11  # AXI4-Lite has no AxCACHE: the next bridge assumes 0b0011
        attrs = (priv, ns, instr, cache)
        if prot_tied(SPEC, b):  # the bridge's downstream end has its PROT tied
            if downstream[b] == "AHB-Lite":
                attrs = from_ahb(_tie(SPEC, b, "hprot"), 1)
            else:  # a PROT tie leaves the cache attributes alone
                tie = _tie(SPEC, b, "awprot" if downstream[b] in AXI else "pprot")
                attrs = from_prot(tie, attrs[3])
    return attrs


def _expected_prot(m: Any, slave: str, prot: int, nonsec: bool,
                   bridges: tuple[str, ...]) -> int | None:  # fmt: skip
    """PROT the slave should log (AXI: AxPROT, AHB: HPROT | HNONSEC << 4, APB: PPROT); None:
    not seen."""
    s = next(x for x in SPEC.slaves if x.name == slave)
    if prot_tied(SPEC, slave) or s.protocol == "APB3":
        return None  # the slave has no PROT signals
    priv, ns, instr, cache = _attributes(m, prot, nonsec, bridges)
    if s.protocol == "AHB-Lite":
        return cache << 2 | priv << 1 | (1 - instr) | ns << 4
    return priv | ns << 1 | instr << 2


def _expected_cache(m: Any, prot: int, nonsec: bool, bridges: tuple[str, ...]) -> int:
    """AxCACHE an AXI4 slave should log ({2'b00, modifiable, bufferable})."""
    return _attributes(m, prot, nonsec, bridges)[3]


async def _access(master: Any, proto: str, dw: int, addr: int, write: bool, data: int,
                  nonsec: bool, prot: int, user: int = 0) -> tuple[bool, int, int]:  # fmt: skip
    """One 32-bit access; returns (error, read data on the access's lanes, AXI response
    code: 0 OKAY, 2 SLVERR, 3 DECERR; AHB / APB report 2 for any error)."""
    if proto == "AHB-Lite":
        shift = 8 * (addr % (dw // 8)) if dw > 32 else 0
        beat = Beat(addr, write, data << shift, size=2, prot=prot, nonsec=int(nonsec))
        result = (await master.run([beat]))[0]
        return result.err, (result.data >> shift) & 0xFFFF_FFFF, 2 * int(result.err)
    if proto in AXI:
        if write:
            code = await master.write(addr, data, prot=prot, user=user)
            return code != 0, 0, code
        r = await master.read(addr, prot=prot, user=user)
        return r.err, r.data, r.resp
    if write:
        err = await master.write(addr, data, prot=prot)
        return err, 0, 2 * int(err)
    r = await master.read(addr, prot=prot)
    return r.err, r.data, 2 * int(r.err)


# A default-slave master's own errors (D32) come from its guard, which answers DECERR.
AXI_RESP = {"SLVERR": 2, "DECERR": 3, "default-slave": 3, "PSLVERR": 2, "HRESP_ERROR": 2}


def _to_lite(m: Any) -> bool:
    """An AXI4 master on an AXI4-Lite fabric (through PULP axi_to_axi_lite, whose burst
    splitter reports every write error as SLVERR; read responses pass unchanged)."""
    fabric = next(f for f in SPEC.fabrics if any(p.port == m.name for p in f.master_ports))
    return bool(m.protocol == "AXI4" and fabric.protocol == "AXI4-Lite")


def _expected_resp(m: Any, exp: Any, write: bool) -> int | None:
    """The exact AXI response an AXI master must get (None for other masters). An error from
    beyond a bridge into AHB or APB is SLVERR (those protocols have one error response);
    PULP axi_to_axi_lite (AXI4 onto AXI4-Lite) reports every write error as SLVERR."""
    if m.protocol not in AXI:
        return None
    if exp.slave is None:
        code = 0 if exp.masked else AXI_RESP[exp.miss]
    else:
        code = 2 if exp.slave_error else 0
    protocols = {f.name: f.protocol for f in SPEC.fabrics}
    bridges = {b.name: b for b in SPEC.bridges}
    if any(protocols[bridges[b].downstream] not in AXI for b in exp.bridges):
        return 2 if code else 0
    through_lite = _to_lite(m) or bool(exp.bridges)
    return 2 if code and write and through_lite else code


def _pin_sets() -> list[dict[str, bool]]:
    pins = list(SPEC.wrapper.extra_inputs)
    sets = [dict.fromkeys(pins, False)]
    sets += [{p: p == on for p in pins} for on in pins]  # one at a time
    if len(pins) > 1:
        sets.append(dict.fromkeys(pins, True))
    return sets


# Timeouts are a few times the longest run seen (the route test on demo_ss, the largest config,
# stays under 1 ms of simulated time), so a deadlocked design fails within minutes instead of
# hanging CI.
@cocotb.test(timeout_time=3000, timeout_unit="us")
async def every_master_reaches_what_the_model_says(dut: Any) -> None:
    # BAG_ROUTE_MASTERS "i/k" checks every k-th master from i (test_corpus.py's ROUTE_PARTS).
    i, k = (int(x) for x in (os.environ.get("BAG_ROUTE_MASTERS") or "0/1").split("/"))
    masters, slaves, _ = await _bring_up(dut)
    secure = any(r.secure for r in SPEC.memory_map)
    checked = errors = 0
    reached: set[str] = set()
    for pins in _pin_sets():
        for x, on in pins.items():
            getattr(dut, x).value = int(on)
        for m in SPEC.masters[i::k]:
            nonsec_options = [False, True] if secure and not prot_tied(SPEC, m.name) else [False]
            for nonsec in nonsec_options:
                for addr in probes(SPEC):
                    if addr >= 1 << m.addr_width:
                        continue
                    data = random.getrandbits(32)
                    written = None
                    for write in (True, False):
                        ns = nonsec
                        if m.protocol == "AHB-Lite":
                            prot = random.getrandbits(4)
                        else:
                            # Without secure regions the non-secure bit cannot change the
                            # route, so it is random too and every PROT bit is checked.
                            ns = nonsec if secure else bool(random.getrandbits(1))
                            prot = (random.getrandbits(3) & ~0b010) | int(ns) << 1
                        exp = route(SPEC, m.name, addr, write, ns, pins)
                        before = {n: len(s.log) for n, s in slaves.items()}
                        user = random.getrandbits(m.user_width) if m.user_width else 0
                        err, got, resp = await _access(masters[m.name], m.protocol,
                                                       m.data_width, addr, write, data, nonsec,
                                                       prot, user)  # fmt: skip
                        want_resp = _expected_resp(m, exp, write)
                        where = f"{m.name} {'W' if write else 'R'} {addr:#x} nonsec={nonsec} pins={pins}"
                        grew = {n for n, s in slaves.items() if len(s.log) != before[n]}
                        if exp.slave is None:
                            assert err == (not exp.masked) and not grew, (
                                f"{where}: expected {'a masked' if exp.masked else 'an'} error, "
                                f"got err={err} at {grew}"
                            )
                            assert want_resp is None or resp == want_resp, (
                                f"{where}: response {resp}, expected {want_resp} ({exp.miss})"
                            )
                        else:
                            assert err == exp.slave_error and grew == {exp.slave}, (
                                f"{where}: expected {exp.slave} @ {exp.address:#x} "
                                f"error={exp.slave_error}, got err={err} at {grew}"
                            )
                            # A width converter may add beats (zero-strobe halves), so the
                            # access must be among what the slave saw, not necessarily last.
                            new = slaves[exp.slave].log[before[exp.slave] :]
                            match = [e for e in new if e.addr == exp.address and e.write == write]
                            assert match, (
                                f"{where}: {exp.slave} saw {[hex(e.addr) for e in new]} "
                                f"write={write}, expected {exp.address:#x}"
                            )
                            seen = match[0]
                            assert want_resp is None or resp == want_resp, (
                                f"{where}: response {resp}, expected {want_resp}"
                            )
                            s_uw = next(x.user_width for x in SPEC.slaves if x.name == exp.slave)
                            if m.user_width and s_uw and not exp.bridges:
                                want_user = user & ((1 << s_uw) - 1)
                                assert seen.user == want_user, (
                                    f"{where}: {exp.slave} saw USER {seen.user:#x}, "
                                    f"expected {want_user:#x}"
                                )
                            want = _expected_prot(m, exp.slave, prot, exp.nonsec, exp.bridges)
                            assert want is None or seen.prot == want, (
                                f"{where}: {exp.slave} saw PROT {seen.prot:#b}, expected {want:#b}"
                            )
                            cache = getattr(seen, "cache", None)  # AXI4 slaves only
                            want_cache = _expected_cache(m, prot, exp.nonsec, exp.bridges)
                            assert cache is None or cache == want_cache, (
                                f"{where}: {exp.slave} saw AxCACHE {cache:#b}, "
                                f"expected {want_cache:#b}"
                            )
                            errors += exp.slave_error
                            if write and not exp.slave_error:
                                written = exp
                            elif not write and written == exp and not exp.slave_error:
                                assert got == data, f"{where}: read {got:#x}, wrote {data:#x}"
                        checked += 1
                        reached.add(m.name)
    assert checked > 0 and errors > 0, (checked, errors)
    assert reached == {m.name for m in SPEC.masters[i::k]}, reached
    dut._log.info("checked %d accesses (%d slave errors)", checked, errors)


def _reach(region: Any, ports: list[Any]) -> list[Any]:
    """The PORTS that can address REGION (16 bytes per port) and write it directly."""
    widths = {m.name: m.addr_width for m in SPEC.masters}
    return [p for p in ports if region.base + 16 * len(ports) < 1 << widths[p.port]
            and route(SPEC, p.port, region.base, True, False, {}).slave == region.slave
            and not route(SPEC, p.port, region.base, True, False, {}).bridges]  # fmt: skip


@cocotb.test(timeout_time=50, timeout_unit="us")
async def arbitration_follows_the_fabric_policy(dut: Any) -> None:
    masters, slaves, _ = await _bring_up(dut)
    for f in SPEC.fabrics:
        ports = [p for p in sorted(f.master_ports, key=lambda p: p.index)
                 if any(m.name == p.port for m in SPEC.masters)]  # fmt: skip
        if f.protocol != "AHB-Lite" or len(ports) < 2:
            continue
        # The region the most masters can address and write directly; those masters
        # write it at a distinct word each.
        regions = [r for r in SPEC.memory_map if r.fabric == f.name]
        candidates = ports
        region = max(regions, key=lambda r: len(_reach(r, candidates)))
        ports = _reach(region, ports)
        if len(ports) < 2:
            continue
        width = next(s.addr_width for s in SPEC.slaves if s.name == region.slave)
        addrs = {p.port: region.base + 16 * i for i, p in enumerate(ports)}
        visible = {(a & ((1 << width) - 1)): p for p, a in addrs.items()}
        n = len(slaves[region.slave].log)
        await Combine(*(cocotb.start_soon(_access(masters[p], "AHB-Lite", 32, a, True, i, False, 3))
                        for i, (p, a) in enumerate(addrs.items())))  # fmt: skip
        order = [visible[e.addr] for e in slaves[region.slave].log[n:] if e.write]
        if f.arbitration == "fixed-priority":
            want = [p.port for p in sorted(ports, key=lambda p: p.priority or 0)]
            assert order == want, f"{f.name}: served {order}, priority order {want}"
        else:
            assert sorted(order) == sorted(addrs), f"{f.name}: served {order}"
        dut._log.info("%s (%s): served %s", f.name, f.arbitration, order)


def _private_words(m: Any, index: int, count: int, direct: bool = False) -> list[int]:
    """COUNT words only master M uses (offset by INDEX) in the lowest region it can write and
    read back without errors; with DIRECT, of an AXI slave on M's own fabric (no bridge)."""
    for r in sorted(SPEC.memory_map, key=lambda r: r.base):
        words = [r.base + 0x100 * index + 4 * k for k in range(count)]
        if words[-1] >= min(1 << m.addr_width, r.base + r.size - 16):
            continue
        ok = True
        for a in words:
            wr = route(SPEC, m.name, a, True, False, {})
            rd = route(SPEC, m.name, a, False, False, {})
            # Both reach one slave word without error (a denied write that a default slave
            # takes would not read back).
            ok &= wr.slave is not None and not wr.slave_error and not wr.masked
            ok &= (wr.slave, wr.address, wr.slave_error) == (rd.slave, rd.address, rd.slave_error)
            if direct:
                ok &= not wr.bridges and wr.slave in _axi_names(SPEC.slaves)
        if ok:
            return words
    return []


@cocotb.test(timeout_time=50, timeout_unit="us")
async def concurrent_traffic_keeps_every_masters_data(dut: Any) -> None:
    """All masters at once, AXI masters with several transactions in flight (cocotbext-axi
    rotates IDs): every write lands and every read returns that master's own data."""
    masters, _, _ = await _bring_up(dut)

    async def traffic(m: Any, words: list[int]) -> int:
        data = {a: random.getrandbits(32) for a in words}
        if m.protocol in AXI:
            writes = [cocotb.start_soon(masters[m.name].write(a, d)) for a, d in data.items()]
            await Combine(*writes)
            assert not any(w.result() for w in writes), f"{m.name}: write error"
            reads = [cocotb.start_soon(masters[m.name].read(a)) for a in data]
            await Combine(*reads)
            got = [r.result() for r in reads]
        else:
            prot = 3 if m.protocol == "AHB-Lite" else 0  # secure (APB: PPROT[1] = non-secure)
            for a, d in data.items():
                err, _, _ = await _access(masters[m.name], m.protocol, m.data_width, a, True,
                                          d, False, prot)  # fmt: skip
                assert not err, f"{m.name}: write error at {a:#x}"
            got = []
            for a in data:
                err, value, _ = await _access(masters[m.name], m.protocol, m.data_width, a,
                                              False, 0, False, prot)  # fmt: skip
                got.append(type("R", (), {"err": err, "data": value})())
        for (a, d), r in zip(data.items(), got, strict=True):
            assert not r.err and r.data == d, f"{m.name} {a:#x}: read {r.data:#x}, wrote {d:#x}"
        return len(data)

    plans = {m.name: (m, _private_words(m, i, 8)) for i, m in enumerate(SPEC.masters)}
    tasks = [cocotb.start_soon(traffic(m, words)) for m, words in plans.values() if words]
    await Combine(*tasks)
    total = sum(t.result() for t in tasks)
    assert total > 0
    dut._log.info("concurrent traffic: %d words over %d masters", total, len(tasks))


# --------------------------------------------------------------------------- AXI fabrics


def _contention(f: Any) -> tuple[Any, list[Any]] | None:
    """A slave region on AXI fabric F that several masters write directly, and those
    masters (by Port index)."""
    ports = [p for p in sorted(f.master_ports, key=lambda p: p.index)
             if any(m.name == p.port for m in SPEC.masters)]  # fmt: skip
    regions = [r for r in SPEC.memory_map if r.fabric == f.name and not r.secure]
    if len(ports) < 2 or not regions:
        return None
    region = max(regions, key=lambda r: len(_reach(r, ports)))
    ports = _reach(region, ports)
    return (region, ports) if len(ports) >= 2 else None


async def _writes(masters: dict[str, Any], jobs: list[tuple[str, int]]) -> None:
    """Start every (master, address) write in the same cycle and wait for all."""
    tasks = [cocotb.start_soon(masters[p].write(a, i)) for i, (p, a) in enumerate(jobs)]
    await Combine(*tasks)
    assert not any(t.result() for t in tasks), "unexpected write error"


async def _served(masters: dict[str, Any], slave: Any, clk: Any, owner: dict[int, str],
                  jobs: list[tuple[str, int]]) -> list[str]:  # fmt: skip
    """Run JOBS against SLAVE held stalled for 20 cycles; the masters in the order served."""
    n = len(slave.log)
    slave.stall(True)
    task = cocotb.start_soon(_writes(masters, jobs))
    await ClockCycles(clk, 20)
    slave.stall(False)
    await task
    return [owner[e.addr] for e in slave.log[n:] if e.write and e.addr in owner]


async def _absorbed(masters: dict[str, Any], slave: Any, clk: Any, port: str, base: int) -> int:
    """AW handshakes PORT gets at its own wrapper port with SLAVE stalled: its path's capacity."""
    aw = masters[port].bus.write.aw
    slave.stall(True)
    task = cocotb.start_soon(_writes(masters, [(port, base + 16 * (3 + k)) for k in range(12)]))
    taken = 0
    for _ in range(40):
        await RisingEdge(clk)
        await ReadOnly()
        taken += int(aw.awvalid.value) & int(aw.awready.value)
    await RisingEdge(clk)
    slave.stall(False)
    await task
    return taken


@cocotb.test(timeout_time=200, timeout_unit="us")
async def axi_arbitration_rounds_starvation_and_lock(dut: Any) -> None:
    """AXI fabrics with several masters on one slave, with the slave stalled so every
    request is waiting when it opens:

    * rounds: one write per master, three times; fixed priority serves them in priority
      order every round, round robin serves each once per round;
    * starvation: the first-served master keeps four writes queued against one write of
      the last-served one; fixed priority serves the low master last, round robin does
      not;
    * lock: a low-priority write is presented while the slave is stalled, then a higher-
      priority one arrives; the grant must not change before the handshake.
    """
    masters, slaves, clocks = await _bring_up(dut)
    tested = 0
    for f in SPEC.fabrics:
        if f.protocol not in AXI or (found := _contention(f)) is None:
            continue
        region, ports = found
        slave = slaves[region.slave]
        clk = clocks[f.domain]
        width = next(s.addr_width for s in SPEC.slaves if s.name == region.slave)
        # 16 bytes between jobs: a downsizer's extra (zero-strobe) beat of a narrow write
        # never lands on another job's address.
        base = {p.port: region.base + 0x100 * i for i, p in enumerate(ports)}
        owner = {(a + 16 * k) & ((1 << width) - 1): p for p, a in base.items() for k in range(16)}
        fixed = f.arbitration == "fixed-priority"
        by_prio = [p.port for p in sorted(ports, key=lambda p: p.priority or 0)]

        # Without master-side adapters every request reaches the arbiter in the same cycle,
        # so round robin must rotate in Port-index order and starve nobody.
        direct = not any(a.fabric == f.name and a.port in base for a in SPEC.adapters)
        index = [p.port for p in ports]
        for rnd in range(3):
            order = await _served(
                masters, slave, clk, owner, [(p.port, base[p.port] + 16 * rnd) for p in ports]
            )
            if fixed:
                assert order == by_prio, f"{f.name} round {rnd}: {order}, priority {by_prio}"
            else:
                assert sorted(order) == sorted(base), f"{f.name} round {rnd}: {order}"
                if direct:
                    k = index.index(order[0])
                    assert order == index[k:] + index[:k], (
                        f"{f.name} round {rnd}: {order} is not a rotation of {index}"
                    )
        first = by_prio[0] if fixed else order[0]
        last = by_prio[-1] if fixed else order[-1]
        # Starvation needs every request waiting at the arbiter when the slave opens; a
        # shared-bus mid-point instead absorbs requests during the stall, as they arrive.
        mid = f.midpoint
        buffered = mid is not None and (mid.fifo_depth > 0 or mid.slice != "none")
        if not buffered:
            # Behind adapters the last-served request can arrive long after the first-served
            # master's writes: queue more than that master's path absorbs while the slave is
            # stalled (measured), so some still wait at the arbiter when it arrives.
            queued = 4 if direct else await _absorbed(masters, slave, clk, first, base[first]) + 4
            assert queued <= 12, f"{f.name}: {first} absorbs {queued - 4} writes; address plan"
            jobs = [(first, base[first] + 16 * (3 + k)) for k in range(queued)]
            jobs.append((last, base[last] + 16 * 3))
            order = await _served(masters, slave, clk, owner, jobs)
            pos = order.index(last)
            if fixed:
                assert pos == len(order) - 1, f"{f.name}: low-priority {last} at {pos} in {order}"
            elif direct:
                assert pos <= 1, f"{f.name}: round robin starved {last}: {order}"
            else:
                # Requests reach the arbiter through adapter pipelines at different times,
                # so strict alternation is not required; leaving the request last is starvation.
                assert pos < len(order) - 1, f"{f.name}: round robin starved {last}: {order}"
        if fixed:
            n = len(slave.log)
            slave.stall(True)
            low = cocotb.start_soon(masters[last].write(base[last] + 16 * 12, 1))
            await ClockCycles(clk, 10)
            high = cocotb.start_soon(masters[first].write(base[first] + 16 * 12, 2))
            await ClockCycles(clk, 10)
            slave.stall(False)
            await Combine(low, high)
            order = [owner[e.addr] for e in slave.log[n:] if e.write and e.addr in owner]
            assert order == [last, first], f"{f.name}: grant changed under a stall: {order}"
        tested += 1
        dut._log.info("%s (%s): arbitration checked over %s", f.name, f.arbitration,
                      [p.port for p in ports])  # fmt: skip
    if tested == 0:
        dut._log.info("no AXI fabric with contention")


@cocotb.test(timeout_time=50, timeout_unit="us")
async def axi_shared_bus_midpoint_buffers_reads(dut: Any) -> None:
    """With the slave's AR stalled, a master can issue exactly as many reads as the shared
    bus's mid-point holds: FIFO depth + 2 per register slice (each cut is a 2-entry spill
    register). AXI4-Lite guards also cap a master at Max mst trans outstanding."""
    masters, slaves, clocks = await _bring_up(dut)
    for f in SPEC.fabrics:
        if f.protocol not in AXI or f.topology != "shared-bus" or f.midpoint is None:
            continue
        stages = {"none": 0, "cut": 1, "multicut-2": 2, "multicut-3": 3}[f.midpoint.slice]
        buffered = f.midpoint.fifo_depth + 2 * stages
        # The master with the widest ID, so per-ID limits do not bind.
        names = [p.port for p in f.master_ports if any(m.name == p.port for m in SPEC.masters)]
        m = max((x for x in SPEC.masters if x.name in names), key=lambda x: x.id_width or 0)
        # The count is exact only for an AXI slave right behind the mid-point.
        words = _private_words(m, 0, buffered + 4, direct=True)
        if not words:
            continue
        exp = route(SPEC, m.name, words[0], False, False, {})
        assert exp.slave is not None
        expected = buffered
        if f.protocol == "AXI4-Lite":
            expected = min(buffered, f.max_mst_trans or buffered)
        else:
            expected = min(buffered, (f.max_mst_trans or buffered) << (m.id_width or 0))
        ar = masters[m.name].bus.read.ar
        clk = clocks[f.domain]
        slave = slaves[exp.slave]
        slave.stall(True)
        reads = [cocotb.start_soon(masters[m.name].read(a)) for a in words]
        accepted = 0
        for _ in range(200):
            await RisingEdge(clk)
            accepted += int(ar.arvalid.value) and int(ar.arready.value)
        slave.stall(False)
        await Combine(*reads)
        assert accepted == expected, (
            f"{f.name}: {accepted} reads accepted with the slave stalled, expected {expected} "
            f"(FIFO {f.midpoint.fifo_depth}, {f.midpoint.slice})"
        )
        dut._log.info("%s: mid-point held %d reads", f.name, accepted)


@cocotb.test(timeout_time=50, timeout_unit="us")
async def axi4_bursts_reach_slaves_and_error_paths(dut: Any) -> None:
    """Every AXI4 master: a 4-beat INCR burst writes and reads back through the fabric; a
    4-beat burst to an unmapped or denied address is answered with the master's error on
    every beat (the read returns all four beats, so RLAST came last)."""
    masters, _, _ = await _bring_up(dut)
    checked = 0
    for i, m in enumerate(SPEC.masters):
        if m.protocol != "AXI4":
            continue
        words = _private_words(m, i, 4)
        if words:
            data = bytes(random.getrandbits(8) for _ in range(16))
            assert await masters[m.name].burst_write(words[0], data) == 0
            r = await masters[m.name].burst_read(words[0], 16)
            assert not r.err and r.data == int.from_bytes(data, "little"), f"{m.name} burst"
            checked += 1
        miss = next((a for a in probes(SPEC) if a + 16 < 1 << m.addr_width and all(
            route(SPEC, m.name, a + 4 * k, w, False, {}).slave is None
            and not route(SPEC, m.name, a + 4 * k, w, False, {}).bridges
            for k in range(4) for w in (True, False))), None)  # fmt: skip
        if miss is not None:
            exp = route(SPEC, m.name, miss, True, False, {})
            want_w, want_r = _expected_resp(m, exp, True), _expected_resp(m, exp, False)
            code = await masters[m.name].burst_write(miss, bytes(16))
            assert code == want_w, f"{m.name} burst write to {miss:#x}: {code}, expected {want_w}"
            r = await masters[m.name].burst_read(miss, 16)
            assert r.resp == want_r, (
                f"{m.name} burst read to {miss:#x}: {r.resp}, expected {want_r}"
            )
            checked += 1
    dut._log.info("AXI4 bursts: %d checks", checked)


@cocotb.test(timeout_time=50, timeout_unit="us")
async def axi4_crossbars_admit_max_mst_trans_per_id(dut: Any) -> None:
    """Every AXI4 crossbar, master and AXI4 slave with no adapter between: with the slave
    taking requests but holding R, reads on one ID reach it until Max mst trans at least."""
    masters, slaves, clocks = await _bring_up(dut)
    adapted = {a.port for a in SPEC.adapters}
    checked = 0
    for f in SPEC.fabrics:
        if f.template != "bag_axi_xbar" or not f.max_mst_trans:
            continue
        ports = {p.port for p in f.master_ports} | {p.port for p in f.slave_ports}
        for m in SPEC.masters:
            if m.name not in ports or m.name in adapted or not m.id_width:
                continue
            words = _private_words(m, 0, f.max_mst_trans + 2, direct=True)
            exp = route(SPEC, m.name, words[0], False, False, {}) if words else None
            if exp is None or exp.slave not in ports or exp.slave in adapted:
                continue
            drv, slave = masters[m.name], slaves[exp.slave]
            n = len(slave.log)
            # The slave model holds only 5 reads by itself (2 AR, 2 R, 1 in hand): lift that, so
            # the count is the fabric's limit and not the model's.
            read_if = slave.slave.read_if
            limits = (
                read_if.ar_channel.queue_occupancy_limit,
                read_if.r_channel.queue_occupancy_limit,
            )
            read_if.ar_channel.queue_occupancy_limit = read_if.r_channel.queue_occupancy_limit = -1
            read_if.r_channel.pause = True
            reads = [cocotb.start_soon(drv.master.read(a, 4, arid=0, size=2, prot=drv._prot(0)))
                     for a in words]  # fmt: skip
            await ClockCycles(clocks[f.domain], 100)
            taken = sum(not e.write for e in slave.log[n:])
            read_if.r_channel.pause = False
            await Combine(*reads)
            read_if.ar_channel.queue_occupancy_limit, read_if.r_channel.queue_occupancy_limit = (
                limits
            )
            assert taken >= f.max_mst_trans, (
                f"{f.name}: {taken} reads on one ID reached {exp.slave}, "
                f"Max mst trans is {f.max_mst_trans}"
            )
            checked += 1
    dut._log.info("Crossbar per-ID limits: %d checks", checked)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def narrow_apb_behind_axi2apb_keeps_lanes_strobes_and_errors(dut: Any) -> None:
    """Every AXI master to every APB slave narrower than 32 bits behind bag_axi2apb: a word
    becomes one access per APB word, at the right addresses with the right lanes; a byte
    write touches only its lane; an error on any beat reaches the master."""
    masters, slaves, _ = await _bring_up(dut)
    axi2apb = {b.name for b in SPEC.bridges if b.template == "bag_axi2apb"}
    checked = 0
    for m in SPEC.masters:
        if m.protocol not in AXI:
            continue
        for s in SPEC.slaves:
            if s.protocol not in ("APB3", "APB4") or s.data_width >= 32:
                continue
            word = next(r for r in SPEC.memory_map if r.slave == s.name).base + 0x40
            if word >= 1 << m.addr_width:
                continue
            outs = [route(SPEC, m.name, word, w, False, {}) for w in (True, False)]
            if any(o.slave != s.name or o.slave_error or o.masked or not axi2apb & set(o.bridges)
                   for o in outs):  # fmt: skip
                continue
            drv, sl, addr, nbytes = (
                masters[m.name],
                slaves[s.name],
                outs[0].address,
                s.data_width // 8,
            )
            value, label = 0x4433_2211, f"{m.name} -> {s.name}"
            beats = list(range(addr, addr + 4, nbytes))
            lanes = [(value >> (8 * (a - addr))) & ((1 << s.data_width) - 1) for a in beats]
            n = len(sl.log)
            assert await drv.write(word, value) == 0, f"{label} write"
            seen = [e for e in sl.log[n:] if e.write]
            assert [(e.addr, e.data) for e in seen] == list(zip(beats, lanes, strict=True)), (
                f"{label}: {[(hex(e.addr), hex(e.data)) for e in seen]}"
            )
            assert [sl.mem[a] for a in beats] == lanes, f"{label}: memory"
            r = await drv.read(word)
            assert not r.err and r.data == value, f"{label}: read {r.data:#x}"
            # APB3 has no PSTRB: a partial write onto a wider APB word cannot be expressed.
            if nbytes == 1 or s.protocol == "APB4":
                n = len(sl.log)
                assert await drv.byte_write(word + 1, 0x5A) == 0, f"{label} byte write"
                seen = [e for e in sl.log[n:] if e.write]
                assert [e.addr for e in seen] == [addr + 1 - (addr + 1) % nbytes], f"{label} byte"
                r = await drv.read(word)
                assert not r.err and r.data == 0x4433_5A11, f"{label}: {r.data:#x}"
            for beat in beats:
                sl.err_addrs.add(beat)
                assert await drv.write(word, value) == 2, f"{label}: write error at {beat:#x}"
                r = await drv.read(word)
                assert r.resp == 2, f"{label}: read error at {beat:#x}: {r.resp}"
                sl.err_addrs.clear()
            assert await drv.write(word, value) == 0, f"{label}: after the errors"
            checked += 1
    dut._log.info("Narrow APB behind bag_axi2apb: %d checks", checked)


@cocotb.test(timeout_time=50, timeout_unit="us")
async def apb_byte_writes_carry_pstrb(dut: Any) -> None:
    """Every AXI master to every APB4 slave it reaches: pre-fill a word, write one byte;
    the APB slave sees that byte's PSTRB and the other bytes keep their value."""
    masters, slaves, _ = await _bring_up(dut)
    checked = 0
    for m in SPEC.masters:
        if m.protocol not in AXI:
            continue
        for s in SPEC.slaves:
            if s.protocol != "APB4" or s.data_width != 32:
                continue
            region = next(r for r in SPEC.memory_map if r.slave == s.name)
            word = region.base + 0x40
            if word >= 1 << m.addr_width:
                continue
            outs = [route(SPEC, m.name, word, w, False, {}) for w in (True, False)]
            if any(o.slave != s.name or o.slave_error or o.masked for o in outs):
                continue
            drv = masters[m.name]
            assert await drv.write(word, 0xFFFF_FFFF) == 0, f"{m.name} -> {s.name} pre-fill"
            n = len(slaves[s.name].log)
            assert not await drv.byte_write(word + 1, 0x5A), f"{m.name} -> {s.name} byte write"
            seen = [e for e in slaves[s.name].log[n:] if e.write and e.strb]
            assert [e.strb for e in seen] == [0b0010], (
                f"{m.name} -> {s.name}: APB writes with PSTRB {[bin(e.strb) for e in seen]}"
            )
            r = await drv.read(word)
            assert not r.err and r.data == 0xFFFF_5AFF, f"{m.name} -> {s.name}: {r.data:#x}"
            checked += 1
    dut._log.info("byte writes checked: %d master/slave pairs", checked)


@cocotb.test(timeout_time=3000, timeout_unit="us")
async def axi_ports_take_every_handshake_ordering(dut: Any) -> None:
    """D38. On every channel a model answers (an AXI slave's AW / W / AR, an AXI master's
    B / R), the generated design sees READY before VALID, READY after VALID and both in the
    same cycle, and data stays right. Lazy READY proves the design's VALID does not wait for
    its READY, w_first that WVALID does not wait for AWREADY: either would deadlock and time
    out. Where the design drives READY the order is its own choice: logged, not required.
    Every AXI port must be reached on the channels its regions allow (_ordering_targets)."""
    masters, slaves, _ = await _bring_up(dut)
    targets, required = _ordering_targets()
    if not required:
        return  # no AXI ports
    ports = {n: (slaves | masters)[n] for n in required}
    for port in ports.values():
        port.watch_orderings()

    async def traffic() -> None:
        for t in targets:
            m = next(x for x in SPEC.masters if x.name == t.master)
            for k in range(12):
                addr, data = t.base + 4 * k, random.getrandbits(32)
                prot = 0b0011 if m.protocol == "AHB-Lite" else 0
                for write in (w for w, ok in ((True, t.writes), (False, t.reads)) if ok):
                    err, got, _ = await _access(masters[t.master], m.protocol, m.data_width, addr,
                                                write, data, False, prot)  # fmt: skip
                    assert not err, f"{t.master} -> {t.slave} {addr:#x}"
                    if not write and t.writes:
                        assert got == data, f"{t.master} -> {t.slave} {addr:#x}: read {got:#x}"

    def gaps() -> dict[str, Any]:
        out = {n: ports[n].orderings.missing(channels) for n, channels in required.items()}
        return {n: g for n, g in out.items() if g}

    for mode in ("eager", "lazy", "w_first", "random"):
        for name, port in ports.items():
            # w_first needs a slave's AW and W channels.
            if mode != "w_first" or "aw" in port.handshaking.sinks:
                port.handshaking.set(mode if mode != "w_first" or name in slaves else "eager", 31)
        await traffic()
    for _ in range(ROUNDS):  # same-cycle handshakes come by chance: run until seen
        if not gaps():
            break
        await traffic()
    for name, port in ports.items():
        dut._log.info(f"handshake orderings of {name}: {port.orderings.counts}")
    assert not gaps(), gaps()


def _axi_names(rows: Any) -> set[str]:
    return {x.name for x in rows if x.protocol in AXI}


@dataclass(frozen=True)
class _Target:
    slave: str
    master: str
    base: int
    writes: bool
    reads: bool


def _ordering_targets() -> tuple[list[_Target], dict[str, tuple[str, ...]]]:
    """Traffic that reaches every AXI port, and the channels each must then have seen in every
    order. A slave whose regions are all read-only cannot see AW or W, nor one all write-only
    AR; a master that reaches nothing it can read back still has B or R. A port with no
    traffic possible at all is an error: the test must not silently check less."""
    slave_names = {s.name for s in SPEC.slaves}
    width = {m.name: m.addr_width for m in SPEC.masters}
    # Secure regions need a master that can say secure: not APB3, not PROT-tied (route knows).
    able = {m.name for m in SPEC.masters if m.protocol != "APB3"}

    def reach(master: str, r: Any, write: bool) -> bool:
        if r.base + 48 >= 1 << width[master] or (r.secure and master not in able):
            return False
        return all((e := route(SPEC, master, r.base + 4 * k, write, False, {})).slave == r.slave
                   and not e.slave_error for k in range(12))  # fmt: skip

    def target(r: Any, master: str) -> _Target | None:
        writes, reads = reach(master, r, True), reach(master, r, False)
        return _Target(r.slave, master, r.base, writes, reads) if writes or reads else None

    regions = sorted((r for r in SPEC.memory_map if r.slave in slave_names),
                     key=lambda r: (r.access != "RW", r.secure, r.base))  # fmt: skip
    found: list[_Target] = []
    for s in sorted(_axi_names(SPEC.slaves)):
        options = [t for r in regions if r.slave == s for m in SPEC.masters
                   if (t := target(r, m.name)) is not None]  # fmt: skip
        assert options, f"AXI slave {s}: no master reaches it, so no ordering can be checked"
        found.append(max(options, key=lambda t: (t.writes and t.reads, t.writes, t.reads)))
        # A read-only and a write-only way in together cover all three of its channels.
        if not (found[-1].writes and found[-1].reads):
            found += [t for t in options if t.writes != found[-1].writes][:1]
    for name in sorted(_axi_names(SPEC.masters)):
        options = [t for r in regions if (t := target(r, name)) is not None]
        assert options, f"AXI master {name}: it reaches no slave, so no ordering can be checked"
        if not any(t.master == name and t.writes and t.reads for t in found):
            found.append(max(options, key=lambda t: (t.writes and t.reads, t.writes, t.reads)))
            if not (found[-1].writes and found[-1].reads):
                found += [t for t in options if t.writes != found[-1].writes][:1]
    required: dict[str, tuple[str, ...]] = {}
    for s in _axi_names(SPEC.slaves):
        mine = [t for t in found if t.slave == s]
        required[s] = (*(("aw", "w") if any(t.writes for t in mine) else ()),
                       *(("ar",) if any(t.reads for t in mine) else ()))  # fmt: skip
    for m in _axi_names(SPEC.masters):
        mine = [t for t in found if t.master == m]
        required[m] = (*(("b",) if any(t.writes for t in mine) else ()),
                       *(("r",) if any(t.reads for t in mine) else ()))  # fmt: skip
    return found, required
