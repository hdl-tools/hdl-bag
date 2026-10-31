# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Read a spec workbook into a RawSpec.

Input columns are found by header name, never by position, so users may reorder or
insert columns. Derived (grey) columns are ignored: BAG recomputes everything.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from hdl_bag.reader.model import Cell, RawSpec, RawValue, Row

HDR_ROW: Final = 4


class ReaderError(Exception):
    """The workbook does not have the shape of the spec schema."""


@dataclass(frozen=True)
class Table:
    sheet: str
    headers: tuple[str, ...]  # input columns; the first one keys the row
    stop_labels: tuple[str, ...] = ()  # column-A section labels that end the table


# Input columns of every table (schema 0.3). tests/reader keeps these equal to the
# builder's input columns.
CLOCKS = Table("Clocks", ("Clock", "Freq (MHz)", "Source", "Domain", "Reset", "Notes"))
RESETS = Table(
    "Resets",
    ("Reset", "Polarity", "Assertion", "Deassertion", "Domain", "Source", "Notes"),
)
FABRICS = Table(
    "Fabrics",
    (
        "Fabric", "Protocol", "Topology", "Data width (bits)", "Addr width (bits)",
        "Clock domain", "Arbitration", "Latency mode", "Max mst trans", "Max slv trans",
        "Backing template", "Backing IP", "Mid-point FIFO depth", "Mid-point register slice",
        "Notes",
    ),
)  # fmt: skip
MASTERS = Table(
    "Masters",
    (
        "Master", "Protocol", "Data width (bits)", "Addr width (bits)", "ID width (bits)",
        "User width (bits)", "Clock domain", "Max outstanding rd", "Max outstanding wr",
        "Description", "Notes",
    ),
)  # fmt: skip
SLAVES = Table(
    "Slaves",
    (
        "Slave", "Protocol", "Data width (bits)", "Addr width (bits)", "ID width (bits)",
        "User width (bits)", "Clock domain", "Error response", "Wait states",
        "Description", "Notes",
    ),
)  # fmt: skip
BRIDGES = Table(
    "Bridges",
    ("Bridge", "Template", "Window base (hex)", "Pass-through address", "FIFO depth", "Notes"),
)
TOPOLOGY = Table("Topology", ("Source", "Destination", "Port index", "Priority", "Notes"))
ACCESS_RULES = Table("Access_Rules", ("Master", "Slave / bridge", "Access", "Notes"))
MEMORY_MAP = Table(
    "Memory_Map",
    ("Slave / bridge", "Region", "Base (hex)", "Size (hex)", "Access", "Secure only", "Notes"),
)
ADDRESS_DECODE = Table(
    "Address_Decode",
    (
        "Master", "Miss response", "Default slave", "Alias base (hex)", "Alias size (hex)",
        "Alias maps to (hex)", "Alias enable", "Notes",
    ),
)  # fmt: skip
PORT_OPTIONS = Table(
    "Port_Options",
    (
        "Port", "FIFO depth", "Register slice", "PROT", "QOS", "REGION", "CACHE",
        "LOCK / excl", "PSTRB", "PSLVERR", "Notes",
    ),
)  # fmt: skip
SUB_WRAPPERS = Table("Hierarchy", ("Sub-wrapper", "Parent", "Notes"), ("Placement",))
PLACEMENT = Table("Hierarchy", ("Instance", "Sub-wrapper", "RTL instance name", "Notes"))
EXPOSED_PORTS = Table(
    "Wrapper", ("Port", "Exposed at top", "Top-level prefix", "If not exposed", "Notes")
)
KV_HEADERS: Final = ("Setting", "Value", "Notes")

TABLES: Final = (
    CLOCKS, RESETS, FABRICS, MASTERS, SLAVES, BRIDGES, TOPOLOGY, ACCESS_RULES, MEMORY_MAP,
    ADDRESS_DECODE, PORT_OPTIONS, SUB_WRAPPERS, PLACEMENT, EXPOSED_PORTS,
)  # fmt: skip


def _norm(value: object) -> RawValue:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)  # dates and other exotic cell types: keep as text for the checks


def _sheet(wb: openpyxl.Workbook, name: str) -> Worksheet:
    if name not in wb.sheetnames:
        raise ReaderError(f"{name}: sheet missing (READ-SHEET)")
    ws: Worksheet = wb[name]
    return ws


def _find_header(
    ws: Worksheet, headers: tuple[str, ...], first_row: int
) -> tuple[int, dict[str, int]]:
    """Locate the header row (at or below ``first_row``) holding ``headers[0]`` in any column."""
    for r in range(first_row, ws.max_row + 1):
        names = {
            _norm(ws.cell(r, c).value): c for c in range(ws.max_column, 0, -1)
        }  # reversed so the first occurrence wins
        if headers[0] in names:
            seen = [_norm(ws.cell(r, c).value) for c in range(1, ws.max_column + 1)]
            for h in headers:
                if h not in names:
                    raise ReaderError(f"{ws.title}: missing column '{h}' in row {r} (READ-COLUMN)")
                if seen.count(h) > 1:
                    raise ReaderError(
                        f"{ws.title}: duplicate column '{h}' in row {r} (READ-COLUMN)"
                    )
            return r, {h: names[h] for h in headers}
    raise ReaderError(f"{ws.title}: header row with '{headers[0]}' not found (READ-HEADER)")


def _read_table(wb: openpyxl.Workbook, table: Table, first_row: int = HDR_ROW) -> tuple[Row, ...]:
    ws = _sheet(wb, table.sheet)
    hdr, cols = _find_header(ws, table.headers, first_row)
    rows = []
    for r in range(hdr + 1, ws.max_row + 1):
        if _norm(ws.cell(r, 1).value) in table.stop_labels:
            break
        cells = {
            h: Cell(sheet=ws.title, row=r, column=h, value=_norm(ws.cell(r, c).value))
            for h, c in cols.items()
        }
        if any(cell.value is not None for cell in cells.values()):
            rows.append(Row(sheet=ws.title, row=r, cells=cells))
    return tuple(rows)


def _read_kv(wb: openpyxl.Workbook, sheet: str) -> tuple[dict[str, Cell], int]:
    """Read a Setting/Value block; returns the settings and the last row used."""
    ws = _sheet(wb, sheet)
    hdr, cols = _find_header(ws, KV_HEADERS, HDR_ROW)
    out: dict[str, Cell] = {}
    r = hdr + 1
    while (key := _norm(ws.cell(r, cols["Setting"]).value)) is not None:
        if str(key) in out:
            raise ReaderError(f"{sheet} row {r}: duplicate setting '{key}' (READ-SETTING)")
        out[str(key)] = Cell(
            sheet=sheet, row=r, column=str(key), value=_norm(ws.cell(r, cols["Value"]).value)
        )
        r += 1
    return out, r - 1


def read_workbook(path: Path) -> RawSpec:
    data = path.read_bytes()
    # Formulas, not cached values: the reader never trusts derived results.
    wb = openpyxl.load_workbook(path, data_only=False)
    project, _ = _read_kv(wb, "Project")
    wrapper, wrapper_end = _read_kv(wb, "Wrapper")

    sub_wrappers = list(_read_table(wb, SUB_WRAPPERS))
    if sub_wrappers:
        # Row 5 is the top wrapper; its name is a formula pointing at Wrapper > Top module name.
        top = sub_wrappers[0]
        name = top["Sub-wrapper"]
        if isinstance(name.value, str) and name.value.startswith("="):
            top_name = wrapper.get("Top module name")
            cells = dict(top.cells)
            cells["Sub-wrapper"] = name.model_copy(
                update={"value": top_name.value if top_name else None}
            )
            sub_wrappers[0] = top.model_copy(update={"cells": cells})
    placement_from = max((r.row for r in sub_wrappers), default=HDR_ROW) + 1

    return RawSpec(
        source=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        project=project,
        wrapper=wrapper,
        clocks=_read_table(wb, CLOCKS),
        resets=_read_table(wb, RESETS),
        fabrics=_read_table(wb, FABRICS),
        masters=_read_table(wb, MASTERS),
        slaves=_read_table(wb, SLAVES),
        bridges=_read_table(wb, BRIDGES),
        topology=_read_table(wb, TOPOLOGY),
        access_rules=_read_table(wb, ACCESS_RULES),
        memory_map=_read_table(wb, MEMORY_MAP),
        address_decode=_read_table(wb, ADDRESS_DECODE),
        port_options=_read_table(wb, PORT_OPTIONS),
        sub_wrappers=tuple(sub_wrappers),
        placement=_read_table(wb, PLACEMENT, first_row=placement_from),
        exposed_ports=_read_table(wb, EXPOSED_PORTS, first_row=wrapper_end + 1),
    )
