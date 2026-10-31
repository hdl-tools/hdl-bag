# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Hierarchy (sub-wrappers, placement) and Wrapper (exposed ports, name collisions)."""

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
    owners: dict[str, str] = {}  # top-level port name -> owner

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
    """Sub-wrapper boundary port names must be unique within each sub-wrapper.

    The names come from the generator's routing plan (pure; no RTL is generated), so
    `bag check` reports a collision before anything is written.
    """
    from hdl_bag.gen.hierarchy import collisions, plan
    from hdl_bag.gen.netlist import build_netlist
    from hdl_bag.ir.resolve import resolve

    netlist = build_netlist(resolve(result))
    rows = {ctx.text(r["Sub-wrapper"]): r for r in ctx.raw.sub_wrappers}
    for w, port, owners in collisions(netlist, plan(netlist)):
        ctx.report("WRAP-NAME-COLLISION", rows[w]["Sub-wrapper"],
                   f"boundary port {port} of sub-wrapper {w} is claimed by {' and '.join(owners)}")  # fmt: skip
