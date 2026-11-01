# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Findings, severities and the rule catalogue."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from hdl_bag.template.builder import SHEET_ORDER


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


E, W = Severity.ERROR, Severity.WARNING

# Every rule BAG checks. IDs are stable: tests, docs and users refer to them.
RULES: Final[dict[str, Severity]] = {
    # values and settings
    "VALUE-MISSING": E,
    "VALUE-ILLEGAL": E,
    "PROJ-SCHEMA-VERSION": E,
    "WRAP-FILE-HEADER": E,
    "WRAP-PATTERN": E,
    # names and references
    "NAME-UNIQUE": E,
    "NAME-IDENT": E,
    "REF-UNKNOWN": E,
    "CLK-DOMAIN-TWO-CLOCKS": E,
    "CLK-DOMAIN-NO-RESET": E,
    "CLK-RESET-DOMAIN": E,
    "CLK-CDC-RESET": E,
    # widths, templates, fabrics
    "WIDTH-DATA": E,
    "WIDTH-ADDR": E,
    "WIDTH-ID": E,
    "WIDTH-USER": E,
    "TMPL-FABRIC": E,
    "TMPL-BRIDGE": E,
    "FAB-TOPOLOGY": E,
    "FAB-ARBITRATION": E,
    "FAB-SINGLE-MASTER": E,
    "FAB-EMPTY-SIDE": E,
    "FAB-NA-SETTING": W,
    "FAB-ID-GROWTH": W,
    "PORT-ADAPTER": E,
    # topology
    "TOPO-UNKNOWN-NAME": E,
    "TOPO-ILLEGAL-LINK": E,
    "TOPO-DUPLICATE": E,
    "TOPO-MULTI-ATTACH": E,
    "TOPO-NOT-CONNECTED": E,
    "TOPO-INDEX-MISSING": E,
    "TOPO-INDEX-NOT-INTEGER": E,
    "TOPO-INDEX-OUT-OF-RANGE": E,
    "TOPO-INDEX-REPEATED": E,
    "TOPO-PRIORITY-NOT-INTEGER": E,
    "TOPO-PRIORITY-MISSING": E,
    "TOPO-PRIORITY-REPEATED": E,
    "TOPO-BRIDGE-CYCLE": E,
    # access rules
    "ACC-UNKNOWN-NAME": E,
    "ACC-NOT-SAME-FABRIC": E,
    "ACC-DUPLICATE": E,
    "ACC-UNREACHABLE-SLAVE": W,
    # memory map and bridges
    "MM-UNKNOWN-SLAVE": E,
    "MM-NOT-CONNECTED": E,
    "MM-OVERLAP": E,
    "MM-SIZE-NOT-POW2": E,
    "MM-MISALIGNED": E,
    "MM-ADDR-RANGE": E,
    "MM-SLAVE-ALIGN": E,
    "MM-SMALL-AXI": W,
    "MM-PATH-NO-REGION": W,
    "BRIDGE-WINDOW-BASE": E,
    "BRIDGE-WINDOW-EMPTY": E,
    # address decode
    "DEC-UNKNOWN-MASTER": E,
    "DEC-DUPLICATE": E,
    "DEC-MISSING-ROW": E,
    "DEC-MISS-RESP": E,
    "DEC-DEFAULT-SLAVE": E,
    "DEC-ALIAS-SIZE": E,
    "DEC-ALIAS-TARGET": E,
    "DEC-ALIAS-OVERLAP": E,
    "DEC-ALIAS-ENABLE": E,
    "SEC-PROT-TIED": E,
    # port options
    "PORTOPT-UNKNOWN-PORT": E,
    "PORTOPT-DUPLICATE": E,
    "PORTOPT-SIGNAL": E,
    "PORTOPT-ERROR-MASKED": W,
    # hierarchy and wrapper
    "HIER-UNKNOWN-PARENT": E,
    "HIER-SELF-PARENT": E,
    "HIER-PARENT-LOOP": E,
    "HIER-TOP": E,
    "HIER-UNKNOWN-INSTANCE": E,
    "HIER-UNKNOWN-SUBWRAPPER": E,
    "HIER-PLACED-TWICE": E,
    "HIER-RTL-NAME": E,
    "HIER-RESERVED-NAME": E,
    "HIER-EMPTY": W,
    "WRAP-PORT-NOT-LISTED": E,
    "WRAP-UNKNOWN-PORT": E,
    "WRAP-DUPLICATE-PORT": E,
    "WRAP-INTERNAL-PORT": E,
    "WRAP-NAME-COLLISION": E,
}

_SHEET_RANK: Final = {name: i for i, name in enumerate(SHEET_ORDER)}


@dataclass(frozen=True, order=False)
class Finding:
    rule: str
    sheet: str
    row: int | None  # None: sheet-level finding
    what: str

    @property
    def severity(self) -> Severity:
        return RULES[self.rule]

    @property
    def message(self) -> str:
        where = self.sheet if self.row is None else f"{self.sheet} row {self.row}"
        return f"{where}: {self.what} ({self.rule})"

    def sort_key(self) -> tuple[int, int, str, str]:
        return (_SHEET_RANK.get(self.sheet, len(_SHEET_RANK)), self.row or 0, self.rule, self.what)

    def __str__(self) -> str:
        return self.message
