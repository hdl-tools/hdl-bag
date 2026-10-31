# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""IR-driven route test through a generated top (flat ports, or the flat harness).

The config comes from BAG_IR. Every master drives every probe address (route_model),
read and write, with every alias pin off and on, secure and non-secure where it
matters; the reference model says which slave must see the access, at which address,
or that the master must get an error. Data written is read back.
"""

import os
import random
from pathlib import Path
from typing import Any

import cocotb
from bfm import AHB_M, AHB_S, APB, AhbMaster, AhbSlave, ApbMaster, ApbSlave, Beat, Port
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles
from route_model import probes, prot_tied, route

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
            clocks[s.domain], port, data_width=s.data_width, max_wait=1)  # fmt: skip
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


async def _access(master: Any, proto: str, dw: int, addr: int, write: bool, data: int,
                  nonsec: bool) -> tuple[bool, int]:  # fmt: skip
    """One 32-bit access; returns (error, read data on the access's lanes)."""
    if proto == "AHB-Lite":
        shift = 8 * (addr % (dw // 8)) if dw > 32 else 0
        beat = Beat(addr, write, data << shift, size=2, nonsec=int(nonsec))
        result = (await master.run([beat]))[0]
        return result.err, (result.data >> shift) & 0xFFFF_FFFF
    prot = 0b010 if nonsec else 0
    if write:
        return await master.write(addr, data, prot=prot), 0
    r = await master.read(addr, prot=prot)
    return r.err, r.data


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def every_master_reaches_what_the_model_says(dut: Any) -> None:
    masters, slaves, _ = await _bring_up(dut)
    pin_sets = [dict.fromkeys(SPEC.wrapper.extra_inputs, False)]
    if SPEC.wrapper.extra_inputs:
        pin_sets.append(dict.fromkeys(SPEC.wrapper.extra_inputs, True))
    secure = any(r.secure for r in SPEC.memory_map)
    checked = 0
    for pins in pin_sets:
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
                        exp = route(SPEC, m.name, addr, write, nonsec, pins)
                        before = {n: len(s.log) for n, s in slaves.items()}
                        err, got = await _access(masters[m.name], m.protocol, m.data_width,
                                                 addr, write, data, nonsec)  # fmt: skip
                        where = f"{m.name} {'W' if write else 'R'} {addr:#x} nonsec={nonsec} pins={pins}"
                        grew = {n for n, s in slaves.items() if len(s.log) != before[n]}
                        if exp.slave is None and exp.masked:
                            assert not err and not grew, (
                                f"{where}: expected a masked error (OKAY, no slave), got err={err} at {grew}"
                            )
                        elif exp.slave is None:
                            assert err and not grew, (
                                f"{where}: expected an error, got err={err} at {grew}"
                            )
                        else:
                            assert not err and grew == {exp.slave}, (
                                f"{where}: expected {exp.slave} @ {exp.address:#x}, got err={err} at {grew}"
                            )
                            seen = slaves[exp.slave].log[-1]
                            assert seen.addr == exp.address and seen.write == write, (
                                f"{where}: {exp.slave} saw {seen.addr:#x} write={seen.write}, "
                                f"expected {exp.address:#x}"
                            )
                            if write:
                                written = exp
                            elif written == exp:
                                assert got == data, f"{where}: read {got:#x}, wrote {data:#x}"
                        checked += 1
    assert checked > 0
    dut._log.info("checked %d accesses", checked)
