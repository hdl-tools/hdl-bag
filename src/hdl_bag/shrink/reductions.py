# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The edits `bag shrink` tries, as pure functions over `SpecData`.

Edits work on workbook rows, not on the resolved IR, because adapters, address rules, decode
and placement are all derived by `resolve()`; editing the IR would mean keeping every one of
those invariants by hand. A candidate that fails `bag check` is simply discarded by the caller,
so the cascade below aims to make most candidates valid, not to be provably complete.

`KINDS` is the pass order, coarse to fine: fabric subtree, master, slave, sub-wrapper,
one row of a per-entity sheet, alias, wrapper scalars, equalise to the fabric, unused clocks and
resets, narrower widths, smaller memory regions.

- There is no bridge kind: a bridge cannot go without its downstream fabric (FAB-EMPTY-SIDE), so
  every such candidate is exactly the fabric subtree edit, which already removes the bridge.
- "Drop adapters" is not a row edit. Adapters are derived by `resolve()` and disappear through
  the equalise and narrow kinds. Placement rows that name an adapter are dropped by the row kind,
  deliberately before equalising and narrowing, so a stale adapter name cannot block a width change.
- Regions are rows of `memory_map`, so they go through the row kind.
- Protocol equalising is excluded on purpose: the `port_options` n/a sets are per protocol, so
  almost every such candidate would be invalid.
"""

import re
from collections.abc import Callable
from typing import Final

from hdl_bag.ir.model import _to_hex
from hdl_bag.reader.values import parse_hex
from hdl_bag.template.data import Cell, Rows, SpecData

Edit = tuple[str, Callable[[SpecData], None]]  # (label, mutate in place)
Kind = Callable[[SpecData], list[Edit]]  # the edits available on this data, in row order

DATA_WIDTHS: Final = (64, 32, 16, 8)
ADDR_WIDTHS: Final = (32, 24, 20, 16, 12)
MIN_REGION: Final = 0x1000
ROW_FIELDS: Final = (
    "placement", "user_ports", "access_rules", "memory_map", "port_options",
)  # fmt: skip
# Not here: dropping an exposed_ports row is always WRAP-PORT-NOT-LISTED and dropping an
# address_decode row always DEC-MISSING-ROW, so neither can yield a valid candidate. `drop_names`
# removes both along with their entity, and `_aliases` is the only useful decode edit.

# Sheets whose row is removed when column 0 (or, for the second group, column 0 or 1) is dropped.
_BY_FIRST: Final = ("masters", "slaves", "bridges", "fabrics", "memory_map", "address_decode",
                    "port_options", "user_ports", "exposed_ports")  # fmt: skip
_BY_EITHER: Final = ("topology", "access_rules", "placement")


def drop_names(data: SpecData, names: frozenset[str]) -> None:
    """Remove NAMES and every row that refers to them, in place.

    Names are unique across sheets (`NAME-UNIQUE`), so a name identifies one entity.
    """
    for sheet in _BY_FIRST:
        setattr(data, sheet, [r for r in getattr(data, sheet) if r[0] not in names])
    for sheet in _BY_EITHER:
        rows = [r for r in getattr(data, sheet) if r[0] not in names and r[1] not in names]
        setattr(data, sheet, rows)
    # The children of a dropped sub-wrapper move up to its parent (through dropped ancestors).
    parent = {str(r[0]): r[1] for r in data.sub_wrappers}
    kept: Rows = []
    for row in data.sub_wrappers:
        if row[0] in names:
            continue
        up = row[1]
        while up in names:
            up = parent[str(up)]
        kept.append([row[0], up, *row[2:]])
    data.sub_wrappers = kept
    # An adapter instance is named from its port (`{from}_{type}_2_{to}`, user-configurable), so a
    # placement row for it names the dropped entity only as a `_`-delimited segment. A false
    # positive (a master literally called `dw`) merely drops a placement row, which resets that
    # instance to its default home: the candidate stays check-clean, just slightly more reduced.
    segments = [re.compile(rf"(?:^|_){re.escape(n)}(?:_|$)") for n in names]
    data.placement = [r for r in data.placement
                      if not any(p.search(str(r[0])) for p in segments)]  # fmt: skip
    # NOTE: two gaps are deliberate. address_decode column 2 (the default slave) is left alone
    # even when that slave was just dropped: rewriting the miss mode needs a protocol-specific
    # error code, so `bag check` rejects the candidate instead. Adapter names are matched by the
    # segment heuristic above, not by parsing the naming pattern, which is fragile.
    _renumber_ports(data)


def _renumber_ports(data: SpecData) -> None:
    """Make the port indices dense (0..n-1) within each (fabric, side) group again.

    `TOPO-INDEX-OUT-OF-RANGE` fires when an index is >= the group's size, so without this
    nearly every dropped port would invalidate the candidate. Relative order is kept.
    Priorities are not touched: they sit on surviving rows only, so dropping rows cannot make two
    of them equal (`TOPO-PRIORITY-REPEATED`).
    """
    groups: dict[tuple[str, str], list[list[Cell]]] = {}
    fabrics = {r[0] for r in data.fabrics}
    for row in data.topology:
        if not isinstance(row[2], int):
            continue  # left for bag check to report
        key = (str(row[0]), "slave") if row[0] in fabrics else (str(row[1]), "master")
        groups.setdefault(key, []).append(row)
    for rows in groups.values():
        for new, row in enumerate(sorted(rows, key=lambda r: int(r[2] or 0))):  # stable
            row[2] = new


def fabric_ports(data: SpecData, fabric: str) -> list[str]:
    """Names of the masters, slaves and bridges linked to FABRIC, in topology row order."""
    fabrics = {r[0] for r in data.fabrics}
    ports: list[str] = []
    for row in data.topology:
        if row[0] == fabric and row[1] not in fabrics:  # slave row: [fabric, slave, ...]
            port = row[1]
        elif row[1] == fabric and row[0] not in fabrics:  # master row: [master, fabric, ...]
            port = row[0]
        else:
            continue
        if port not in ports:
            ports.append(str(port))
    return ports


def _drop(label: str, names: frozenset[str]) -> Edit:
    return (label, lambda data: drop_names(data, names))


def _fabric_subtrees(data: SpecData) -> list[Edit]:
    return [
        _drop(f"drop fabric {name} and its ports", frozenset({name, *fabric_ports(data, name)}))
        for name in (str(r[0]) for r in data.fabrics)
    ]


def _entities(sheet: str, noun: str) -> Kind:
    def kind(data: SpecData) -> list[Edit]:
        return [_drop(f"drop {noun} {r[0]}", frozenset({str(r[0])})) for r in getattr(data, sheet)]

    return kind


def _rows(data: SpecData) -> list[Edit]:
    edits: list[Edit] = []
    for sheet in ROW_FIELDS:
        for i, row in enumerate(getattr(data, sheet)):
            edits.append((f"drop {sheet} row {i} ({row[0]})", _row_dropper(sheet, i)))
    return edits


def _row_dropper(sheet: str, i: int) -> Callable[[SpecData], None]:
    return lambda data: getattr(data, sheet).pop(i)


def _aliases(data: SpecData) -> list[Edit]:
    def clear(i: int) -> Callable[[SpecData], None]:
        def apply(d: SpecData) -> None:
            d.address_decode[i][3:7] = [None] * 4

        return apply

    return [(f"clear alias of {r[0]}", clear(i)) for i, r in enumerate(data.address_decode)
            if r[3] is not None]  # fmt: skip


def _wrapper_scalars(data: SpecData) -> list[Edit]:
    def clear(key: str) -> Callable[[SpecData], None]:
        return lambda d: d.wrapper.__setitem__(key, None)

    return [(f"clear {key}", clear(key)) for key in ("File header", "Extra top-level inputs")
            if data.wrapper.get(key) is not None]  # fmt: skip


def _fabric_of(data: SpecData, name: str, sheet: str) -> list[Cell] | None:
    """The fabrics row that the master or slave NAME hangs off, if linked."""
    by_name = {r[0]: r for r in data.fabrics}
    for row in data.topology:
        end, fab = (row[0], row[1]) if sheet == "masters" else (row[1], row[0])  # asymmetric
        if end == name and fab in by_name:
            return by_name[fab]
    return None


def _equalise(data: SpecData) -> list[Edit]:
    edits: list[Edit] = []
    for sheet, noun in (("masters", "master"), ("slaves", "slave")):
        for i, row in enumerate(getattr(data, sheet)):
            fab = _fabric_of(data, str(row[0]), sheet)
            if fab is None:
                continue
            for col, what, fcol in ((2, "data width", 3), (6, "domain", 5)):
                if row[col] != fab[fcol]:
                    edits.append((f"set {what} of {noun} {row[0]} to {fab[fcol]}",
                                  _setter(sheet, i, col, fab[fcol])))  # fmt: skip
    return edits


def _setter(sheet: str, i: int, col: int, value: Cell) -> Callable[[SpecData], None]:
    def apply(data: SpecData) -> None:
        getattr(data, sheet)[i][col] = value

    return apply


def _unused_clock_reset(data: SpecData) -> list[Edit]:
    used = {r[6] for r in (*data.masters, *data.slaves)} | {r[5] for r in data.fabrics}
    edits: list[Edit] = []
    for sheet, noun, col in (("clocks", "clock", 3), ("resets", "reset", 4)):
        for i, row in enumerate(getattr(data, sheet)):
            if row[col] not in used:
                edits.append((f"drop unused {noun} {row[0]}", _row_dropper(sheet, i)))
    return edits


def _step_down(value: Cell, ladder: tuple[int, ...]) -> int | None:
    """The first ladder rung below VALUE, or None if VALUE is not an int above the lowest."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return next((rung for rung in ladder if rung < value), None)


def _narrow(data: SpecData) -> list[Edit]:
    edits: list[Edit] = []
    cells = (("masters", "master", 2, "data width", DATA_WIDTHS),
             ("slaves", "slave", 2, "data width", DATA_WIDTHS),
             ("fabrics", "fabric", 3, "data width", DATA_WIDTHS),
             ("masters", "master", 3, "address width", ADDR_WIDTHS),
             ("slaves", "slave", 3, "address width", ADDR_WIDTHS),
             ("fabrics", "fabric", 4, "address width", ADDR_WIDTHS))  # fmt: skip
    for sheet, _noun, col, what, ladder in cells:
        for i, row in enumerate(getattr(data, sheet)):
            to = _step_down(row[col], ladder)
            if to is not None:
                edits.append((f"narrow {what} of {row[0]} to {to}", _setter(sheet, i, col, to)))
    for i, row in enumerate(data.masters):
        for col, what in ((4, "id width"), (7, "max reads"), (8, "max writes")):
            cell = row[col]
            if isinstance(cell, int) and not isinstance(cell, bool) and cell > 1:
                edits.append((f"narrow {what} of {row[0]} to 1", _setter("masters", i, col, 1)))
    return edits


def _shrink_maps(data: SpecData) -> list[Edit]:
    edits: list[Edit] = []
    for i, row in enumerate(data.memory_map):
        size = parse_hex(row[3]) if isinstance(row[3], str) else row[3]
        if not isinstance(size, int) or size <= 1:
            continue
        to = MIN_REGION if size > MIN_REGION else size // 2
        edits.append((f"shrink region {row[1]} of {row[0]} to {_to_hex(to)}",
                      _setter("memory_map", i, 3, _to_hex(to))))  # fmt: skip
    return edits


KINDS: Final[tuple[Kind, ...]] = (
    _fabric_subtrees,
    _entities("masters", "master"),
    _entities("slaves", "slave"),
    _entities("sub_wrappers", "sub-wrapper"),
    _rows,
    _aliases,
    _wrapper_scalars,
    _equalise,
    _unused_clock_reset,
    _narrow,
    _shrink_maps,
)
