# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal generation: SymbiYosys jobs and the properties they prove (Phase 5).

Output layout (relative to the output directory)::

    formal/<project>_reset.sv     property module, bound into the lint top
    formal/<project>_reset.sby    SymbiYosys job: a bmc task and a cover task

Properties attach with ``bind`` to the lint top (the flat top, or ``<top>_flat`` in the
sv-interface style), so they see its ports by name and the design needs no formal
wrapper. Its inputs are free; the property module assumes what the environment must do.

The first property family is quiet-in-reset, the formal form of the generated test
``reset_during_traffic_leaves_nothing_behind``: while a domain's reset is held, the design
asks nothing of the masters and slaves in that domain (no request VALID, PSEL or HTRANS
towards a slave, no response VALID towards an AXI master, HREADY high towards an AHB-Lite
master). Like the test, a domain whose reset asserts synchronously gets the synchronizer's
stages first. The environment assumptions, from the AMBA specifications: masters and
slaves in reset drive no VALID (AXI), no PSEL (APB) and HTRANS IDLE (AHB-Lite); every
reset group (D51) asserts and releases together; every reset is asserted at the start.

Covers keep each assertion honest, as ``checked > 0`` does for a test: each signal the
property holds low in reset must be seen active out of reset (a signal stuck at its reset
value would pass vacuously), and each reset group must be seen to reset again after it.

A design with a user-supplied fabric gets no formal, because BAG does not know how that
fabric behaves (D8).
"""

from dataclasses import dataclass

from hdl_bag.flows.formal import sby_job
from hdl_bag.gen.collateral import RST_SYNC_STAGES, reset_groups
from hdl_bag.gen.netlist import Bus, Netlist
from hdl_bag.gen.render import header
from hdl_bag.ir.model import Spec
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import AXI_FAMILY, PROTO_TOKEN, Protocol

# BMC depth in solver steps. With several clocks (multiclock), each clock edge takes steps
# of its own, so this is a bound on events, not cycles. The formal corpus tunes it per
# config class (Phase 5 PR 4).
BMC_DEPTH = 20
# Covers search deeper: a signal behind a bridge and a clock crossing needs edges of every
# clock on the way before it can rise.
COVER_DEPTH = 40

# Free-running solver for both tasks.
ENGINE = "smtbmc yices"


@dataclass(frozen=True)
class Watch:
    """One top-level signal the property watches in reset."""

    name: str  # top-level port name
    width: int
    quiet: int  # its value while the domain is in reset
    assert_: bool  # True: the design drives it (assert); False: the environment does (assume)


def _watches(spec: Spec, bus: Bus) -> list[Watch]:
    """What BUS's port carries in reset: the design's outputs to assert, the environment's
    inputs to assume. Signals the port lacks are skipped."""
    proto = bus.protocol
    if proto in AXI_FAMILY:
        requests, responses = ("awvalid", "wvalid", "arvalid"), ("bvalid", "rvalid")
        mine, theirs = (requests, responses) if bus.is_slave else (responses, requests)
        rows = [(s, 1, 0, True) for s in mine] + [(s, 1, 0, False) for s in theirs]
    elif proto is Protocol.AHB_LITE:
        rows = [("htrans", 2, 0, True)] if bus.is_slave else [
            ("hready", 1, 1, True), ("htrans", 2, 0, False)]  # fmt: skip
    else:  # APB3, APB4
        rows = [("psel", 1, 0, bus.is_slave)]
    prefix = {p.port: p.prefix for p in spec.wrapper.ports}.get(bus.name)
    token = PROTO_TOKEN[proto]
    return [
        Watch(flat_signal_name(spec.wrapper.signal_pattern, bus.name, token, s, prefix), w, q, a)
        for s, w, q, a in rows
        if s in bus.signals and s not in bus.ties
    ]


def props_module(spec: Spec) -> str:
    return f"bag_{spec.project.name}_reset_props"


def _asserted(spec: Spec, reset: str) -> str:
    """SV expression: RESET is asserted."""
    r = next(x for x in spec.resets if x.name == reset)
    return f"!{reset}" if r.polarity == "active-low" else reset


def render_props(spec: Spec, netlist: Netlist, top: str, hdr: str) -> str:
    """The quiet-in-reset property module for SPEC, bound into TOP."""
    clocks = {c.domain: c for c in spec.clocks}
    resets = {r.domain: r for r in spec.resets}
    ports: list[str] = []
    body: list[str] = []

    def port(name: str, width: int = 1) -> None:
        decl = f"input logic {f'[{width - 1}:0] ' if width > 1 else ''}{name}"
        if decl not in ports:
            ports.append(decl)

    for k, group in enumerate(reset_groups(spec)):
        first_clk = clocks[group[0]].name
        names = sorted({clocks[d].reset for d in group})
        for d in group:
            port(clocks[d].name)
        for r in names:
            port(r)
        body += [
            f"  // Reset group {k}: {', '.join(group)}. Every reset is asserted at the start, "
            "and the group's resets assert and release together (D51).",
            f"  logic bag_first_{k} = 1'b1;",
            f"  always_ff @(posedge {first_clk}) bag_first_{k} <= 1'b0;",
            f"  always_comb if (bag_first_{k}) assume ({_asserted(spec, names[0])});",
        ]
        body += [f"  always_comb assume (({_asserted(spec, names[0])}) == ({_asserted(spec, r)}));"
                 for r in names[1:]]  # fmt: skip
        busy: list[str] = []
        for d in group:
            clk, rst = clocks[d].name, resets[d]
            held = f"bag_held_{d.lower()}"
            body += ["", f"  // {d}: clock {clk}, reset {rst.name} ({rst.assertion} assertion)",
                     f"  wire {held} = {_asserted(spec, rst.name)};"]  # fmt: skip
            quiet = held
            if rst.assertion == "sync":
                # The synchronizer takes its stages to pass a sync assertion into the domain.
                edges = f"bag_edges_{d.lower()}"
                body += [
                    f"  logic [1:0] {edges} = 2'd0;",
                    f"  always_ff @(posedge {clk}) {edges} <= !{held} ? 2'd0 : "
                    f"{edges} == 2'd3 ? 2'd3 : {edges} + 2'd1;",
                    f"  wire bag_quiet_{d.lower()} = {held} && {edges} >= 2'd{RST_SYNC_STAGES};",
                ]
                quiet = f"bag_quiet_{d.lower()}"
            for bus in netlist.ports:
                if not bus.external or bus.domain != d:
                    continue
                for w in _watches(spec, bus):
                    port(w.name, w.width)
                    value = f"{w.width}'d{w.quiet}"
                    if not w.assert_:
                        body.append(f"  always_comb if ({held}) assume ({w.name} == {value});")
                        continue
                    seen = f"bag_seen_{w.name}"
                    body += [
                        f"  always_comb if ({quiet}) quiet_{w.name}: assert ({w.name} == {value});",
                        f"  logic {seen} = 1'b0;",
                        f"  always_ff @(posedge {clk}) if (!{held} && {w.name} != {value}) "
                        f"{seen} <= 1'b1;",
                        f"  always_comb active_{w.name}: cover ({seen});",
                    ]
                    busy.append(seen)
        if busy:
            body += ["", f"  // Group {k} resets again after its design was active.",
                     f"  always_comb reset_again_{k}: cover (({_asserted(spec, names[0])}) && "
                     f"({' || '.join(busy)}));"]  # fmt: skip
        body.append("")
    name = props_module(spec)
    decls = ",\n".join(f"    {p}" for p in ports)
    return (
        f"{hdr}\n"
        f"// Quiet-in-reset properties for {top} (see hdl_bag.formal_gen).\n"
        f"module {name} (\n{decls}\n);\n\n" + "\n".join(body) + "endmodule\n\n"
        f"bind {top} {name} u_{name} (.*);\n"
    )


def render_sby(spec: Spec, top: str, files: list[str], incdirs: list[str], props: str,
               user_header: str | None) -> str:  # fmt: skip
    """The SymbiYosys job for PROPS (a file in formal/) over the design FILES (relative to the
    output directory, in compile order) with TOP. It runs from formal/: SBY copies rtl/ and
    the property file into its work directory."""
    hdr = "".join(f"# {line}".rstrip() + "\n" for line in header(spec, user_header).splitlines())
    job = sby_job(top=top, files=[*files, props], incdirs=incdirs,
                  depths={"bmc": BMC_DEPTH, "cover": COVER_DEPTH},
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
