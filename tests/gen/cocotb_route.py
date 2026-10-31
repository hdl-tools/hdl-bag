# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""IR-driven tests through a generated top (flat ports, or the flat harness).

The config comes from BAG_IR.

* Route: every master drives every probe address (route_model), read and write, with
  the alias pins all off, one at a time and all on, secure and non-secure where it
  matters, with random PROT attribute bits; the reference model says which slave must
  see the access, at which address and with which PROT, whether it answers with an
  error, or what the fabric answers. Data written is read back.
* Arbitration: on every AHB-Lite fabric with several masters, all masters request the
  same slave in the same cycle; fixed priority serves them in priority order,
  round-robin serves each once.
"""

import os
import random
from pathlib import Path
from typing import Any

import cocotb
from bfm import AHB_M, AHB_S, APB, AhbMaster, AhbSlave, ApbMaster, ApbSlave, Beat, Port
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Combine
from route_model import error_words, probes, prot_tied, route

from hdl_bag.ir import Spec, load
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import OPTIONAL_SIGNAL_NAMES, PROTO_TOKEN, Protocol, port_signals

SPEC = load(Path(os.environ["BAG_IR"]))
COLUMNS = {"prot": "PROT", "lock": "LOCK / excl", "pstrb": "PSTRB", "pslverr": "PSLVERR"}


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
    slaves: dict[str, Any] = {}
    for s in SPEC.slaves:
        port = _port(dut, SPEC, s.name, s.protocol, True, s.addr_width, s.data_width)
        model = (AhbSlave if s.protocol == "AHB-Lite" else ApbSlave)(
            clocks[s.domain], port, data_width=s.data_width, max_wait=1,
            err_addrs=error_words(SPEC, s.name))  # fmt: skip
        model.start()
        slaves[s.name] = model
    masters: dict[str, Any] = {}
    for m in SPEC.masters:
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
    return default | {"hnonsec": 1, "pprot": 0b010}.get(sig, 0)


def _expected_prot(m: Any, slave: str, prot: int, nonsec: bool,
                   bridges: tuple[str, ...]) -> int | None:  # fmt: skip
    """PROT the slave should log (AHB: HPROT | HNONSEC << 4, APB: PPROT); None: not seen."""
    s = next(x for x in SPEC.slaves if x.name == slave)
    if prot_tied(SPEC, slave) or s.protocol == "APB3":
        return None  # the slave has no PROT signals
    if m.protocol == "AHB-Lite":
        hprot = _tie(SPEC, m.name, "hprot") if prot_tied(SPEC, m.name) else prot
        ns = 1 if prot_tied(SPEC, m.name) else int(nonsec)
        if not bridges:
            return hprot | ns << 4
        if prot_tied(SPEC, bridges[-1]):
            return _tie(SPEC, bridges[-1], "pprot")
        return (1 - (hprot & 1)) << 2 | ns << 1 | (hprot >> 1 & 1)  # bag_ahb2apb PPROT
    return _tie(SPEC, m.name, "pprot") if prot_tied(SPEC, m.name) else prot


async def _access(master: Any, proto: str, dw: int, addr: int, write: bool, data: int,
                  nonsec: bool, prot: int) -> tuple[bool, int]:  # fmt: skip
    """One 32-bit access; returns (error, read data on the access's lanes)."""
    if proto == "AHB-Lite":
        shift = 8 * (addr % (dw // 8)) if dw > 32 else 0
        beat = Beat(addr, write, data << shift, size=2, prot=prot, nonsec=int(nonsec))
        result = (await master.run([beat]))[0]
        return result.err, (result.data >> shift) & 0xFFFF_FFFF
    if write:
        return await master.write(addr, data, prot=prot), 0
    r = await master.read(addr, prot=prot)
    return r.err, r.data


def _pin_sets() -> list[dict[str, bool]]:
    pins = list(SPEC.wrapper.extra_inputs)
    sets = [dict.fromkeys(pins, False)]
    sets += [{p: p == on for p in pins} for on in pins]  # one at a time
    if len(pins) > 1:
        sets.append(dict.fromkeys(pins, True))
    return sets


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def every_master_reaches_what_the_model_says(dut: Any) -> None:
    masters, slaves, _ = await _bring_up(dut)
    secure = any(r.secure for r in SPEC.memory_map)
    checked = errors = 0
    for pins in _pin_sets():
        for x, on in pins.items():
            getattr(dut, x).value = int(on)
        for m in SPEC.masters:
            nonsec_options = [False, True] if secure and not prot_tied(SPEC, m.name) else [False]
            for nonsec in nonsec_options:
                for addr in probes(SPEC):
                    if addr >= 1 << m.addr_width:
                        continue
                    data = random.getrandbits(32)
                    written = None
                    for write in (True, False):
                        if m.protocol == "AHB-Lite":
                            prot = random.getrandbits(4)
                        else:
                            prot = (random.getrandbits(3) & ~0b010) | int(nonsec) << 1
                        exp = route(SPEC, m.name, addr, write, nonsec, pins)
                        before = {n: len(s.log) for n, s in slaves.items()}
                        err, got = await _access(masters[m.name], m.protocol, m.data_width,
                                                 addr, write, data, nonsec, prot)  # fmt: skip
                        where = f"{m.name} {'W' if write else 'R'} {addr:#x} nonsec={nonsec} pins={pins}"
                        grew = {n for n, s in slaves.items() if len(s.log) != before[n]}
                        if exp.slave is None:
                            assert err == (not exp.masked) and not grew, (
                                f"{where}: expected {'a masked' if exp.masked else 'an'} error, "
                                f"got err={err} at {grew}"
                            )
                        else:
                            assert err == exp.slave_error and grew == {exp.slave}, (
                                f"{where}: expected {exp.slave} @ {exp.address:#x} "
                                f"error={exp.slave_error}, got err={err} at {grew}"
                            )
                            seen = slaves[exp.slave].log[-1]
                            assert seen.addr == exp.address and seen.write == write, (
                                f"{where}: {exp.slave} saw {seen.addr:#x} write={seen.write}, "
                                f"expected {exp.address:#x}"
                            )
                            want = _expected_prot(m, exp.slave, prot, exp.nonsec, exp.bridges)
                            assert want is None or seen.prot == want, (
                                f"{where}: {exp.slave} saw PROT {seen.prot:#b}, expected {want:#b}"
                            )
                            errors += exp.slave_error
                            if write and not exp.slave_error:
                                written = exp
                            elif not write and written == exp and not exp.slave_error:
                                assert got == data, f"{where}: read {got:#x}, wrote {data:#x}"
                        checked += 1
    assert checked > 0 and errors > 0, (checked, errors)
    dut._log.info("checked %d accesses (%d slave errors)", checked, errors)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def arbitration_follows_the_fabric_policy(dut: Any) -> None:
    masters, slaves, _ = await _bring_up(dut)
    for f in SPEC.fabrics:
        ports = [p for p in sorted(f.master_ports, key=lambda p: p.index)
                 if any(m.name == p.port for m in SPEC.masters)]  # fmt: skip
        if f.protocol != "AHB-Lite" or len(ports) < 2:
            continue
        # A slave every master can write directly, at a distinct word per master.
        region = next(r for r in SPEC.memory_map if r.fabric == f.name and all(
            route(SPEC, p.port, r.base, True, False, {}).slave == r.slave
            and not route(SPEC, p.port, r.base, True, False, {}).bridges for p in ports))  # fmt: skip
        width = next(s.addr_width for s in SPEC.slaves if s.name == region.slave)
        addrs = {p.port: region.base + 16 * i for i, p in enumerate(ports)}
        visible = {(a & ((1 << width) - 1)): p for p, a in addrs.items()}
        n = len(slaves[region.slave].log)
        await Combine(*(cocotb.start_soon(_access(masters[p], "AHB-Lite", 32, a, True, i, False, 3))
                        for i, (p, a) in enumerate(addrs.items())))  # fmt: skip
        order = [visible[e.addr] for e in slaves[region.slave].log[n:]]
        if f.arbitration == "fixed-priority":
            want = [p.port for p in sorted(ports, key=lambda p: p.priority or 0)]
            assert order == want, f"{f.name}: served {order}, priority order {want}"
        else:
            assert sorted(order) == sorted(addrs), f"{f.name}: served {order}"
        dut._log.info("%s (%s): served %s", f.name, f.arbitration, order)
