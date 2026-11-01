# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Opaque (user-supplied) fabrics: bag_external and any template with ``opaque: true``.

BAG instantiates a module named ``<project>_<fabric>`` inside its wrapper, in the place
a generated fabric would take, and the user supplies that module's RTL. The port contract:

* ``clk_i`` / ``rst_ni``: the fabric's clock and conditioned (active-low) reset.
* For each attached port, in Port-index order (master side, then slave side), the bus
  signals named by the Wrapper signal pattern with ``{port}`` = ``<fabric>_<port>``, e.g.
  ``noc_cpu0_axi_awaddr``. Directions are the fabric's: on a master's attachment the
  fabric is the slave end, on a slave's attachment the master end. The widths are those
  of the bus at the fabric (after the port's adapters: protocol, width, CDC, ID, ...).

BAG also writes a stub, ``rtl/ext/<module>.sv``, with the same name and ports (outputs
tied to zero, inputs unused) so the generated design lints and elaborates; replace that
file (or its file-list entry) with the fabric's RTL.
"""

from dataclasses import dataclass

from hdl_bag.gen.netlist import Bus, Instance
from hdl_bag.ir.model import Fabric, Spec
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import PROTO_TOKEN, signal_width, towards_slave


@dataclass(frozen=True)
class OpaquePort:
    bus: Bus
    signal: str
    name: str  # port name on the user module
    direction: str  # "input" / "output", as the fabric sees it
    width: int
    port: str  # the attached master, slave or bridge


def opaque_module(spec: Spec, fabric: Fabric) -> str:
    return f"{spec.project.name}_{fabric.name}"


def opaque_ports(spec: Spec, inst: Instance) -> list[OpaquePort]:
    """The user module's bus ports for opaque fabric instance INST, in contract order."""
    fab = inst.fabric
    assert fab is not None
    out: list[OpaquePort] = []
    sides = ((fab.master_ports, inst.masters), (fab.slave_ports, inst.slaves))
    for attached, buses in sides:
        for p, bus in zip(sorted(attached, key=lambda p: p.index), buses, strict=True):
            prefix = f"{fab.name}_{p.port}"
            for sig in bus.signals:
                # Signals the master side drives flow into the fabric on a master's
                # attachment (bus.is_slave False) and out of it on a slave's.
                into = towards_slave(sig, bus.is_slave) != bus.is_slave
                width = signal_width(sig, bus.addr_width, bus.data_width, bus.id_width,
                                     bus.user_width)  # fmt: skip
                name = flat_signal_name(spec.wrapper.signal_pattern, prefix,
                                        PROTO_TOKEN[bus.protocol], sig, None)  # fmt: skip
                out.append(OpaquePort(bus, sig, name, "input" if into else "output", width,
                                      p.port))  # fmt: skip
    return out


def _decl(direction: str, width: int, name: str) -> str:
    dims = f"[{width - 1}:0] " if width > 1 else ""
    return f"    {direction:<6} logic {dims}{name}"


def render_stub(spec: Spec, inst: Instance, header: str) -> str:
    """A placeholder for opaque fabric INST: the contract's ports, outputs tied to zero."""
    fab = inst.fabric
    assert fab is not None
    module = opaque_module(spec, fab)
    ports = opaque_ports(spec, inst)
    rows = [_decl("input", 1, "clk_i"), _decl("input", 1, "rst_ni")]
    last = None
    lines = []
    for p in ports:
        if p.port != last:
            role = "master" if not p.bus.is_slave else "slave"
            rows.append(f"    // {p.port}: {p.bus.protocol} {role} attachment")
            last = p.port
        rows.append(_decl(p.direction, p.width, p.name))
    decls = [r for r in rows if not r.lstrip().startswith("//")]
    for r in rows:
        sep = "," if not r.lstrip().startswith("//") and r is not decls[-1] else ""
        lines.append(r + sep)
    inputs = ["clk_i", "rst_ni", *(p.name for p in ports if p.direction == "input")]
    body = [f"  assign {p.name} = '0;" for p in ports if p.direction == "output"]
    body += ["", "  logic unused;", "  assign unused = ^{"]
    body += [f"      {n}{',' if k < len(inputs) - 1 else ''}" for k, n in enumerate(inputs)]
    body += ["  };"]
    text = [
        header.rstrip("\n"),
        f"// Stub for {fab.name}, a user-supplied {fab.protocol} fabric (bag_external): it",
        "// accepts and answers nothing. Replace this file with the fabric's RTL: a module",
        f"// {module} with exactly these ports (see hdl_bag.gen.external).",
        "",
        f"module {module} (",
        *lines,
        ");",
        "",
        *body,
        "",
        f"endmodule : {module}",
        "",
    ]
    return "\n".join(text)
