# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Hierarchy (sub-wrappers, placement), Wrapper (exposed ports, name collisions) and
User_Ports (extra ports of user-supplied fabrics, D41)."""

import re
from typing import TYPE_CHECKING, Literal

from hdl_bag.checks.basics import extra_inputs
from hdl_bag.checks.context import Context
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import (
    OPTIONAL_SIGNAL_NAMES,
    OPTIONAL_SIGNALS,
    PROTO_TOKEN,
    Protocol,
    port_signals,
)
from hdl_bag.reader.model import Row
from hdl_bag.reader.values import parse_connection

if TYPE_CHECKING:
    from hdl_bag.checks import CheckResult


def check_hierarchy(ctx: Context) -> None:
    subs = ctx.raw.sub_wrappers
    parents: dict[str, tuple[str | None, int]] = {}  # name -> (parent, row)
    for i, row in enumerate(subs):
        name = ctx.text(row["Sub-wrapper"])
        parent = ctx.text(row["Parent"], required=True)
        ctx.text(row["Notes"])
        if i == 0:
            if parent is not None and parent != "-":
                ctx.report("HIER-TOP", row["Parent"], "the top wrapper (row 5) must have Parent -")
            if name:
                parents[name] = (None, row.row)
            continue
        if name is None or parent is None or name in parents:
            continue
        if parent == "-":
            ctx.report(
                "HIER-TOP",
                row["Parent"],
                f"only the top wrapper may have Parent -; give {name} a parent",
            )
            parents[name] = (None, row.row)  # still a sub-wrapper, so placements into it resolve
            continue
        parents[name] = (parent, row.row)
    for row in subs[1:]:
        name, parent = ctx.text(row["Sub-wrapper"]), ctx.text(row["Parent"])
        if not name or not parent or parent == "-" or parents.get(name, (None, 0))[1] != row.row:
            continue
        if parent == name:
            ctx.report("HIER-SELF-PARENT", row["Parent"], f"{name} cannot be its own parent")
        elif parent not in parents:
            ctx.report(
                "HIER-UNKNOWN-PARENT", row["Parent"], f"parent {parent} is not a sub-wrapper"
            )
    # Parent loops: walk up from each sub-wrapper; report each loop once, at its first row.
    reported: set[frozenset[str]] = set()
    for name in parents:
        path: list[str] = []
        node: str | None = name
        while node is not None and node in parents and node not in path:
            path.append(node)
            node = parents[node][0]
        if node is not None and node in path and node != parents[node][0]:
            loop = path[path.index(node) :]
            if frozenset(loop) not in reported:
                reported.add(frozenset(loop))
                first = min(loop, key=lambda n: parents[n][1])
                row = next(r for r in subs if r.row == parents[first][1])
                ctx.report(
                    "HIER-PARENT-LOOP",
                    row["Parent"],
                    "sub-wrappers form a parent loop: " + " -> ".join([*loop, loop[0]]),
                )

    instances = {n for n, k in ctx.kinds.items() if k in ("fabric", "bridge")}
    instances |= {a.name for a in ctx.adapters()}
    placed: dict[str, int] = {}
    for row in ctx.raw.placement:
        inst = ctx.text(row["Instance"], required=True)
        sub = ctx.text(row["Sub-wrapper"], required=True)
        if row["RTL instance name"].value is not None:
            ctx.name(row["RTL instance name"])
        ctx.text(row["Notes"])
        if inst is None:
            continue
        if inst not in instances:
            ctx.report(
                "HIER-UNKNOWN-INSTANCE",
                row["Instance"],
                f"{inst} is not a fabric, bridge or adapter instance",
            )
        elif inst in placed:
            ctx.report(
                "HIER-PLACED-TWICE",
                row["Instance"],
                f"{inst} is already placed on row {placed[inst]}",
            )
        else:
            placed[inst] = row.row
        if sub is not None and sub not in parents:
            ctx.report(
                "HIER-UNKNOWN-SUBWRAPPER",
                row["Sub-wrapper"],
                f"{sub} is not defined in the Sub-wrapper table",
            )


def _tied(ctx: Context, port: str) -> frozenset[str]:
    """Signals removed from the wrapper because Port_Options ties them."""
    for row in ctx.raw.port_options:
        if row["Port"].value == port:
            out: set[str] = set()
            for col in OPTIONAL_SIGNALS:
                if row[col].value in ("tie-0", "tie-default"):
                    out |= OPTIONAL_SIGNAL_NAMES[col]
            return frozenset(out)
    return frozenset()


def check_wrapper_ports(ctx: Context) -> None:
    listed: dict[str, int] = {}
    pattern_cell = ctx.raw.wrapper.get("Signal naming pattern")
    pattern = (ctx.text(pattern_cell) if pattern_cell else None) or "{port}_{proto}_{signal}"
    if any(
        f.rule == "WRAP-PATTERN" and f.row == (pattern_cell.row if pattern_cell else -1)
        for f in ctx.findings
    ):
        pattern = "{port}_{proto}_{signal}"
    owners = ctx.top_names  # top-level port name -> owner

    def claim(name: str, owner: str, row_cell: object) -> None:
        if name in owners and owners[name] != owner:
            ctx.report("WRAP-NAME-COLLISION", row_cell,  # type: ignore[arg-type]
                       f"top-level port {name} of {owner} collides with {owners[name]}")  # fmt: skip
        else:
            owners.setdefault(name, owner)

    for rows in (ctx.raw.clocks, ctx.raw.resets):
        for row in rows:
            name = ctx.text(row[row.sheet[:-1]])  # Clocks -> Clock, Resets -> Reset
            if name:
                owners.setdefault(name, f"{row.sheet} {row.where}")
    for name in extra_inputs(ctx):
        owners.setdefault(name, "Wrapper > Extra top-level inputs")

    for row in ctx.raw.exposed_ports:
        port = ctx.text(row["Port"], required=True)
        exposed = ctx.yes_no(row["Exposed at top"])
        prefix = ctx.name(row["Top-level prefix"], required=False)
        internal = ctx.enum(row["If not exposed"], ["internal"], required=False)
        ctx.text(row["Notes"])
        if port is None:
            continue
        kind = ctx.kinds.get(port)
        if kind not in ("master", "slave", "bridge"):
            ctx.report("WRAP-UNKNOWN-PORT", row["Port"], f"{port} is not a master, slave or bridge")
            continue
        if port in listed:
            ctx.report(
                "WRAP-DUPLICATE-PORT",
                row["Port"],
                f"{port} is already listed on row {listed[port]}",
            )
            continue
        listed[port] = row.row
        if kind == "bridge" and exposed:
            ctx.report(
                "WRAP-INTERNAL-PORT",
                row["Exposed at top"],
                f"bridge {port} is always internal; set N",
            )
            continue
        if kind != "bridge" and exposed is False:
            ctx.report(
                "WRAP-INTERNAL-PORT",
                row["Exposed at top"],
                f"{kind} {port} must be exposed; only bridges are internal in v1",
            )
            continue
        if not exposed or internal:
            continue
        side: Literal["master", "slave"] = "master" if kind == "master" else "slave"
        protocol = ctx.port_protocol(port, side)
        if protocol is None:
            continue
        prow = ctx.defs[port]
        id_w = ctx.integer(prow["ID width (bits)"], 1, required=False, tokens=("auto",))
        user = ctx.integer(prow["User width (bits)"], 0, required=False, tokens=("n/a",))
        signals = port_signals(
            protocol, side == "slave",
            has_id=protocol == Protocol.AXI4 and id_w is not None,
            has_user=isinstance(user, int) and user > 0,
            removed=_tied(ctx, port),
        )  # fmt: skip
        for sig in signals:
            name = flat_signal_name(pattern, port, PROTO_TOKEN[protocol], sig, prefix)
            claim(name, port, row["Port"])

    for kind, column in (("master", "Master"), ("slave", "Slave"), ("bridge", "Bridge")):
        for row in ctx.rows(kind):  # type: ignore[arg-type]
            name = ctx.text(row[column])
            if name and ctx.defs.get(name) is row and name not in listed:
                ctx.report(
                    "WRAP-PORT-NOT-LISTED",
                    "Wrapper",
                    f"{kind} {name} is not listed under Exposed ports",
                )


def check_boundary_names(ctx: Context, result: "CheckResult") -> None:
    """Checks on the generator's routing plan (pure; no RTL is generated), reported by
    `bag check` before anything is written: boundary port names unique per sub-wrapper,
    instance names unique per wrapper, sub-wrapper names not taken by a generated design
    unit, and (warning) sub-wrappers with no instances.
    """
    from hdl_bag.gen.hierarchy import plan, problems
    from hdl_bag.gen.netlist import build_netlist
    from hdl_bag.ir.resolve import resolve

    netlist = build_netlist(resolve(result))
    rows = {ctx.text(r["Sub-wrapper"]): r for r in ctx.raw.sub_wrappers}
    for prob in problems(netlist, plan(netlist)):
        ctx.report(prob.rule, rows[prob.wrapper]["Sub-wrapper"], prob.what)


USER_DIRECTIONS = ("input", "output")
_INPUT_ONLY = ("extra-input", "tie", "clock", "reset")


def check_user_ports(ctx: Context) -> None:
    """User_Ports rows: a known user-supplied fabric, a fresh port name, a connection legal
    for the direction, and (exposed) a top-level name nothing else uses."""
    pattern_cell = ctx.raw.wrapper.get("Signal naming pattern")
    pattern = (ctx.text(pattern_cell) if pattern_cell else None) or "{port}_{proto}_{signal}"
    domains = set(ctx.domains)
    inputs = set(extra_inputs(ctx))
    seen: dict[tuple[str, str], int] = {}
    contract: dict[str, set[str]] = {}
    for row in ctx.raw.user_ports:
        inst = ctx.text(row["Instance"], required=True)
        port = ctx.name(row["Port"])
        direction = ctx.enum(row["Direction"], USER_DIRECTIONS)
        width = ctx.integer(row["Width (bits)"], 1)
        conn_text = ctx.text(row["Connection"], required=True)
        ctx.text(row["Notes"])
        if inst is None:
            continue
        frow = ctx.defs.get(inst)
        template = ctx.text(frow["Backing template"]) if frow is not None else None
        manifest = ctx.registry.get(template) if template else None
        if ctx.kinds.get(inst) != "fabric" or manifest is None or not manifest.opaque:
            ctx.report("USERPORT-INSTANCE", row["Instance"],
                       f"{inst} is not a fabric with a user-supplied template (bag_external)")  # fmt: skip
            continue
        if port is None:
            continue
        if (inst, port) in seen:
            ctx.report("USERPORT-DUPLICATE", row["Port"],
                       f"{inst}.{port} is already declared on row {seen[(inst, port)]}")  # fmt: skip
            continue
        seen[(inst, port)] = row.row
        if inst not in contract:
            proto = ctx.fabric_protocol(inst)
            names = {"clk_i", "rst_ni"}
            if proto is not None:
                attached = [*ctx.fabric_masters.get(inst, []), *ctx.fabric_slaves.get(inst, [])]
                sigs = {*port_signals(proto, False, has_user=True),
                        *port_signals(proto, True, has_user=True)}  # fmt: skip
                names |= {flat_signal_name(pattern, f"{inst}_{p}", PROTO_TOKEN[proto], s, None)
                          for p in attached for s in sigs}  # fmt: skip
            contract[inst] = names
        if port in contract[inst]:
            ctx.report("USERPORT-NAME", row["Port"],
                       f"{port} is a bus or clock port BAG already connects on {inst}")  # fmt: skip
            continue
        if conn_text is None or direction is None:
            continue
        try:
            kind, target, tie = parse_connection(conn_text)
        except ValueError as exc:
            ctx.report("USERPORT-CONNECTION", row["Connection"], str(exc))
            continue
        if kind in _INPUT_ONLY and direction != "input":
            ctx.report("USERPORT-CONNECTION", row["Connection"],
                       f"{conn_text} drives the port, so {port} must be an input")  # fmt: skip
        elif kind == "open" and direction != "output":
            ctx.report("USERPORT-CONNECTION", row["Connection"],
                       f"open leaves the port unconnected, so {port} must be an output "
                       "(tie an unused input)")  # fmt: skip
        elif kind == "extra-input" and target not in inputs:
            ctx.report("USERPORT-CONNECTION", row["Connection"],
                       f"{target} is not on Wrapper > Extra top-level inputs")  # fmt: skip
        elif kind in ("clock", "reset") and target not in domains:
            ctx.report("USERPORT-CONNECTION", row["Connection"],
                       f"{target} is not a clock domain")  # fmt: skip
        elif kind in ("clock", "reset", "extra-input") and isinstance(width, int) and width != 1:
            ctx.report("USERPORT-CONNECTION", row["Width (bits)"],
                       f"a {kind.replace('-', ' ')} is 1 bit; {port} is {width} bits")  # fmt: skip
        elif kind == "tie" and isinstance(width, int) and tie is not None and tie >= 1 << width:
            ctx.report("USERPORT-CONNECTION", row["Connection"],
                       f"{conn_text} does not fit in {width} bits")  # fmt: skip
        elif kind == "expose":
            top = f"{inst}_{port}"
            owner = ctx.top_names.get(top)
            if owner is not None:
                ctx.report("WRAP-NAME-COLLISION", row["Port"],
                           f"top-level port {top} of User_Ports {inst}.{port} collides with "
                           f"{owner}")  # fmt: skip
            else:
                ctx.top_names[top] = f"User_Ports {row.where}"


def check_external_modules(ctx: Context) -> None:
    """Fabrics > External module (D45): a legal name, free of what BAG generates, on a
    user-supplied fabric; fabrics that name one module must have the same shape."""
    top = (
        ctx.text(ctx.raw.wrapper["Top module name"])
        if "Top module name" in ctx.raw.wrapper
        else None
    )
    package = (
        ctx.text(ctx.raw.wrapper["Package name"]) if "Package name" in ctx.raw.wrapper else None
    )
    generated = {top, package, *(ctx.text(r["Sub-wrapper"]) for r in ctx.raw.sub_wrappers)}
    shapes: dict[str, tuple[Row, tuple[object, ...]]] = {}
    for row in ctx.raw.fabrics:
        cell = row["External module"]
        module = ctx.text(cell)
        name = ctx.text(row["Fabric"])
        if module is None or name is None:
            continue
        template = ctx.text(row["Backing template"])
        manifest = ctx.registry.get(template) if template else None
        if manifest is None or not manifest.opaque:
            ctx.report("FAB-NA-SETTING", cell,
                       f"External module {module!r} does not apply to {template}; leave it blank")  # fmt: skip
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module):
            ctx.report(
                "EXT-MODULE-NAME",
                cell,
                f"External module {module!r} is not a SystemVerilog identifier",
            )
            continue
        if module in generated or module.startswith("bag_"):
            ctx.report(
                "EXT-MODULE-NAME",
                cell,
                f"External module {module!r} clashes with a module BAG generates",
            )
            continue
        users = sorted((ctx.text(u["Port"]), ctx.text(u["Direction"]), str(u["Width (bits)"].value))
                       for u in ctx.raw.user_ports if ctx.text(u["Instance"]) == name)  # fmt: skip
        shape = (
            ctx.text(row["Protocol"]), row["Data width (bits)"].value, row["Addr width (bits)"].value,
            sum(f == name for f in ctx.master_fabric.values()),
            sum(f == name for f in ctx.slave_fabric.values()),
            users,
        )  # fmt: skip
        if module not in shapes:
            shapes[module] = (row, shape)
        elif shapes[module][1] != shape:
            first = ctx.text(shapes[module][0]["Fabric"])
            ctx.report("EXT-MODULE-SHARED", cell,
                       f"{name} and {first} share module {module} but differ in protocol, widths, "
                       "attachments or user ports")  # fmt: skip
