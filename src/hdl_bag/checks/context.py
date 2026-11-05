# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Check context: parses every input cell once and derives the connection model.

A cell that fails to parse is reported once, where it is, and reads as None for
every later check; checks skip whatever depends on a None, so one mistake gives one
message (D23). Resolve builds the IR from the same context once the checks pass.
"""

import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from hdl_bag.checks.core import Finding
from hdl_bag.protocols import AXI_FAMILY, Protocol
from hdl_bag.reader.model import Cell, RawSpec, Row
from hdl_bag.reader.values import is_identifier, parse_hex, parse_int, parse_yes_no
from hdl_bag.templates import Registry

Kind = Literal["master", "slave", "bridge", "fabric"]
_SV_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Link:
    row: Row
    source: str
    destination: str
    fabric: str
    side: Literal["master", "slave"]  # master port: link into the fabric
    end: str  # the master, slave or bridge on the other side of the fabric
    index: int | None
    priority: int | None


@dataclass(frozen=True)
class Adapter:
    type: str  # pc | dw | cdc | iw | fifo | cut
    name: str
    port: str
    fabric: str


@dataclass
class Context:
    raw: RawSpec
    registry: Registry
    base_dir: Path
    findings: list[Finding] = field(default_factory=list)
    _cache: dict[tuple[str, int, str], object] = field(default_factory=dict)

    # ------------------------------------------------------------ reporting
    def report(self, rule: str, where: Cell | Row | str, what: str) -> None:
        if isinstance(where, str):
            self.findings.append(Finding(rule, where, None, what))
        else:
            self.findings.append(Finding(rule, where.sheet, where.row, what))

    # ------------------------------------------------------------ parsing (cached)
    def _parsed(self, cell: Cell, parse: Callable[[Cell], object]) -> object:
        key = (cell.sheet, cell.row, cell.column)
        if key not in self._cache:
            self._cache[key] = parse(cell)
        return self._cache[key]

    def text(self, cell: Cell, required: bool = False) -> str | None:
        def parse(c: Cell) -> object:
            if c.value is None:
                if required:
                    self.report("VALUE-MISSING", c, f"{c.column} is required")
                return None
            return str(c.value)

        v = self._parsed(cell, parse)
        return v if isinstance(v, str) else None

    def external_module(self, row: Row) -> str | None:
        """Fabrics > External module; n/a means blank, as on the other optional settings."""
        value = self.text(row["External module"])
        return None if value == "n/a" else value

    def name(self, cell: Cell, required: bool = True, lowercase: bool = True) -> str | None:
        """An instance, port, region or wrapper name (lowercase SV identifier)."""

        def parse(c: Cell) -> object:
            if c.value is None:
                if required:
                    self.report("VALUE-MISSING", c, f"{c.column} is required")
                return None
            ok = (
                is_identifier(c.value)
                if lowercase
                else (isinstance(c.value, str) and _SV_IDENT.fullmatch(c.value) is not None)
            )
            if not ok:
                kind = "a lowercase" if lowercase else "an"
                self.report(
                    "NAME-IDENT",
                    c,
                    f"{c.column} {c.value!r} is not {kind} SystemVerilog identifier",
                )
                return None
            return c.value

        v = self._parsed(cell, parse)
        return v if isinstance(v, str) else None

    def enum(self, cell: Cell, allowed: Iterable[str], required: bool = True) -> str | None:
        allowed = tuple(allowed)

        def parse(c: Cell) -> object:
            if c.value is None:
                if required:
                    self.report("VALUE-MISSING", c, f"{c.column} is required")
                return None
            if c.value not in allowed:
                self.report(
                    "VALUE-ILLEGAL", c, f"{c.column} {c.value!r} is not one of {', '.join(allowed)}"
                )
                return None
            return c.value

        v = self._parsed(cell, parse)
        return v if isinstance(v, str) else None

    def integer(
        self, cell: Cell, minimum: int = 0, required: bool = True, tokens: Sequence[str] = ()
    ) -> int | str | None:
        """An integer >= minimum, or one of ``tokens`` (e.g. 'auto', 'n/a')."""

        def parse(c: Cell) -> object:
            if c.value is None:
                if required:
                    self.report("VALUE-MISSING", c, f"{c.column} is required")
                return None
            if c.value in tokens:
                return c.value
            try:
                return parse_int(c.value, minimum=minimum)
            except ValueError:
                extra = "".join(f" or {t}" for t in tokens)
                self.report(
                    "VALUE-ILLEGAL",
                    c,
                    f"{c.column} {c.value!r} is not an integer >= {minimum}{extra}",
                )
                return None

        v = self._parsed(cell, parse)
        return v if isinstance(v, int | str) else None

    def hexval(self, cell: Cell, required: bool = True) -> int | None:
        def parse(c: Cell) -> object:
            if c.value is None:
                if required:
                    self.report("VALUE-MISSING", c, f"{c.column} is required")
                return None
            try:
                return parse_hex(c.value)
            except ValueError:
                self.report(
                    "VALUE-ILLEGAL", c, f"{c.column} {c.value!r} is not hex like 0x4000_0000"
                )
                return None

        v = self._parsed(cell, parse)
        return v if isinstance(v, int) else None

    def yes_no(self, cell: Cell, required: bool = True) -> bool | None:
        def parse(c: Cell) -> object:
            if c.value is None:
                if required:
                    self.report("VALUE-MISSING", c, f"{c.column} is required")
                return None
            try:
                return parse_yes_no(c.value)
            except ValueError:
                self.report("VALUE-ILLEGAL", c, f"{c.column} {c.value!r} is not Y or N")
                return None

        v = self._parsed(cell, parse)
        return v if isinstance(v, bool) else None

    def protocol(self, cell: Cell) -> Protocol | None:
        v = self.enum(cell, [p.value for p in Protocol])
        return Protocol(v) if v else None

    # ------------------------------------------------------------ model (filled by build())
    kinds: dict[str, Kind] = field(default_factory=dict)
    defs: dict[str, Row] = field(default_factory=dict)  # name -> defining row
    domains: dict[str, Row] = field(default_factory=dict)  # domain -> clock row
    links: list[Link] = field(default_factory=list)
    master_fabric: dict[str, str] = field(default_factory=dict)  # masters, bridge downstream ends
    slave_fabric: dict[str, str] = field(default_factory=dict)  # slaves, bridge upstream ends
    fabric_masters: dict[str, list[str]] = field(default_factory=dict)
    fabric_slaves: dict[str, list[str]] = field(default_factory=dict)
    # Top-level port names and their owners (filled by check_wrapper_ports).
    top_names: dict[str, str] = field(default_factory=dict)

    def rows(self, kind: Kind) -> tuple[Row, ...]:
        return {
            "master": self.raw.masters,
            "slave": self.raw.slaves,
            "bridge": self.raw.bridges,
            "fabric": self.raw.fabrics,
        }[kind]

    def fabric_row(self, fabric: str) -> Row | None:
        return self.defs.get(fabric) if self.kinds.get(fabric) == "fabric" else None

    def fabric_protocol(self, fabric: str) -> Protocol | None:
        row = self.fabric_row(fabric)
        return self.protocol(row["Protocol"]) if row else None

    def fabric_topology(self, fabric: str) -> str | None:
        row = self.fabric_row(fabric)
        return self.text(row["Topology"]) if row else None

    def port_protocol(self, port: str, side: Literal["master", "slave"]) -> Protocol | None:
        """Protocol of a master/slave, or of a bridge end (= that fabric's protocol)."""
        kind = self.kinds.get(port)
        if kind in ("master", "slave"):
            return self.protocol(self.defs[port]["Protocol"])
        if kind == "bridge":
            fab = (self.master_fabric if side == "master" else self.slave_fabric).get(port)
            return self.fabric_protocol(fab) if fab else None
        return None

    def ports(self) -> list[tuple[str, Literal["master", "slave"], str]]:
        """Every attached port end: (name, side, fabric), in a stable order."""
        ends: list[tuple[str, Literal["master", "slave"], str]] = []
        for link in self.links:
            ends.append((link.end, link.side, link.fabric))
        return ends

    # ------------------------------------------------------------ derived widths/adapters
    def master_id_width(self, port: str) -> int | None:
        if self.kinds.get(port) == "master":
            v = self.integer(self.defs[port]["ID width (bits)"], minimum=1, required=False)
            return v if isinstance(v, int) else None
        if self.kinds.get(port) == "bridge":
            return 1  # in-house bridges into AXI4 issue a single ID (bag_ahb2axi: ID width 1)
        return None

    def fabric_id_widths(self, fabric: str) -> tuple[int, int] | None:
        """(master-side, slave-side) ID widths of an AXI4 fabric."""
        if self.fabric_protocol(fabric) != Protocol.AXI4:
            return None
        masters = self.fabric_masters.get(fabric, [])
        widths = [
            w for m in masters
            if self.port_protocol(m, "master") == Protocol.AXI4
            and (w := self.master_id_width(m)) is not None
        ]  # fmt: skip
        master_side = max(widths, default=1)
        n = len(masters)
        return master_side, master_side + (0 if n <= 1 else math.ceil(math.log2(n)))

    def adapters(self) -> list[Adapter]:
        """Adapters between ports and fabrics, in chain order per port (D9, D10, D24, D33)."""
        pattern = self.adapter_pattern()
        opts = self.port_options()
        out: list[Adapter] = []
        for port, side, fabric in self.ports():
            frm, to = (port, fabric) if side == "master" else (fabric, port)
            types: list[str] = []
            kind = self.kinds.get(port)
            p_proto, f_proto = self.port_protocol(port, side), self.fabric_protocol(fabric)
            if kind in ("master", "slave") and p_proto in AXI_FAMILY and f_proto in AXI_FAMILY:
                prow, frow = self.defs[port], self.fabric_row(fabric)
                assert frow is not None
                if self.text(prow["Clock domain"]) != self.text(frow["Clock domain"]):
                    types.append("cdc")
                if p_proto != f_proto:
                    types.append("pc")
                if self.integer(prow["Data width (bits)"], 1) != self.integer(
                    frow["Data width (bits)"], 1
                ):
                    types.append("dw")
                # D33: width conversion stays on the AXI4 side of the protocol adapter, so
                # an AXI4 port on an AXI4-Lite fabric converts width before it becomes Lite
                # (a narrow AXI4 access never turns into a full-width Lite access).
                if p_proto == Protocol.AXI4 and "pc" in types and "dw" in types:
                    types.remove("dw")
                    types.insert(types.index("pc"), "dw")
                if kind == "slave" and p_proto == f_proto == Protocol.AXI4:
                    want = self.integer(
                        prow["ID width (bits)"], 1, required=False, tokens=("auto",)
                    )
                    ids = self.fabric_id_widths(fabric)
                    if isinstance(want, int) and ids and want != ids[1]:
                        types.append("iw")
            if port in opts and (side == "master" or kind != "bridge"):
                fifo, cut = opts[port]
                if fifo:
                    types.append("fifo")
                if cut:
                    types.append("cut")
            if side == "slave":
                types.reverse()  # fabric -> cut -> fifo -> iw -> dw|pc -> pc|dw -> cdc -> slave
            for t in types:
                name = pattern.format(**{"from": frm, "type": t, "to": to})
                out.append(Adapter(type=t, name=name, port=port, fabric=fabric))
        return out

    def adapter_pattern(self) -> str:
        cell = self.raw.wrapper.get("Adapter instance naming")
        v = self.text(cell) if cell else None
        return (
            v
            if v and {"{from}", "{type}", "{to}"} <= set(re.findall(r"\{\w+\}", v))
            else "{from}_{type}_2_{to}"
        )

    def port_options(self) -> dict[str, tuple[bool, bool]]:
        """Port -> (has FIFO, has register slice), for listed ports."""
        out: dict[str, tuple[bool, bool]] = {}
        for row in self.raw.port_options:
            port = self.text(row["Port"])
            if not port or port in out:
                continue
            depth = self.integer(row["FIFO depth"], 0, required=False)
            sl = self.enum(
                row["Register slice"],
                ("none", "cut", "multicut-2", "multicut-3", "n/a"),
                required=False,
            )
            out[port] = (
                isinstance(depth, int) and depth > 0,
                sl in ("cut", "multicut-2", "multicut-3"),
            )
        return out
