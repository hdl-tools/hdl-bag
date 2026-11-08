# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal generation: SymbiYosys jobs and the properties they prove (Phase 5).

Output layout (relative to the output directory)::

    formal/<project>_reset.sv     property module, bound into the lint top
    formal/<project>_reset.sby    SymbiYosys job: a prove task and a cover task

Properties attach with ``bind`` to the lint top (the flat top, or ``<top>_flat`` in the
sv-interface style), so they see its ports by name and the design needs no formal
wrapper. Its inputs are free; the property module assumes what the environment must do.
Clocks are free too (multiclock), so any clock ratio and phase is covered. Registers that
must see every solver step, whatever the clocks do, are clocked by a ``(* gclk *)`` net.

The first property family is quiet-in-reset, the formal form of the generated test
``reset_during_traffic_leaves_nothing_behind``: while a domain's reset is held, the design
asks nothing of the masters and slaves in that domain (no request VALID, PSEL or HTRANS
towards a slave, no response VALID towards an AXI master, HREADY high towards an AHB-Lite
master). A domain whose reset asserts synchronously gets the synchronizer's stages first.
It is proved by k-induction, so it holds in every reachable state, not only to a depth.

The environment, from the AMBA specifications and the reset sequence (D51): masters and
slaves in reset drive no VALID (AXI) or PSEL (APB), and HTRANS IDLE and HREADYOUT high
(AHB-Lite); every reset is asserted at the start; a reset asserts only together with the
rest of its reset group, and the group's resets release in any order; a reset is released
only after it has taken effect.

Covers keep each assertion honest, as ``checked > 0`` does for a test: for each asserted
signal, the reset arrives while the signal is active (after the design has been through a
reset that took effect), and the domain then reaches the window the assertion checks. A
signal stuck at its reset value, or a property only ever checked in the first reset, fails
its cover. A cover is generated only where the route model says the signal can be active:
a slave no master may write never sees a write request.

A design with a user-supplied fabric gets no formal, because BAG does not know how that
fabric behaves (D8).
"""

from collections.abc import Callable
from dataclasses import dataclass

from hdl_bag.flows.formal import sby_job
from hdl_bag.gen.collateral import RST_SYNC_STAGES, reset_groups
from hdl_bag.gen.netlist import Bus, Netlist
from hdl_bag.gen.render import header
from hdl_bag.ir.model import Spec
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import AXI_FAMILY, PROTO_TOKEN, Protocol

# k-induction depth for the assertions, in solver steps. The quiet property depends on the
# reset state and the synchronizer's stage count, so a few steps make it inductive (demo_ss
# proves at 6).
PROVE_DEPTH = 6
# Cover search depth in solver steps. With free clocks each clock edge takes steps of its
# own: a signal behind a bridge and a clock crossing needs edges of every clock on the way,
# then a reset. The formal corpus tunes this per config class (Phase 5 PR 4).
COVER_DEPTH = 40

ENGINE = "smtbmc yices"


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
            f"  logic bag_was_{r} = 1'b1;",
            f"  always_ff @(posedge bag_step) bag_was_{r} <= {_asserted(spec, r)};",
            f"  always_comb if (bag_init) assume ({_asserted(spec, r)});",
        ]
        if len(names) > 1:
            out.append(f"  always_comb if (({_asserted(spec, r)}) && !bag_was_{r}) "
                       f"assume ({every});")  # fmt: skip
    return out


def render_props(spec: Spec, netlist: Netlist, top: str, hdr: str) -> str:
    """The quiet-in-reset property module for SPEC, bound into TOP."""
    clocks = {c.domain: c for c in spec.clocks}
    resets = {r.domain: r for r in spec.resets}
    active = _active(spec)
    stages, bits = RST_SYNC_STAGES, RST_SYNC_STAGES.bit_length()
    ports: list[str] = []
    body = [
        "  // The solver's own step: a register on it sees every step, whatever the clocks do.",
        "  (* gclk *) logic bag_step;",
        "  logic bag_init = 1'b1;",
        "  always_ff @(posedge bag_step) bag_init <= 1'b0;",
        "",
    ]

    def port(name: str, width: int = 1) -> None:
        decl = f"input logic {f'[{width - 1}:0] ' if width > 1 else ''}{name}"
        if decl not in ports:
            ports.append(decl)

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
        body += [f"  logic bag_done_{d} = 1'b0;  // {d} has been through a full reset"
                 for d in group]  # fmt: skip
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
                    f"  logic bag_quiet_was_{d} = 1'b0;",
                    f"  always_ff @(posedge bag_step) bag_quiet_was_{d} <= {quiet};",
                    f"  always_comb if (bag_was_{rst.name} && !{held}) assume (bag_quiet_was_{d});",
                ]
            else:
                body.append(f"  wire {quiet} = {held};")
            body.append(f"  always_ff @(posedge bag_step) if ({quiet}) bag_done_{d} <= 1'b1;")
            for bus in netlist.ports:
                if bus.external and bus.domain == d:
                    body += _signals(spec, bus, active, held, quiet, done, port)
        body.append("")
    name = props_module(spec)
    decls = ",\n".join(f"    {p}" for p in ports)
    return (
        f"{hdr}\n"
        f"// Quiet-in-reset properties for {top} (see hdl_bag.formal_gen).\n"
        f"module {name} (\n{decls}\n);\n\n" + "\n".join(body) + "endmodule\n\n"
        f"bind {top} {name} u_{name} (.*);\n"
    )


def _signals(spec: Spec, bus: Bus, active: dict[str, set[str]], held: str, quiet: str,
             done: str, port: Callable[[str, int], None]) -> list[str]:  # fmt: skip
    out = []
    for w in _watches(spec, bus, active):
        port(w.name, w.width)
        value = f"{w.width}'d{w.quiet}"
        if not w.assert_:
            out.append(f"  always_comb if ({held}) assume ({w.name} == {value});")
            continue
        out.append(f"  always_comb if ({quiet}) quiet_{w.name}: assert ({w.name} == {value});")
        if not w.cover:
            continue
        busy, hit = f"bag_busy_{w.name}", f"bag_hit_{w.name}"
        out += [
            f"  logic {busy} = 1'b0;  // active in the last step, after a full reset",
            f"  always_ff @(posedge bag_step) {busy} <= ({done}) && !{held} && "
            f"{w.name} != {value};",
            f"  logic {hit} = 1'b0;  // the reset came while it was active",
            f"  always_ff @(posedge bag_step) if ({held} && {busy}) {hit} <= 1'b1;",
            f"  always_comb reset_mid_{w.name}: cover ({quiet} && {hit});",
        ]
    return out


def render_sby(spec: Spec, top: str, files: list[str], incdirs: list[str], props: str,
               user_header: str | None) -> str:  # fmt: skip
    """The SymbiYosys job for PROPS (a file in formal/) over the design FILES (relative to the
    output directory, in compile order) with TOP. It runs from formal/: SBY copies rtl/ and
    the property file into its work directory."""
    hdr = "".join(f"# {line}".rstrip() + "\n" for line in header(spec, user_header).splitlines())
    job = sby_job(top=top, files=[*files, props], incdirs=incdirs,
                  depths={"prove": PROVE_DEPTH, "cover": COVER_DEPTH},
                  copy=[("rtl", "../rtl"), (props, props)], engine=ENGINE)  # fmt: skip
    return f"{hdr}\n{job}"


def render_formal(spec: Spec, netlist: Netlist, top: str, files: list[str], incdirs: list[str],
                  user_header: str | None) -> list[tuple[str, str]]:  # fmt: skip
    """Every formal output of SPEC: (path relative to the output directory, text)."""
    stem = f"{spec.project.name}_reset"
    if not all(f.startswith("rtl/") for f in files):
        raise ValueError(f"formal expects every design file under rtl/: {files}")
    props = render_props(spec, netlist, top, header(spec, user_header))
    sby = render_sby(spec, top, files, incdirs, f"{stem}.sv", user_header)
    return [(f"formal/{stem}.sv", props), (f"formal/{stem}.sby", sby)]
