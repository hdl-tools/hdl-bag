# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Fabrics, masters, slaves, bridges and Port_Options."""

from typing import TYPE_CHECKING, Final, Literal

from hdl_bag.checks.basics import check_domain_ref
from hdl_bag.checks.context import Context
from hdl_bag.protocols import (
    APB_FAMILY,
    AXI_FAMILY,
    DATA_WIDTHS,
    ERROR_RESPONSES,
    FABRIC_ADDR_WIDTH,
    MAX_SLAVE_ID_WIDTH_WARN,
    OPTIONAL_SIGNALS,
    PORT_ADDR_WIDTH,
    Protocol,
    signal_applies,
)
from hdl_bag.reader.model import Cell, Row

if TYPE_CHECKING:
    from hdl_bag.checks import CheckResult

TOPOLOGIES: Final = ("crossbar", "shared-bus", "single-layer", "decoder-mux")
ARBITRATION: Final = ("round-robin", "fixed-priority", "single-master")
LATENCY: Final = ("NO_LATENCY", "CUT_SLV_AX", "CUT_MST_AX", "CUT_ALL_AX",
                  "CUT_SLV_PORTS", "CUT_MST_PORTS", "CUT_ALL_PORTS")  # fmt: skip
REG_SLICE: Final = ("none", "cut", "multicut-2", "multicut-3", "n/a")
SIG_DRIVE: Final = ("drive", "tie-0", "tie-default", "n/a")
# Topologies that suit each protocol (guide, Fabrics > Topology)
PROTOCOL_TOPOLOGIES: Final = {
    Protocol.AXI4: ("crossbar", "shared-bus"),
    Protocol.AXI4_LITE: ("crossbar", "shared-bus"),
    Protocol.AHB_LITE: ("single-layer",),
    Protocol.APB4: ("decoder-mux",),
    Protocol.APB3: ("decoder-mux",),
}


def _data_width(ctx: Context, cell: Cell, protocol: Protocol | None) -> int | None:
    width = ctx.integer(cell, minimum=1)
    if isinstance(width, int) and protocol and width not in DATA_WIDTHS[protocol]:
        legal = ", ".join(str(w) for w in sorted(DATA_WIDTHS[protocol]))
        ctx.report("WIDTH-DATA", cell, f"data width {width} is not legal for {protocol} ({legal})")
        return None
    return width if isinstance(width, int) else None


def _na_or_value(ctx: Context, cell: Cell, applies: bool, why: str) -> bool:
    """For a setting that does not apply: warn if it holds a value. Returns ``applies``."""
    if not applies and cell.value not in (None, "n/a"):
        ctx.report(
            "FAB-NA-SETTING", cell, f"{cell.column} {cell.value!r} does not apply {why}; set n/a"
        )
    return applies


def check_fabrics(ctx: Context) -> None:
    for row in ctx.raw.fabrics:
        name = ctx.text(row["Fabric"])
        protocol = ctx.protocol(row["Protocol"])
        topology = ctx.enum(row["Topology"], TOPOLOGIES)
        _data_width(ctx, row["Data width (bits)"], protocol)
        addr = ctx.integer(row["Addr width (bits)"], minimum=1)
        if isinstance(addr, int) and addr not in FABRIC_ADDR_WIDTH:
            ctx.report(
                "WIDTH-ADDR",
                row["Addr width (bits)"],
                f"fabric address width {addr} is outside 12..64",
            )
        check_domain_ref(ctx, row["Clock domain"])
        arb = ctx.enum(row["Arbitration"], ARBITRATION)
        template = ctx.text(row["Backing template"], required=True)
        ctx.text(row["Backing IP"])

        if protocol and topology and topology not in PROTOCOL_TOPOLOGIES[protocol]:
            ctx.report("FAB-TOPOLOGY", row["Topology"],
                       f"topology {topology} does not suit {protocol} "
                       f"({' or '.join(PROTOCOL_TOPOLOGIES[protocol])})")  # fmt: skip
        if template:
            m = ctx.registry.get(template)
            if m is None or m.kind != "fabric":
                ctx.report(
                    "TMPL-FABRIC", row["Backing template"], f"{template} is not a fabric template"
                )
            else:
                if protocol and protocol not in m.protocols:
                    ctx.report(
                        "TMPL-FABRIC",
                        row["Backing template"],
                        f"{template} does not support {protocol}",
                    )
                if topology and topology not in m.topologies:
                    ctx.report(
                        "TMPL-FABRIC",
                        row["Backing template"],
                        f"{template} does not implement {topology}",
                    )
                if arb and arb not in m.arbitration:
                    ctx.report("FAB-ARBITRATION", row["Arbitration"],
                               f"{arb} is not supported by {template} ({', '.join(m.arbitration)})")  # fmt: skip
        n_masters = len(ctx.fabric_masters.get(name or "", []))
        if name and arb == "single-master" and n_masters > 1:
            ctx.report("FAB-SINGLE-MASTER", row["Arbitration"],
                       f"{name} has {n_masters} master ports but Arbitration is single-master")  # fmt: skip
        elif name and protocol and protocol.name.startswith("APB") and n_masters > 1:
            ctx.report(
                "FAB-SINGLE-MASTER",
                row["Fabric"],
                f"APB fabric {name} has {n_masters} master ports; APB allows one",
            )

        crossbar = topology == "crossbar"
        shared = topology == "shared-bus"
        axi = protocol in AXI_FAMILY
        if _na_or_value(ctx, row["Latency mode"], crossbar, "outside a crossbar"):
            ctx.enum(row["Latency mode"], LATENCY)
        for col in ("Max mst trans", "Max slv trans"):
            if _na_or_value(ctx, row[col], axi, "to a non-AXI fabric"):
                ctx.integer(row[col], minimum=1)
        if _na_or_value(ctx, row["Mid-point FIFO depth"], shared, "outside a shared bus"):
            ctx.integer(row["Mid-point FIFO depth"], minimum=0)
        if _na_or_value(ctx, row["Mid-point register slice"], shared, "outside a shared bus"):
            ctx.enum(row["Mid-point register slice"], REG_SLICE[:-1])

        if name and (ids := ctx.fabric_id_widths(name)) and ids[1] > MAX_SLAVE_ID_WIDTH_WARN:
            ctx.report("FAB-ID-GROWTH", row["Fabric"],
                       f"slave-side ID width of {name} is {ids[1]} bits (> {MAX_SLAVE_ID_WIDTH_WARN})")  # fmt: skip


def _check_port_row(ctx: Context, row: Row, kind: Literal["master", "slave"]) -> None:
    column = "Master" if kind == "master" else "Slave"
    name = ctx.text(row[column])
    protocol = ctx.protocol(row["Protocol"])
    width = _data_width(ctx, row["Data width (bits)"], protocol)
    addr = ctx.integer(row["Addr width (bits)"], minimum=1)
    domain = check_domain_ref(ctx, row["Clock domain"])
    attach = ctx.master_fabric if kind == "master" else ctx.slave_fabric
    fabric = attach.get(name or "") if name and ctx.defs.get(name) is row else None
    frow = ctx.fabric_row(fabric) if fabric else None

    if isinstance(addr, int):
        f_addr = ctx.integer(frow["Addr width (bits)"], minimum=1) if frow else None
        if addr not in PORT_ADDR_WIDTH:
            ctx.report(
                "WIDTH-ADDR", row["Addr width (bits)"], f"address width {addr} is outside 1..64"
            )
        elif isinstance(f_addr, int) and addr > f_addr:
            ctx.report("WIDTH-ADDR", row["Addr width (bits)"],
                       f"address width {addr} is wider than fabric {fabric} ({f_addr})")  # fmt: skip

    id_cell = row["ID width (bits)"]
    if protocol == Protocol.AXI4:
        ctx.integer(id_cell, minimum=1, tokens=("auto",) if kind == "slave" else ())
    elif protocol and id_cell.value not in (None, "n/a"):
        ctx.report(
            "WIDTH-ID", id_cell, f"ID width applies to AXI4 only; leave it blank for {protocol}"
        )
    user_cell = row["User width (bits)"]
    user = ctx.integer(user_cell, minimum=0, required=False, tokens=("n/a",))
    if isinstance(user, int) and user > 0 and protocol and protocol != Protocol.AXI4:
        ctx.report(
            "WIDTH-USER",
            user_cell,
            f"User width {user} applies to AXI4 only; use 0 or blank for {protocol}",
        )

    if kind == "master":
        for col in ("Max outstanding rd", "Max outstanding wr"):
            ctx.integer(row[col], minimum=1, required=protocol in AXI_FAMILY)
    else:
        resp = ctx.enum(row["Error response"], ["SLVERR", "DECERR", "HRESP_ERROR", "PSLVERR"])
        if resp and protocol and resp not in ERROR_RESPONSES[protocol]:
            ctx.report("VALUE-ILLEGAL", row["Error response"],
                       f"Error response {resp} does not suit {protocol} "
                       f"({', '.join(sorted(ERROR_RESPONSES[protocol]))})")  # fmt: skip
        ctx.integer(row["Wait states"], minimum=0, required=False, tokens=("variable",))
    for col in ("Description", "Notes"):
        ctx.text(row[col])

    # Adapters exist only inside the AXI family (D24).
    if frow is None or protocol is None:
        return
    f_proto = ctx.fabric_protocol(fabric or "")
    if f_proto is None or (protocol in AXI_FAMILY and f_proto in AXI_FAMILY):
        return
    f_width = ctx.integer(frow["Data width (bits)"], minimum=1)
    f_domain = ctx.text(frow["Clock domain"])
    diffs = [
        what for what, a, b in (
            ("protocol", protocol, f_proto), ("data width", width, f_width), ("clock domain", domain, f_domain),
        )
        if a is not None and b is not None and a != b
    ]  # fmt: skip
    if diffs:
        ctx.report("PORT-ADAPTER", row[column],
                   f"{name} must match the {', '.join(diffs)} of fabric {fabric}: "
                   "adapters exist only between AXI4/AXI4-Lite ports and fabrics")  # fmt: skip


def check_ports(ctx: Context) -> None:
    for row in ctx.raw.masters:
        _check_port_row(ctx, row, "master")
    for row in ctx.raw.slaves:
        _check_port_row(ctx, row, "slave")


def check_bridges(ctx: Context) -> None:
    for row in ctx.raw.bridges:
        name = ctx.text(row["Bridge"])
        template = ctx.text(row["Template"], required=True)
        ctx.hexval(row["Window base (hex)"])
        ctx.yes_no(row["Pass-through address"])
        depth = ctx.integer(row["FIFO depth"], minimum=0, required=False)
        ctx.text(row["Notes"])
        if not template:
            continue
        m = ctx.registry.get(template)
        if m is None or m.kind != "bridge":
            ctx.report("TMPL-BRIDGE", row["Template"], f"{template} is not a bridge template")
            continue
        if not m.fifo_depth and isinstance(depth, int) and depth > 0:
            ctx.report("BRIDGE-FIFO-IGNORED", row["FIFO depth"],
                       f"{template} handles one transfer at a time and ignores FIFO depth {depth}")  # fmt: skip
        if not name or ctx.defs.get(name) is not row:
            continue
        for side, allowed, attach in (
            ("upstream", m.upstream, ctx.slave_fabric), ("downstream", m.downstream, ctx.master_fabric),
        ):  # fmt: skip
            fabric = attach.get(name)
            proto = ctx.fabric_protocol(fabric) if fabric else None
            if proto and proto not in allowed:
                ctx.report("TMPL-BRIDGE", row["Template"],
                           f"{template} needs a {' or '.join(allowed)} {side} fabric; "
                           f"{fabric} is {proto}")  # fmt: skip


TIED = ("tie-0", "tie-default")


def check_port_options(ctx: Context) -> None:
    seen: dict[str, int] = {}
    for row in ctx.raw.port_options:
        port = ctx.text(row["Port"], required=True)
        depth = ctx.integer(row["FIFO depth"], 0, required=False)
        sl = ctx.enum(row["Register slice"], REG_SLICE, required=False)
        signals = {col: ctx.enum(row[col], SIG_DRIVE, required=False) for col in OPTIONAL_SIGNALS}
        ctx.text(row["Notes"])
        if port is None:
            continue
        if port in seen:
            ctx.report(
                "PORTOPT-DUPLICATE", row["Port"], f"{port} is already listed on row {seen[port]}"
            )
            continue
        seen[port] = row.row
        kind = ctx.kinds.get(port)
        # A bridge's options apply to its downstream (master) end, as the workbook derives.
        side: Literal["master", "slave"] = "master" if kind in ("master", "bridge") else "slave"
        attach = ctx.master_fabric if side == "master" else ctx.slave_fabric
        if kind not in ("master", "slave", "bridge") or port not in attach:
            ctx.report(
                "PORTOPT-UNKNOWN-PORT",
                row["Port"],
                f"{port} is not a connected master, slave or bridge",
            )
            continue
        protocol = ctx.port_protocol(port, side)
        if protocol is None:
            continue
        for col, value in signals.items():
            has = signal_applies(col, protocol, is_slave=side == "slave")
            if value is None:
                continue
            if has and value == "n/a":
                ctx.report(
                    "PORTOPT-SIGNAL",
                    row[col],
                    f"{col} exists on {protocol}; use drive, tie-0 or tie-default",
                )
            elif not has and value != "n/a":
                ctx.report(
                    "PORTOPT-SIGNAL", row[col], f"{col} does not exist on {protocol}; use n/a"
                )
        # A tied PSLVERR on the master side of an APB fabric hides every error from it,
        # its own miss response included: the master sees OKAY with no slave behind it.
        if side == "master" and protocol in APB_FAMILY and signals["PSLVERR"] in TIED:
            ctx.report("PORTOPT-ERROR-MASKED", row["PSLVERR"],
                       f"PSLVERR is tied on {port}: errors from {attach[port]}, its miss response "
                       "included, never reach it")  # fmt: skip
        buffered = (isinstance(depth, int) and depth > 0) or sl in (
            "cut",
            "multicut-2",
            "multicut-3",
        )
        if buffered and protocol not in AXI_FAMILY:
            ctx.report("PORT-ADAPTER", row["Port"],
                       f"FIFO and register slices exist only on AXI4/AXI4-Lite ports; {port} is {protocol}")  # fmt: skip


def check_wrap_refusals(ctx: Context, result: "CheckResult") -> None:
    """An AXI4 master whose WRAP bursts a converter on the way answers with SLVERR (D49): the
    PULP width converters and AXI4-to-Lite conversion cannot split them (hdl_bag.bursts).
    One warning per master and converter, on the master's row."""
    from hdl_bag.bursts import wrap_refusals
    from hdl_bag.ir.resolve import resolve

    spec = resolve(result)
    kinds = {a.name: f"{a.type} adapter" for a in spec.adapters}
    kinds |= {b.name: f"bridge ({b.template})" for b in spec.bridges}
    for (master, converter), targets in sorted(wrap_refusals(spec).items()):
        row = ctx.defs[master]
        ctx.report(
            "AXI-WRAP-REFUSED",
            row,
            f"WRAP bursts from {master} to {', '.join(targets)} get SLVERR: {kinds[converter]} "
            f"{converter} cannot convert them. Send INCR bursts on this path, or keep it at "
            "one width and protocol",
        )
