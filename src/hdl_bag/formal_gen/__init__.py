# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal generation: SymbiYosys jobs and the properties they prove (Phase 5).

Output layout (relative to the output directory)::

    formal/<project>_reset.sv     quiet-in-reset property module, bound into the lint top
    formal/<project>_reset.sby    its SymbiYosys job: a prove task and a cover task
    formal/<project>_protocol.sv  port-protocol checkers, bound into the lint top (D60)
    formal/<project>_protocol.sby its job: a bmc task and a cover task
    formal/vendor/zipcpu/         the ZipCPU property files it uses, with their LICENSE

Properties attach with ``bind`` to the lint top (the flat top, or ``<top>_flat`` in the
sv-interface style), so they see its ports by name and the design needs no formal
wrapper. Its inputs are free; the property module assumes what the environment must do.
Clocks are free too (multiclock), so any clock ratio and phase is covered. Registers that
must see every solver step, whatever the clocks do, are instances of ``bag_step_ff0/1``
(``formal/bag_step.v``, registered on ``$global_clock`` and read by Yosys's own frontend):
yosys-slang has no global clock, and a register on a net marked ``(* gclk *)`` is clocked by
an ordinary free net there.

The first property family is quiet-in-reset, the formal form of the generated test
``reset_during_traffic_leaves_nothing_behind``: while a domain's reset is held, the design
asks nothing of the masters and slaves in that domain (no request VALID, PSEL or HTRANS
towards a slave, no response VALID towards an AXI master, HREADY high towards an AHB-Lite
master). A domain whose reset asserts synchronously gets the synchronizer's stages first.
It is proved by k-induction, so it holds in every reachable state, not only to a depth.

The environment, from the AMBA specifications and the reset sequence (D51):
- every reset is asserted at the start; a reset asserts only together with the rest of
  its reset group, and the group's resets release in any order; a reset is released only
  after it has taken effect;
- masters and slaves drive no VALID (AXI) or PSEL (APB), and HTRANS IDLE and HREADYOUT
  high (AHB-Lite), while their domain is in reset, until the clock edge after it (a master
  first drives VALID at an edge after reset, AXI A3.1.2), and until every domain of the
  reset group has left reset (D51 step 4: a sync-deassertion reset leaves the domain only
  after the synchronizer's stages);
- alias-enable pins change only in reset (Phase 4: they are quasi-static);
- in the protocol job, what the environment drives into an AXI4 port follows the AXI
  handshake rule (A3.2.1), until PR 2b brings BAG's own AXI4 properties.

Covers keep each assertion honest, as ``checked > 0`` does for a test: for each asserted
signal, the reset arrives while the signal is active (after the design has been through a
reset that took effect), and the domain then reaches the window the assertion checks. A
signal stuck at its reset value, or a property only ever checked in the first reset, fails
its cover. A cover is generated only where the route model says the signal can be active:
a slave no master may write never sees a write request.

The port-protocol family (D60) checks each AXI4-Lite and APB port of the top with
ZipCPU's property files: the slave checker where the design is the slave (a master port),
the master checker where it is the master (a slave port). They are read by Yosys's own
frontend (their dialect is not slang's); each instance gets a copy of the checker module
with its parameters set (``copy`` and ``chparam``), which the slang-read property module
instantiates. Each checker sees its domain's reset as the design does: asserted from the
start until the first reset has taken effect, then whenever the reset has passed the
synchronizer. ZipCPU's bounded-wait checks are off, because a port's latency depends on how
long the slaves beyond it stall, which nothing bounds. AXI4 and AHB-Lite ports get BAG's
own properties (PR 2b).

A design with a user-supplied fabric gets no formal, because BAG does not know how that
fabric behaves (D8).
"""

from dataclasses import dataclass

from hdl_bag.flows.formal import sby_job
from hdl_bag.gen.collateral import RST_SYNC_STAGES, reset_groups
from hdl_bag.gen.netlist import Bus, Netlist
from hdl_bag.gen.render import header, lit
from hdl_bag.ir.model import Spec
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import AXI_FAMILY, PROTO_TOKEN, TIE_WRITE_STROBE, Protocol, signal_width

# k-induction depth for the assertions, in solver steps. The quiet property depends on the
# reset state and the synchronizer's stage count, so a few steps make it inductive (demo_ss
# proves at 6).
PROVE_DEPTH = 6
# Cover search depth in solver steps. With free clocks each clock edge takes steps of its
# own: a signal behind a bridge and a clock crossing needs edges of every clock on the way,
# then a reset. The formal corpus tunes this per config class (Phase 5 PR 4).
COVER_DEPTH = 40

# Bounded depth for the protocol checkers, in solver steps: their transaction counters have
# no invariant tying them to the fabric's state, so they are checked to a depth, not proved.
# The formal corpus tunes it per config class (Phase 5 PR 4).
PROTOCOL_DEPTH = 16

ENGINE = "smtbmc yices"

# The solver-step registers (formal/bag_step.v), copied next to the jobs.
STEP = "bag_step.v"

# Vendored ZipCPU checkers (D60), by protocol family and by which side the design is on.
ZIPCPU = "formal/vendor/zipcpu"
_CHECKER = {("axil", False): "faxil_slave", ("axil", True): "faxil_master",
            ("apb", False): "fapb_slave", ("apb", True): "fapb_master"}  # fmt: skip


@dataclass(frozen=True)
class Watch:
    """One top-level signal the property watches in reset."""

    name: str  # top-level port name
    width: int
    quiet: int  # its value while the domain is in reset
    assert_: bool  # True: the design drives it (assert); False: the environment does (assume)
    cover: bool = False  # it can be active, so its assertion gets a cover


def _active(spec: Spec) -> dict[str, set[str]]:
    """Per slave, the accesses some master can bring to it ("read", "write"): the route
    model over its probe addresses, both security states and every alias pin setting."""
    from hdl_bag.tests_gen.route_model import pin_sets, probes, route

    out: dict[str, set[str]] = {s.name: set() for s in spec.slaves}
    addrs = probes(spec)
    for m in spec.masters:
        for addr in (a for a in addrs if a < 1 << m.addr_width):
            for write in (False, True):
                for nonsec in (False, True):
                    for pins in pin_sets(spec):
                        slave = route(spec, m.name, addr, write, nonsec, pins).slave
                        if slave is not None:
                            out[slave].add("write" if write else "read")
    return out


def _watches(spec: Spec, bus: Bus, active: dict[str, set[str]]) -> list[Watch]:
    """What BUS's port carries in reset: the design's outputs to assert, the environment's
    inputs to assume. Signals the port lacks are skipped. A master port always sees
    responses (a refused access is answered too); a slave sees what the route model lets
    reach it."""
    proto = bus.protocol
    can = active.get(bus.name, set()) if bus.is_slave else {"read", "write"}
    write, read = "write" in can, "read" in can
    rows: list[tuple[str, int, int, bool, bool]]  # signal, width, quiet, assert, cover
    if proto in AXI_FAMILY and bus.is_slave:
        rows = [("awvalid", 1, 0, True, write), ("wvalid", 1, 0, True, write),
                ("arvalid", 1, 0, True, read), ("bvalid", 1, 0, False, False),
                ("rvalid", 1, 0, False, False)]  # fmt: skip
    elif proto in AXI_FAMILY:
        rows = [("bvalid", 1, 0, True, True), ("rvalid", 1, 0, True, True),
                ("awvalid", 1, 0, False, False), ("wvalid", 1, 0, False, False),
                ("arvalid", 1, 0, False, False)]  # fmt: skip
    elif proto is Protocol.AHB_LITE and bus.is_slave:
        rows = [("htrans", 2, 0, True, bool(can)), ("hreadyout", 1, 1, False, False)]
    elif proto is Protocol.AHB_LITE:
        rows = [("hready", 1, 1, True, True), ("htrans", 2, 0, False, False)]
    else:  # APB3, APB4
        rows = [("psel", 1, 0, bus.is_slave, bus.is_slave and bool(can))]
    prefix = {p.port: p.prefix for p in spec.wrapper.ports}.get(bus.name)
    token, pattern = PROTO_TOKEN[proto], spec.wrapper.signal_pattern
    return [
        Watch(flat_signal_name(pattern, bus.name, token, s, prefix), w, q, a, c)
        for s, w, q, a, c in rows
        if s in bus.signals and s not in bus.ties
    ]


def props_module(spec: Spec) -> str:
    return f"bag_{spec.project.name}_reset_props"


def _step(q: str, init: int, d: str) -> str:
    """A register Q on the solver's own step, starting at INIT, loading D every step."""
    return f"  bag_step_ff{init} u_{q} (.d({d}), .q({q}));"


def _asserted(spec: Spec, reset: str) -> str:
    """SV expression: RESET is asserted."""
    r = next(x for x in spec.resets if x.name == reset)
    return f"!{reset}" if r.polarity == "active-low" else reset


def _group(spec: Spec, k: int, group: list[str], names: list[str]) -> list[str]:
    """Reset group K's environment: asserted at the start; a reset asserts only with the
    rest of the group; release in any order."""
    every = " && ".join(f"({_asserted(spec, r)})" for r in names)
    out = [
        f"  // Reset group {k}: {', '.join(group)} (D51). Every reset is asserted at the start;",
        "  // a reset asserts only with the rest of its group; they release in any order.",
    ]
    for r in names:
        out += [
            f"  logic bag_was_{r};",
            _step(f"bag_was_{r}", 1, _asserted(spec, r)),
            f"  always_comb if (bag_init) assume ({_asserted(spec, r)});",
        ]
        if len(names) > 1:
            out.append(f"  always_comb if (({_asserted(spec, r)}) && !bag_was_{r}) "
                       f"assume ({every});")  # fmt: skip
    return out


@dataclass(frozen=True)
class Domain:
    """The environment's names for one clock domain."""

    clock: str
    held: str  # its reset input is asserted
    quiet: str  # the reset has taken effect in the domain
    in_reset: str  # the domain is in reset as the design sees it (also before its first reset)
    group_done: str  # every domain of its reset group has been through a full reset
    checker_reset: str  # what a clock-edge checker sees as reset (in_reset, held to the edge)


Env = tuple[list[str], dict[str, Domain]]


def _environment(spec: Spec, netlist: Netlist, port: "_Ports") -> Env:
    """The environment both property modules share: the solver step, the reset groups,
    each domain's reset state, and what masters and slaves do in reset."""
    clocks = {c.domain: c for c in spec.clocks}
    resets = {r.domain: r for r in spec.resets}
    stages, bits = RST_SYNC_STAGES, RST_SYNC_STAGES.bit_length()
    domains: dict[str, Domain] = {}
    body = [
        "  // Registers on the solver's own step (bag_step_ff*, formal/bag_step.v) see every",
        "  // step, whatever the clocks do. bag_init is high in the first step only.",
        "  logic bag_init;",
        _step("bag_init", 1, "1'b0"),
        "",
    ]

    for k, group in enumerate(reset_groups(spec)):
        names = sorted({clocks[d].reset for d in group})
        for d in group:
            port(clocks[d].name)
        for r in names:
            port(r)
        body += _group(spec, k, group, names)
        # Activity counts only once every domain of the group has been through a reset that
        # took effect: the start is not a real state.
        done = " && ".join(f"bag_done_{d}" for d in group)
        body += [f"  logic bag_done_{d};  // {d} has been through a full reset" for d in group]
        # D51 step 4: traffic starts once every domain of the group has left reset. A
        # sync-deassertion reset leaves the domain only after the synchronizer's stages.
        body += [f"  logic bag_leaving_{d};  // {d} released but not yet out of reset"
                 for d in group]  # fmt: skip
        leaving = " || ".join(f"bag_leaving_{d}" for d in group)
        for d in group:
            clk, rst = clocks[d].name, resets[d]
            held, quiet = f"bag_held_{d}", f"bag_quiet_{d}"
            body += ["", f"  // {d}: clock {clk}, reset {rst.name} ({rst.assertion} assertion)",
                     f"  wire {held} = {_asserted(spec, rst.name)};"]  # fmt: skip
            if rst.assertion == "sync":
                # The synchronizer takes its stages to pass a sync assertion into the domain,
                # and the reset is held until it has (D51).
                edges = f"bag_edges_{d}"
                body += [
                    f"  logic [{bits - 1}:0] {edges} = {bits}'d0;",
                    f"  always_ff @(posedge {clk}) {edges} <= !{held} ? {bits}'d0 : "
                    f"{edges} == {bits}'d{stages} ? {edges} : {edges} + {bits}'d1;",
                    f"  wire {quiet} = {held} && {edges} == {bits}'d{stages};",
                    f"  logic bag_quiet_was_{d};",
                    _step(f"bag_quiet_was_{d}", 0, quiet),
                    f"  always_comb if (bag_was_{rst.name} && !{held}) assume (bag_quiet_was_{d});",
                ]
            else:
                body.append(f"  wire {quiet} = {held};")
            body.append(_step(f"bag_done_{d}", 0, f"bag_done_{d} || {quiet}"))
            # In reset as the design sees it: before the first reset has taken effect the
            # design's state is not yet defined, so that counts as reset too.
            in_reset = f"bag_in_reset_{d}"
            body.append(f"  wire {in_reset} = {quiet} || !bag_done_{d};")
            # A checker samples its reset at clock edges. A reset that an edge saw ends at its
            # release, as AXI defines it. But an async reset can come and go between two edges
            # and still clear the domain: no edge saw it, so until the next edge it counts as
            # current, and the checker knows its handshakes were cleared. `since` is set by
            # the reset at once and cleared at the next edge; `seen` is the reset as the last
            # edge sampled it.
            since, seen = f"bag_reset_since_edge_{d}", f"bag_reset_seen_{d}"
            checker = f"bag_checker_reset_{d}"
            body += [
                f"  logic {since};",
                f"  always_ff @(posedge {clk} or posedge {in_reset})",
                f"    if ({in_reset}) {since} <= 1'b1;",
                f"    else {since} <= 1'b0;",
                f"  logic {seen} = 1'b0;  // no edge has seen anything yet",
                f"  always_ff @(posedge {clk}) {seen} <= {in_reset};",
                f"  wire {checker} = {in_reset} || ({since} && !{seen});",
            ]
            # Masters and slaves drive nothing while the checker sees reset, and a master first
            # drives a VALID at a clock edge after reset (AMBA AXI A3.1.2): so until the edge
            # after the reset too, which is also what ZipCPU's checkers assume.
            # And until every domain of the group has left reset (D51 step 4).
            if rst.deassertion == "sync":
                out_edges = f"bag_out_edges_{d}"
                body += [
                    f"  logic [{bits - 1}:0] {out_edges} = {bits}'d0;",
                    f"  always_ff @(posedge {clk}) {out_edges} <= {in_reset} ? {bits}'d0 : "
                    f"{out_edges} == {bits}'d{stages} ? {out_edges} : {out_edges} + {bits}'d1;",
                    f"  assign bag_leaving_{d} = !{in_reset} && {out_edges} != {bits}'d{stages};",
                ]
            else:
                body.append(f"  assign bag_leaving_{d} = 1'b0;")
            env_quiet = f"bag_env_quiet_{d}"
            body += [
                f"  logic bag_checker_reset_was_{d} = 1'b1;",
                f"  always_ff @(posedge {clk}) bag_checker_reset_was_{d} <= {checker};",
                f"  wire {env_quiet} = {checker} || bag_checker_reset_was_{d} || {leaving};",
            ]
            domains[d] = Domain(clk, held, quiet, in_reset, f"({done})", checker)
            for bus in netlist.ports:
                if bus.external and bus.domain == d:
                    for w in _watches(spec, bus, {}):
                        if not w.assert_:
                            port(w.name, w.width)
                            body.append(f"  always_comb if ({env_quiet}) "
                                        f"assume ({w.name} == {w.width}'d{w.quiet});")  # fmt: skip
        body.append("")
    body += _alias_pins(spec, domains, port)
    return body, domains


def _alias_pins(spec: Spec, domains: dict[str, Domain], port: "_Ports") -> list[str]:
    """Alias-enable pins are quasi-static: they change only while every domain whose fabric
    uses them is in reset. Without this, a pin toggling under a transfer moves the decode
    mid-access, which no design contract allows."""
    users: dict[str, set[str]] = {}
    for f in spec.fabrics:
        for d in f.decode:
            for a in d.aliases:
                if a.enable != "always":
                    users.setdefault(a.enable, set()).add(f.domain)
    out: list[str] = []
    for pin in sorted(users):
        doms = sorted(users[pin])
        port(pin)
        held = " && ".join(domains[d].in_reset for d in doms)
        out += [
            f"  // Alias enable {pin} ({', '.join(doms)}): changes only in reset.",
            f"  logic bag_pin_was_{pin};",
            f"  always_ff @(posedge {domains[doms[0]].clock}) bag_pin_was_{pin} <= {pin};",
            f"  always_comb if (!({held})) assume ({pin} == bag_pin_was_{pin});",
            "",
        ]
    return out


class _Ports:
    """The inputs of a property module, in the order first used."""

    def __init__(self) -> None:
        self.decls: list[str] = []

    def __call__(self, name: str, width: int = 1) -> None:
        decl = f"input logic {f'[{width - 1}:0] ' if width > 1 else ''}{name}"
        if decl not in self.decls:
            self.decls.append(decl)

    def render(self) -> str:
        return ",\n".join(f"    {d}" for d in self.decls)


def _module(name: str, top: str, hdr: str, what: str, ports: _Ports, body: list[str]) -> str:
    return (
        f"{hdr}\n"
        f"// {what} for {top} (see hdl_bag.formal_gen).\n"
        f"module {name} (\n{ports.render()}\n);\n\n" + "\n".join(body) + "endmodule\n\n"
        f"bind {top} {name} u_{name} (.*);\n"
    )


def render_props(spec: Spec, netlist: Netlist, top: str, hdr: str) -> str:
    """The quiet-in-reset property module for SPEC, bound into TOP."""
    ports = _Ports()
    body, domains = _environment(spec, netlist, ports)
    active = _active(spec)
    for d, dom in domains.items():
        lines = []
        for bus in netlist.ports:
            if bus.external and bus.domain == d:
                lines += _signals(spec, bus, active, dom, ports)
        if lines:
            body += [f"  // {d}: what the design drives in reset", *lines, ""]
    return _module(props_module(spec), top, hdr, "Quiet-in-reset properties", ports, body)


def _signals(spec: Spec, bus: Bus, active: dict[str, set[str]], dom: Domain,
             port: "_Ports") -> list[str]:  # fmt: skip
    out = []
    held, quiet, done = dom.held, dom.quiet, dom.group_done
    for w in _watches(spec, bus, active):
        if not w.assert_:
            continue
        port(w.name, w.width)
        value = f"{w.width}'d{w.quiet}"
        out.append(f"  always_comb if ({quiet}) quiet_{w.name}: assert ({w.name} == {value});")
        if not w.cover:
            continue
        busy, hit = f"bag_busy_{w.name}", f"bag_hit_{w.name}"
        out += [
            f"  logic {busy};  // active in the last step, after a full reset",
            _step(busy, 0, f"{done} && !{held} && {w.name} != {value}"),
            f"  logic {hit} = 1'b0;  // the reset came while it was active",
            _step(hit, 0, f"{hit} || ({held} && {busy})"),
            f"  always_comb reset_mid_{w.name}: cover ({quiet} && {hit});",
        ]
    return out


@dataclass(frozen=True)
class Checker:
    """One ZipCPU checker instance on a port of the top."""

    port: str  # the bus (port) name
    vendor: str  # the vendored module (faxil_slave, ...)
    module: str  # this instance's copy of it, with its parameters set
    params: tuple[tuple[str, int], ...]


def _family(bus: Bus) -> str | None:
    if bus.protocol is Protocol.AXI4_LITE:
        return "axil"
    if bus.protocol in (Protocol.APB3, Protocol.APB4):
        return "apb"
    return None


def checkers(spec: Spec, netlist: Netlist) -> list[Checker]:
    """The protocol checkers SPEC's top gets: one per AXI4-Lite or APB port (D60)."""
    out = []
    active: dict[str, set[str]] | None = None
    for bus in netlist.ports:
        family = _family(bus)
        if not bus.external or family is None:
            continue
        vendor = _CHECKER[(family, bus.is_slave)]
        params: tuple[tuple[str, int], ...]
        if family == "axil":
            params = (("C_AXI_DATA_WIDTH", bus.data_width), ("C_AXI_ADDR_WIDTH", bus.addr_width),
                      ("F_OPT_ASYNC_RESET", 1), ("F_OPT_ASSUME_RESET", 1), ("F_OPT_NO_RESET", 1),
                      ("F_LGDEPTH", 8), ("F_AXI_MAXWAIT", 0), ("F_AXI_MAXRSTALL", 0),
                      ("F_AXI_MAXDELAY", 0))  # fmt: skip
            if bus.is_slave:
                # A slave no master may write (or read), by the route model: the checker
                # then asserts the design never does, and drops the cover it can't reach.
                active = _active(spec) if active is None else active
                can = active.get(bus.name, set())
                if can == {"read"}:
                    params += (("F_OPT_READ_ONLY", 1),)
                elif can == {"write"}:
                    params += (("F_OPT_WRITE_ONLY", 1),)
        else:
            slverr = "pslverr" in bus.signals and "pslverr" not in bus.ties
            params = (("AW", bus.addr_width), ("DW", bus.data_width), ("F_OPT_MAXSTALL", 0),
                      ("F_OPT_SLVERR", int(slverr)), ("F_OPT_ASYNC_RESET", 1))  # fmt: skip
        out.append(Checker(bus.name, vendor, f"bag_{spec.project.name}_{vendor}_{bus.name}",
                           params))  # fmt: skip
    return out


# The checkers' port names, in the order of each vendored module's ports.
_AXIL = ("awvalid", "awready", "awaddr", "awprot", "wvalid", "wready", "wdata", "wstrb",
         "bvalid", "bready", "bresp", "arvalid", "arready", "araddr", "arprot",
         "rvalid", "rready", "rdata", "rresp")  # fmt: skip
_APB = {"PSEL": "psel", "PENABLE": "penable", "PREADY": "pready", "PADDR": "paddr",
        "PWRITE": "pwrite", "PWDATA": "pwdata", "PWSTRB": "pstrb", "PPROT": "pprot",
        "PRDATA": "prdata", "PSLVERR": "pslverr"}  # fmt: skip


def _value(spec: Spec, bus: Bus, sig: str, port: "_Ports") -> str:
    """What BUS carries on SIG: its top-level port, a tied value, or for a signal the
    protocol lacks (APB3 PSTRB and PPROT, a PSLVERR not used) the value the spec implies."""
    width = signal_width(sig, bus.addr_width, bus.data_width)
    prefix = {p.port: p.prefix for p in spec.wrapper.ports}.get(bus.name)
    name = flat_signal_name(spec.wrapper.signal_pattern, bus.name, PROTO_TOKEN[bus.protocol],
                            sig, prefix)  # fmt: skip
    tie = bus.ties.get(sig)
    if sig in bus.signals and tie is None:
        port(name, width)
        return name
    if tie == TIE_WRITE_STROBE or (tie is None and sig == "pstrb"):
        write = _value(spec, bus, "pwrite", port)
        return f"({write} ? {width}'h{(1 << width) - 1:X} : {width}'h0)"
    return lit(width, tie or 0)


def _checker(spec: Spec, bus: Bus, chk: Checker, dom: Domain,
             port: "_Ports") -> list[str]:  # fmt: skip
    if chk.vendor.startswith("faxil"):
        conns = [(".i_clk", dom.clock), (".i_axi_reset_n", f"!{dom.checker_reset}")]
        conns += [(f".i_axi_{s}", _value(spec, bus, s, port)) for s in _AXIL]
        conns += [(f".f_axi_{c}", "") for c in
                  ("rd_outstanding", "wr_outstanding", "awr_outstanding")]  # fmt: skip
    else:
        conns = [(".PCLK", dom.clock), (".PRESETn", f"!{dom.checker_reset}")]
        conns += [(f".{p}", _value(spec, bus, s, port)) for p, s in _APB.items()]
        if chk.vendor == "fapb_slave":
            # BAG's fabric holds PREADY high while PSEL is low, in reset too (D61, owner).
            # The spec leaves PREADY undefined while PSEL is low; fapb_slave wants it low in
            # and just after reset (when PSEL is low), so it sees PREADY only with PSEL.
            psel = _value(spec, bus, "psel", port)
            conns = [(c, f"({v} && {psel})" if c == ".PREADY" else v) for c, v in conns]
    lines = [f"  {chk.module} u_{chk.port} ("]
    lines += [f"      {c}({v})," for c, v in conns]
    lines[-1] = lines[-1].rstrip(",")
    return [*lines, "  );"]


# AXI4 channels: VALID, READY, and the payload VALID qualifies.
_AXI4_CHANNELS = {
    "aw": ("awvalid", "awready", ("awid", "awaddr", "awlen", "awsize", "awburst", "awlock",
                                  "awcache", "awprot", "awqos", "awregion", "awuser")),
    "w": ("wvalid", "wready", ("wdata", "wstrb", "wlast", "wuser")),
    "ar": ("arvalid", "arready", ("arid", "araddr", "arlen", "arsize", "arburst", "arlock",
                                  "arcache", "arprot", "arqos", "arregion", "aruser")),
    "b": ("bvalid", "bready", ("bid", "bresp", "buser")),
    "r": ("rvalid", "rready", ("rid", "rdata", "rresp", "rlast", "ruser")),
}  # fmt: skip


def _axi4_handshake_env(spec: Spec, bus: Bus, dom: Domain, port: "_Ports") -> list[str]:
    """The AXI handshake rule (AMBA AXI A3.2.1) as an assumption on what the environment
    drives into an AXI4 port: once VALID is high without READY at a clock edge, VALID and its
    payload hold until the handshake; a reset abandons the transfer. A master that withdrew
    a request would otherwise move a fabric's arbitration under a stalled transfer
    downstream. BAG's own AXI4 assertions, and the burst rules, are PR 2b."""
    theirs = ("aw", "w", "ar") if not bus.is_slave else ("b", "r")
    prefix = {p.port: p.prefix for p in spec.wrapper.ports}.get(bus.name)
    token, pattern = PROTO_TOKEN[bus.protocol], spec.wrapper.signal_pattern

    def name(sig: str) -> str:
        return flat_signal_name(pattern, bus.name, token, sig, prefix)

    def width(sig: str) -> int:
        return signal_width(sig, bus.addr_width, bus.data_width, bus.id_width, bus.user_width)

    out = [f"  // {bus.name}: AXI4 handshake rule on what the environment drives (A3.2.1)"]
    for ch in theirs:
        valid, ready, payload = _AXI4_CHANNELS[ch]
        sigs = [s for s in payload if s in bus.signals and s not in bus.ties]
        for s in (valid, ready, *sigs):
            port(name(s), width(s))
        pend, held = f"bag_pend_{bus.name}_{ch}", f"bag_payload_{bus.name}_{ch}"
        total = sum(width(s) for s in sigs)
        cat = "{" + ", ".join(name(s) for s in sigs) + "}"
        out += [
            f"  logic {pend} = 1'b0;",
            f"  always_ff @(posedge {dom.clock}) {pend} <= {name(valid)} && !{name(ready)};",
        ]
        cond = f"{pend} && !{dom.checker_reset}"
        out.append(f"  always_comb if ({cond}) assume ({name(valid)});")
        if sigs:
            out += [
                f"  logic [{total - 1}:0] {held};",
                f"  always_ff @(posedge {dom.clock}) {held} <= {cat};",
                f"  always_comb if ({cond}) assume ({cat} == {held});",
            ]
    return [*out, ""]


def render_protocol(spec: Spec, netlist: Netlist, top: str, hdr: str) -> str | None:
    """The port-protocol property module for SPEC, bound into TOP; None with no port to check."""
    found = checkers(spec, netlist)
    if not found:
        return None
    ports = _Ports()
    body, domains = _environment(spec, netlist, ports)
    buses = {b.name: b for b in netlist.ports}
    for chk in found:
        bus = buses[chk.port]
        side, design = ("slave", "master") if bus.is_slave else ("master", "slave")
        body.append(f"  // {chk.port}: {bus.protocol.value} {side} port; the design is its "
                    f"{design} ({chk.vendor})")  # fmt: skip
        body += [*_checker(spec, bus, chk, domains[bus.domain], ports), ""]
    for bus in netlist.ports:
        if bus.external and bus.protocol is Protocol.AXI4:
            body += _axi4_handshake_env(spec, bus, domains[bus.domain], ports)
    name = f"bag_{spec.project.name}_protocol_props"
    return _module(name, top, hdr, "Port-protocol checkers (ZipCPU, D60)", ports, body)


def render_sby(spec: Spec, top: str, files: list[str], incdirs: list[str], props: str,
               user_header: str | None) -> str:  # fmt: skip
    """The SymbiYosys job for PROPS (a file in formal/) over the design FILES (relative to the
    output directory, in compile order) with TOP. It runs from formal/: SBY copies rtl/ and
    the property file into its work directory."""
    job = sby_job(top=top, files=[*files, props], incdirs=incdirs,
                  depths={"prove": PROVE_DEPTH, "cover": COVER_DEPTH},
                  copy=[("rtl", "../rtl"), (STEP, STEP), (props, props)], engine=ENGINE,
                  prelude=[f"read_verilog -formal {STEP}"])  # fmt: skip
    return f"{_sby_header(spec, user_header)}\n{job}"


def _sby_header(spec: Spec, user_header: str | None) -> str:
    return "".join(f"# {line}".rstrip() + "\n" for line in header(spec, user_header).splitlines())


def render_protocol_sby(spec: Spec, netlist: Netlist, top: str, files: list[str],
                        incdirs: list[str], props: str,
                        user_header: str | None) -> str:  # fmt: skip
    """The SymbiYosys job for the protocol checkers: the vendored files read by Yosys's own
    frontend, one parameterised copy per checker instance, then the design and PROPS."""
    found = checkers(spec, netlist)
    vendored = sorted({c.vendor for c in found})
    prelude = [
        f"read_verilog -formal {STEP}",
        *(f"read_verilog -formal zipcpu/{v}.v" for v in vendored),
    ]
    for c in found:
        sets = " ".join(f"-set {k} {v}" for k, v in c.params)
        prelude += [f"copy {c.vendor} {c.module}", f"chparam {sets} {c.module}"]
    job = sby_job(top=top, files=[*files, props], incdirs=incdirs,
                  depths={"bmc": PROTOCOL_DEPTH, "cover": COVER_DEPTH},
                  copy=[("rtl", "../rtl"), (STEP, STEP), ("zipcpu", "vendor/zipcpu"),
                        (props, props)],
                  engine=ENGINE, prelude=prelude)  # fmt: skip
    return f"{_sby_header(spec, user_header)}\n{job}"


def render_formal(spec: Spec, netlist: Netlist, top: str, files: list[str], incdirs: list[str],
                  user_header: str | None) -> list[tuple[str, str]]:  # fmt: skip
    """Every formal output of SPEC: (path relative to the output directory, text)."""
    stem = f"{spec.project.name}_reset"
    if not all(f.startswith("rtl/") for f in files):
        raise ValueError(f"formal expects every design file under rtl/: {files}")
    hdr = header(spec, user_header)
    props = render_props(spec, netlist, top, hdr)
    sby = render_sby(spec, top, files, incdirs, f"{stem}.sv", user_header)
    out = [(f"formal/{stem}.sv", props), (f"formal/{stem}.sby", sby)]
    protocol = render_protocol(spec, netlist, top, hdr)
    if protocol is not None:
        name = f"{spec.project.name}_protocol"
        out += [(f"formal/{name}.sv", protocol),
                (f"formal/{name}.sby", render_protocol_sby(spec, netlist, top, files, incdirs,
                                                           f"{name}.sv", user_header))]  # fmt: skip
    return out


def vendored_files(spec: Spec, netlist: Netlist) -> list[str]:
    """The packaged formal files SPEC's jobs read (package paths): the step registers, and the
    vendored checkers with their LICENSE."""
    found = sorted({c.vendor for c in checkers(spec, netlist)})
    vendored = [f"{ZIPCPU}/{v}.v" for v in found] + ([f"{ZIPCPU}/LICENSE"] if found else [])
    return [f"formal/{STEP}", *vendored]
