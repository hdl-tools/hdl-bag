# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Wrapper modules: ports, nets, reset conditioning and template instantiations.

Generic code only. How a template is instantiated lives in its ``instance.sv.j2``,
which gets an ``InstanceContext`` to resolve bus signals into expressions:

* ``i(bus, sig)``: value for a component input (the net, or a tie constant);
* ``o(bus, sig)``: target for a component output (the net, or an unused sink);
* ``vec(port, buses, sig, dir)``: a packed array ``[N-1:0][W-1:0]`` for one port of a
  multi-port component, wired element by element to the buses;
* ``clk(domain)`` / ``rst(domain)``: clock and active-low reset of a domain.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from hdl_bag.gen.decode import decode_tables
from hdl_bag.gen.netlist import Bus, Instance, Netlist
from hdl_bag.gen.render import camel, environment, lit
from hdl_bag.ir.model import Spec
from hdl_bag.protocols import PROTO_TOKEN, Protocol, port_signals, signal_width, towards_slave


def _width(w: int) -> str:
    return f"[{w - 1}:0] " if w > 1 else ""


def builtin_root() -> Path:
    return Path(str(files("hdl_bag.templates") / "builtin"))


def interface_name(spec: Spec, bus: Bus) -> str:
    return f"{spec.project.name}_{PROTO_TOKEN[bus.protocol]}_if"


@dataclass
class Scope:
    """Name resolution and declarations of one wrapper module."""

    spec: Spec
    style: str  # flat | sv-interface
    external: dict[str, Bus]  # buses whose signals are ports of this module
    decls: list[str] = field(default_factory=list)
    assigns: list[str] = field(default_factory=list)
    domains: set[str] = field(default_factory=set)
    inputs_used: set[str] = field(default_factory=set)
    _declared: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._prefix = {p.port: p.prefix for p in self.spec.wrapper.ports}

    # ---------------------------------------------------------------- names
    def port_prefix(self, bus: Bus) -> str:
        return self._prefix.get(bus.name) or f"{bus.name}_{PROTO_TOKEN[bus.protocol]}"

    def flat_name(self, bus: Bus, sig: str) -> str:
        pattern = self.spec.wrapper.signal_pattern
        prefix = self._prefix.get(bus.name)
        if prefix and "{port}_{proto}" in pattern:
            pattern = pattern.replace("{port}_{proto}", prefix)
        return pattern.format(port=prefix or bus.name, proto=PROTO_TOKEN[bus.protocol], signal=sig)

    def ref(self, bus: Bus, sig: str) -> str:
        """The wire carrying SIG of BUS in this module (assumes it exists and is not tied)."""
        if bus.name in self.external:
            if self.style == "flat":
                return self.flat_name(bus, sig)
            return f"{self.port_prefix(bus)}.{sig}"
        name = f"{bus.name}_{sig}"
        self.declare(name, self.width(bus, sig))
        return name

    @staticmethod
    def width(bus: Bus, sig: str) -> int:
        return signal_width(sig, bus.addr_width, bus.data_width, bus.id_width, bus.user_width)

    def declare(self, name: str, width: int, packed: int = 0) -> None:
        if name not in self._declared:
            self._declared.add(name)
            dims = (f"[{packed - 1}:0]" if packed else "") + (
                f"[{width - 1}:0]" if width > 1 else ""
            )
            self.decls.append(f"  logic {dims} {name};" if dims else f"  logic {name};")

    def sink(self, name: str, width: int) -> str:
        self.declare(f"unused_{name}", width)
        return f"unused_{name}"

    # ---------------------------------------------------------------- clocks
    def clock(self, domain: str) -> str:
        self.domains.add(domain)
        return next(c.name for c in self.spec.clocks if c.domain == domain)

    def reset(self, domain: str) -> str:
        self.domains.add(domain)
        return f"rst_{domain.lower()}_n"


@dataclass
class InstanceContext:
    """What an instance.sv.j2 template sees as helper functions."""

    scope: Scope
    inst: Instance

    def i(self, bus: Bus, sig: str, default: int = 0, width: int | None = None) -> str:
        w = width or Scope.width(bus, sig)
        if sig in bus.ties:
            return lit(w, bus.ties[sig])
        if sig not in bus.signals:
            return lit(w, default)
        return self.scope.ref(bus, sig)

    def o(self, bus: Bus, sig: str, width: int | None = None) -> str:
        w = width or Scope.width(bus, sig)
        if sig in bus.ties or sig not in bus.signals:
            return self.scope.sink(f"{self.inst.rtl_name}_{bus.name}_{sig}", w)
        return self.scope.ref(bus, sig)

    def vec(self, port: str, buses: Sequence[Bus], sig: str, direction: str,
            default: int = 0, width: int | None = None) -> str:  # fmt: skip
        fab = self.inst.fabric
        w = width or (
            signal_width(sig, fab.addr_width, fab.data_width) if fab else Scope.width(buses[0], sig)
        )
        name = f"{self.inst.rtl_name}_{port}"
        self.scope.declare(name, w, packed=len(buses))
        for k, bus in enumerate(buses):
            # A port may be narrower than the fabric (addresses, D19): zero-extend inputs,
            # truncate outputs and sink the bits it does not have.
            bw = Scope.width(bus, sig) if sig in bus.signals and sig not in bus.ties else w
            bw = min(bw, w)
            if direction == "in":
                value = self.i(bus, sig, default, bw)
                if bw < w:
                    value = f"{{{lit(w - bw, 0)}, {value}}}"
                self.scope.assigns.append(f"  assign {name}[{k}] = {value};")
            else:
                src = f"{name}[{k}]" if bw == w else f"{name}[{k}][{bw - 1}:0]"
                self.scope.assigns.append(f"  assign {self.o(bus, sig, bw)} = {src};")
                if bw < w:
                    sink = self.scope.sink(f"{name}_{k}_msb", w - bw)
                    self.scope.assigns.append(f"  assign {sink} = {name}[{k}][{w - 1}:{bw}];")
        return name

    def clk(self, domain: str) -> str:
        return self.scope.clock(domain)

    def rst(self, domain: str) -> str:
        return self.scope.reset(domain)

    def alias_en(self) -> str:
        """Alias enable vector of the instance's fabric, highest alias first."""
        assert self.inst.fabric is not None
        enables = decode_tables(self.inst.fabric).alias_enables
        bits = []
        for e in reversed(enables):
            if e in ("0", "1"):
                bits.append(f"1'b{e}")
            else:
                self.scope.inputs_used.add(e)
                bits.append(e)
        return bits[0] if len(bits) == 1 else "{" + ", ".join(bits) + "}"


def render_instance(scope: Scope, inst: Instance) -> str:
    ctx = InstanceContext(scope, inst)
    template = environment(builtin_root()).get_template(f"{inst.template}/instance.sv.j2")
    fab = inst.fabric
    return template.render(
        ctx=ctx,
        inst=inst,
        name=inst.rtl_name,
        pkg=scope.spec.wrapper.package,
        p=camel(inst.name),
        fabric=fab,
        bridge=inst.bridge,
        tables=decode_tables(fab) if fab else None,
        lit=lit,
    )


def _port_decls(scope: Scope, netlist: Netlist) -> list[str]:
    """(direction, width, name, comment) rows of the module's port list."""
    spec = scope.spec
    rows: list[tuple[str, int, str]] = []
    comments: dict[int, str] = {}
    for c in sorted(spec.clocks, key=lambda c: c.name):
        rows.append(("input", 1, c.name))
    for r in sorted(spec.resets, key=lambda r: r.name):
        rows.append(("input", 1, r.name))
    for x in spec.wrapper.extra_inputs:
        rows.append(("input", 1, x))
    for bus in netlist.ports:
        if bus.name not in scope.external:
            continue
        role = "slave" if bus.is_slave else "master"
        comments[len(rows)] = f"{bus.name}: {bus.protocol} {role}"
        if scope.style == "flat":
            for sig in bus.signals:
                if sig in bus.ties:
                    continue
                drives_in = towards_slave(sig, bus.is_slave) != bus.is_slave
                rows.append(("input" if drives_in else "output", scope.width(bus, sig),
                             scope.flat_name(bus, sig)))  # fmt: skip
        else:
            modport = "to_slave" if bus.is_slave else "to_master"
            rows.append((f"{interface_name(spec, bus)}.{modport}", 0, scope.port_prefix(bus)))
    out = []
    for n, (direction, w, name) in enumerate(rows):
        if n in comments:
            out.append(f"    // {comments[n]}")
        sep = "," if n < len(rows) - 1 else ""
        if w:
            out.append(f"    {direction:<6} logic {_width(w)}{name}{sep}")
        else:
            out.append(f"    {direction} {name}{sep}")
    return out


def _tie_external_outputs(scope: Scope, netlist: Netlist) -> None:
    """sv-interface style: drive tied wrapper outputs so no interface signal floats."""
    if scope.style == "flat":
        return
    for bus in netlist.ports:
        for sig, value in bus.ties.items():
            if towards_slave(sig, bus.is_slave) == bus.is_slave:  # the wrapper drives it
                w = scope.width(bus, sig)
                scope.assigns.append(f"  assign {scope.port_prefix(bus)}.{sig} = {lit(w, value)};")


def _resets(scope: Scope) -> list[str]:
    spec = scope.spec
    resets = {r.domain: r for r in spec.resets}
    out = []
    for c in sorted(spec.clocks, key=lambda c: c.domain):
        r = resets[c.domain]
        net = f"rst_{c.domain.lower()}_n"
        if c.domain not in scope.domains:
            for sig in (c.name, r.name):
                out.append(f"  logic unused_{sig};")
                out.append(f"  assign unused_{sig} = {sig};")
            continue
        low = r.polarity == "active-low"
        out.append(f"  logic {net};")
        if r.deassertion == "sync":
            out += [
                "  bag_rst_sync #(",
                f"      .ActiveLow  (1'b{int(low)}),",
                f"      .AsyncAssert(1'b{int(r.assertion == 'async')})",
                f"  ) u_rst_sync_{c.domain.lower()} (",
                f"      .clk_i ({c.name}),",
                f"      .rst_i ({r.name}),",
                f"      .rst_no({net})",
                "  );",
            ]
        else:
            out.append(f"  assign {net} = {'' if low else '~'}{r.name};")
    return out


def render_wrapper(netlist: Netlist, style: str, header: str) -> str:
    spec = netlist.spec
    external = {b.name: b for b in netlist.ports if b.external}
    scope = Scope(spec, style, external)
    bodies = [render_instance(scope, inst) for inst in netlist.instances]
    _tie_external_outputs(scope, netlist)
    resets = _resets(scope)
    for x in spec.wrapper.extra_inputs:
        if x not in scope.inputs_used:
            scope.decls.append(f"  logic unused_{x};")
            scope.assigns.append(f"  assign unused_{x} = {x};")
    top = spec.hierarchy.top
    lines = [header.rstrip("\n"), "", f"module {top} (", *_port_decls(scope, netlist), ");", ""]
    if resets:
        lines += ["  // Clocks and resets", *resets, ""]
    if scope.decls:
        lines += ["  // Nets", *scope.decls, ""]
    if scope.assigns:
        lines += [*scope.assigns, ""]
    for body in bodies:
        lines += [body.rstrip("\n"), ""]
    lines += [f"endmodule : {top}", ""]
    return "\n".join(lines)


# -------------------------------------------------------------------- sv-interface style

_MODPORTS = {  # modport -> (port role whose signal set it carries, mirrored)
    "master": (False, False),
    "slave": (True, False),
    "to_master": (False, True),  # the fabric side facing a master (used by the wrapper)
    "to_slave": (True, True),  # the fabric side facing a slave (used by the wrapper)
}


def interface_signals(protocol: Protocol) -> tuple[str, ...]:
    """Union of the master-port and slave-port signals, in protocol order."""
    out = list(port_signals(protocol, False))
    out += [s for s in port_signals(protocol, True) if s not in out]
    return tuple(out)


def render_interface(spec: Spec, protocol: Protocol, header: str) -> str:
    name = f"{spec.project.name}_{PROTO_TOKEN[protocol]}_if"
    axi = protocol == Protocol.AXI4
    params = ["parameter int unsigned AddrWidth = 32", "parameter int unsigned DataWidth = 32"]
    if axi:
        params += ["parameter int unsigned IdWidth = 1", "parameter int unsigned UserWidth = 1"]
    lines = [header.rstrip("\n"), "", f"interface {name} #("]
    lines += [",\n".join(f"    {p}" for p in params), ") ();", ""]
    for sig in interface_signals(protocol):
        w = signal_width(sig, 2, 16, 2, 2)  # fixed widths; parameterized ones below
        if sig.endswith("addr"):
            dim = "[AddrWidth-1:0] "
        elif sig.endswith("data"):
            dim = "[DataWidth-1:0] "
        elif sig in ("wstrb", "pstrb"):
            dim = "[DataWidth/8-1:0] "
        elif sig in ("awid", "bid", "arid", "rid"):
            dim = "[IdWidth-1:0] "
        elif sig.endswith("user"):
            dim = "[UserWidth-1:0] "
        else:
            dim = _width(w)
        lines.append(f"  logic {dim}{sig};")
    lines.append("")
    for modport, (is_slave, mirrored) in _MODPORTS.items():
        sigs = port_signals(protocol, is_slave)
        items = []
        for sig in sigs:
            out = towards_slave(sig, is_slave) != is_slave
            out = out != mirrored
            items.append(f"{'output' if out else 'input'} {sig}")
        lines.append(f"  modport {modport} (")
        lines += [f"      {it}{',' if n < len(items) - 1 else ''}" for n, it in enumerate(items)]
        lines.append("  );")
    lines += ["", f"endinterface : {name}", ""]
    return "\n".join(lines)


def render_harness(netlist: Netlist, header: str) -> str:
    """Flat-port module around the sv-interface wrapper: the lint and compile top.

    Verilator cannot compile a top module with interface ports, so the flat harness
    instantiates the interfaces and the wrapper. It also documents the flat mapping.
    """
    spec = netlist.spec
    external = {b.name: b for b in netlist.ports if b.external}
    flat = Scope(spec, "flat", external)
    top = spec.hierarchy.top
    name = f"{top}_flat"
    body: list[str] = []
    conns = [c.name for c in sorted(spec.clocks, key=lambda c: c.name)]
    conns += [r.name for r in sorted(spec.resets, key=lambda r: r.name)]
    conns += list(spec.wrapper.extra_inputs)
    for bus in netlist.ports:
        if bus.name not in external:
            continue
        ifc = flat.port_prefix(bus)
        conns.append(ifc)
        params = [f".AddrWidth({bus.addr_width})", f".DataWidth({bus.data_width})"]
        if bus.protocol == Protocol.AXI4:
            params += [f".IdWidth({max(bus.id_width, 1)})", f".UserWidth({max(bus.user_width, 1)})"]
        body.append(f"  {interface_name(spec, bus)} #({', '.join(params)}) {ifc} ();")
        for sig in interface_signals(bus.protocol):
            drives_in = towards_slave(sig, bus.is_slave) != bus.is_slave
            into_wrapper = sig in bus.signals and drives_in
            if sig in bus.signals and sig not in bus.ties:
                port = flat.flat_name(bus, sig)
                if into_wrapper:
                    body.append(f"  assign {ifc}.{sig} = {port};")
                else:
                    body.append(f"  assign {port} = {ifc}.{sig};")
            elif sig in bus.signals and not into_wrapper:
                # Tied wrapper output: the wrapper drives the constant; read it here.
                w = Scope.width(bus, sig)
                body.append(f"  logic {_width(w)}unused_{ifc}_{sig};")
                body.append(f"  assign unused_{ifc}_{sig} = {ifc}.{sig};")
            else:
                # Not part of this port's role, or a tied input the wrapper ignores: give
                # it a defined value, and read it so no interface signal is left dangling.
                w = Scope.width(bus, sig)
                body.append(f"  assign {ifc}.{sig} = {lit(w, 0)};")
                body.append(f"  logic {_width(w)}unused_{ifc}_{sig};")
                body.append(f"  assign unused_{ifc}_{sig} = {ifc}.{sig};")
    lines = [header.rstrip("\n"), "", f"module {name} (", *_port_decls(flat, netlist), ");", ""]
    lines += body
    lines += ["", f"  {top} u_{top} ("]
    lines += [f"      .{c}({c}){',' if n < len(conns) - 1 else ''}" for n, c in enumerate(conns)]
    lines += ["  );", "", f"endmodule : {name}", ""]
    return "\n".join(lines)
