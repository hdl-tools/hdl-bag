# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Opaque (user-supplied) fabrics: bag_external and any template with ``opaque: true``.

BAG instantiates a module inside its wrapper, in the place a generated fabric would take,
and the user supplies that module's RTL. Its name is Fabrics > External module or, if that is
blank, ``<project>_<fabric>``. The port contract:

* ``clk_i`` / ``rst_ni``: the fabric's clock and conditioned (active-low) reset.
* For each attached port, in Port-index order (master side, then slave side), the bus
  signals named by the Wrapper signal pattern with ``{port}`` = ``<fabric>_<port>``, e.g.
  ``noc_cpu0_axi_awaddr``. With an External module, ``{port}`` is by position instead, so
  fabrics of the same shape can share the module: ``s<k>`` for the k-th master attachment
  (the fabric is the slave end) and ``m<k>`` for the k-th slave attachment, e.g.
  ``s0_axi_awaddr``. Directions are the fabric's: on a master's attachment the
  fabric is the slave end, on a slave's attachment the master end. The widths are those
  of the bus at the fabric (after the port's adapters: protocol, width, CDC, ID, ...).

* Then the user ports declared on User_Ports (D41), named as declared and connected as
  declared: exposed at the top as <fabric>_<port>, an extra input, a constant, a clock,
  a conditioned reset, or left open.

BAG also writes a stub, ``rtl/ext/<module>.sv``, with the same name and ports (outputs
tied to zero, inputs unused) so the generated design lints and elaborates; replace that
file (or its file-list entry) with the fabric's RTL.
"""

from dataclasses import dataclass

from hdl_bag.gen.netlist import Bus, Instance
from hdl_bag.ir.model import Fabric, Spec, UserPort
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


def exposed_name(u: UserPort) -> str:
    """Top-level (and sub-wrapper) port of an exposed user port: <instance>_<port>."""
    return f"{u.instance}_{u.port}"


def opaque_module(spec: Spec, fabric: Fabric) -> str:
    return fabric.external_module or f"{spec.project.name}_{fabric.name}"


def opaque_ports(spec: Spec, inst: Instance) -> list[OpaquePort]:
    """The user module's bus ports for opaque fabric instance INST, in contract order."""
    fab = inst.fabric
    assert fab is not None
    out: list[OpaquePort] = []
    sides = (("s", fab.master_ports, inst.masters), ("m", fab.slave_ports, inst.slaves))
    for end, attached, buses in sides:
        ordered = sorted(attached, key=lambda p: p.index)
        for k, (p, bus) in enumerate(zip(ordered, buses, strict=True)):
            prefix = f"{end}{k}" if fab.external_module else f"{fab.name}_{p.port}"
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


def opaque_declarations(spec: Spec, inst: Instance) -> list[str]:
    """The user module's ports for INST, in order: fabrics sharing a module must agree on these."""
    fab = inst.fabric
    assert fab is not None
    rows = [_decl(p.direction, p.width, p.name) for p in opaque_ports(spec, inst)]
    rows += [_decl(u.direction, u.width, u.port) for u in spec.user_ports if u.instance == fab.name]
    return [_decl("input", 1, "clk_i"), _decl("input", 1, "rst_ni"), *rows]


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
    users = [u for u in spec.user_ports if u.instance == fab.name]
    if users:
        rows.append("    // user ports (User_Ports)")
        rows += [_decl(u.direction, u.width, u.port) for u in users]
    decls = [r for r in rows if not r.lstrip().startswith("//")]
    for r in rows:
        sep = "," if not r.lstrip().startswith("//") and r is not decls[-1] else ""
        lines.append(r + sep)
    inputs = ["clk_i", "rst_ni", *(p.name for p in ports if p.direction == "input")]
    inputs += [u.port for u in users if u.direction == "input"]
    body = [f"  assign {p.name} = '0;" for p in ports if p.direction == "output"]
    body += [f"  assign {u.port} = '0;" for u in users if u.direction == "output"]
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
