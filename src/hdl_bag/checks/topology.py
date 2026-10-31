# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Topology: links, attachment, port indices, priorities, connectivity, bridge cycles."""

from collections import defaultdict
from typing import Literal

from hdl_bag.checks.context import Context, Link
from hdl_bag.reader.model import Cell
from hdl_bag.reader.values import parse_int

_LEGAL = {("master", "fabric"), ("fabric", "slave"), ("fabric", "bridge"), ("bridge", "fabric")}


def _index(ctx: Context, cell: Cell, rule: str) -> int | None:
    try:
        return parse_int(cell.value, minimum=0)
    except ValueError:
        ctx.report(rule, cell, f"{cell.column} {cell.value!r} is not an integer >= 0")
        return None


def check_topology(ctx: Context) -> None:
    pairs: dict[tuple[str, str], int] = {}
    attached: dict[tuple[str, str], int] = {}  # (end, role) -> row; role source|destination
    for row in ctx.raw.topology:
        src = ctx.text(row["Source"], required=True)
        dst = ctx.text(row["Destination"], required=True)
        if src is None or dst is None:
            continue
        unknown = [n for n in (src, dst) if n not in ctx.kinds]
        if unknown:
            for n in unknown:
                ctx.report(
                    "TOPO-UNKNOWN-NAME",
                    row,
                    f"{n} is not defined on Masters, Slaves, Bridges or Fabrics",
                )
            continue
        sk, dk = ctx.kinds[src], ctx.kinds[dst]
        if (sk, dk) not in _LEGAL:
            ctx.report(
                "TOPO-ILLEGAL-LINK", row,
                f"{src} ({sk}) -> {dst} ({dk}) is not master -> fabric, fabric -> slave, "
                "fabric -> bridge or bridge -> fabric",
            )  # fmt: skip
            continue
        if (src, dst) in pairs:
            ctx.report("TOPO-DUPLICATE", row, f"link {src} -> {dst} repeats row {pairs[src, dst]}")
            continue
        pairs[src, dst] = row.row
        end, role = (dst, "destination") if sk == "fabric" else (src, "source")
        if (end, role) in attached:
            ctx.report(
                "TOPO-MULTI-ATTACH", row, f"{end} is already attached on row {attached[end, role]}"
            )
            continue
        attached[end, role] = row.row

        fabric = src if sk == "fabric" else dst
        side: Literal["master", "slave"] = "slave" if sk == "fabric" else "master"
        idx_cell, pri_cell = row["Port index"], row["Priority"]
        index = None
        if idx_cell.value is None:
            ctx.report("TOPO-INDEX-MISSING", idx_cell, f"link {src} -> {dst} has no Port index")
        else:
            index = _index(ctx, idx_cell, "TOPO-INDEX-NOT-INTEGER")
        priority = (
            None if pri_cell.value is None else _index(ctx, pri_cell, "TOPO-PRIORITY-NOT-INTEGER")
        )
        ctx.links.append(Link(row, src, dst, fabric, side, end, index, priority))
        if side == "master":
            ctx.master_fabric[end] = fabric
            ctx.fabric_masters.setdefault(fabric, []).append(end)
        else:
            ctx.slave_fabric[end] = fabric
            ctx.fabric_slaves.setdefault(fabric, []).append(end)

    _check_indices(ctx)
    _check_priorities(ctx)
    _check_connected(ctx)
    _check_bridge_cycles(ctx)


def _check_indices(ctx: Context) -> None:
    groups: dict[tuple[str, str], list[Link]] = defaultdict(list)
    for link in ctx.links:
        groups[link.fabric, link.side].append(link)
    for (fabric, side), links in groups.items():
        seen: dict[int, int] = {}
        for link in links:
            if link.index is None:
                continue
            cell = link.row["Port index"]
            if link.index >= len(links):
                ctx.report("TOPO-INDEX-OUT-OF-RANGE", cell,
                           f"Port index {link.index} is outside 0..{len(links) - 1} "
                           f"for the {side} ports of {fabric}")  # fmt: skip
            elif link.index in seen:
                ctx.report("TOPO-INDEX-REPEATED", cell,
                           f"Port index {link.index} repeats row {seen[link.index]} "
                           f"on the {side} ports of {fabric}")  # fmt: skip
            else:
                seen[link.index] = link.row.row


def _check_priorities(ctx: Context) -> None:
    seen: dict[tuple[str, int], int] = {}
    for link in ctx.links:
        row = ctx.fabric_row(link.fabric)
        if link.side != "master" or row is None:
            continue
        arb = ctx.enum(row["Arbitration"], ["round-robin", "fixed-priority", "single-master"])
        if arb != "fixed-priority":
            continue
        cell = link.row["Priority"]
        if cell.value is None:
            ctx.report("TOPO-PRIORITY-MISSING", cell,
                       f"master port {link.end} on fixed-priority fabric {link.fabric} has no Priority")  # fmt: skip
        elif link.priority is not None:
            key = (link.fabric, link.priority)
            if key in seen:
                ctx.report("TOPO-PRIORITY-REPEATED", cell,
                           f"Priority {link.priority} repeats row {seen[key]} on {link.fabric}")  # fmt: skip
            else:
                seen[key] = link.row.row


def _check_connected(ctx: Context) -> None:
    kinds: tuple[tuple[Literal["master", "slave"], str], ...] = (
        ("master", "Master"),
        ("slave", "Slave"),
    )
    for kind, column in kinds:
        attach = ctx.master_fabric if kind == "master" else ctx.slave_fabric
        for row in ctx.rows(kind):
            name = ctx.text(row[column])
            if name and ctx.defs.get(name) is row and name not in attach:
                ctx.report("TOPO-NOT-CONNECTED", row[column], f"{kind} {name} has no Topology link")
    for row in ctx.raw.bridges:
        name = ctx.text(row["Bridge"])
        if not name or ctx.defs.get(name) is not row:
            continue
        if name not in ctx.slave_fabric:
            ctx.report(
                "TOPO-NOT-CONNECTED",
                row["Bridge"],
                f"bridge {name} has no upstream link (fabric -> {name})",
            )
        if name not in ctx.master_fabric:
            ctx.report(
                "TOPO-NOT-CONNECTED",
                row["Bridge"],
                f"bridge {name} has no downstream link ({name} -> fabric)",
            )
    for row in ctx.raw.fabrics:
        name = ctx.text(row["Fabric"])
        if not name or ctx.defs.get(name) is not row:
            continue
        for side, ports in (("master", ctx.fabric_masters), ("slave", ctx.fabric_slaves)):
            if not ports.get(name):
                ctx.report(
                    "FAB-EMPTY-SIDE",
                    row["Fabric"],
                    f"fabric {name} has no {side} ports in Topology",
                )


def _check_bridge_cycles(ctx: Context) -> None:
    edges: dict[str, list[tuple[str, str, int]]] = defaultdict(list)  # up -> (down, bridge, row)
    for bridge, up in ctx.slave_fabric.items():
        if ctx.kinds.get(bridge) == "bridge" and bridge in ctx.master_fabric:
            row = next(lk.row.row for lk in ctx.links if lk.end == bridge and lk.side == "master")
            edges[up].append((ctx.master_fabric[bridge], bridge, row))
    reported: set[frozenset[str]] = set()
    for start in sorted(edges):
        # Depth-first search for a path back to start.
        stack: list[tuple[str, list[tuple[str, str, int]]]] = [(start, [])]
        while stack:
            node, path = stack.pop()
            for down, bridge, row in sorted(edges.get(node, [])):
                step = [*path, (down, bridge, row)]
                if down == start:
                    key = frozenset(b for _, b, _ in step)
                    if key not in reported:
                        reported.add(key)
                        text = start + "".join(f" -> {b} -> {d}" for d, b, _ in step)
                        last = min(step, key=lambda s: s[2])
                        cell = next(lk.row for lk in ctx.links if lk.row.row == last[2])
                        ctx.report(
                            "TOPO-BRIDGE-CYCLE", cell, f"bridges link fabrics in a loop: {text}"
                        )
                elif all(down != d for d, _, _ in path) and down != start:
                    stack.append((down, step))
