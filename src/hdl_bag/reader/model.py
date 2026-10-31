# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""RawSpec: the workbook's input cells, unparsed, with their locations.

The reader does not interpret values; checks and resolve parse them (see
``hdl_bag.reader.values``) so that every problem is reported against the cell it
came from.
"""

from pydantic import BaseModel, ConfigDict

RawValue = str | int | float | bool | None


class Cell(BaseModel):
    """One input cell. Strings are stripped; blank cells are ``None``."""

    model_config = ConfigDict(frozen=True)

    sheet: str
    row: int
    column: str  # header name; for Setting/Value blocks, the setting
    value: RawValue

    @property
    def where(self) -> str:
        return f"{self.sheet} row {self.row}"


class Row(BaseModel):
    """One data row of a table, keyed by header name (input columns only)."""

    model_config = ConfigDict(frozen=True)

    sheet: str
    row: int
    cells: dict[str, Cell]

    def __getitem__(self, column: str) -> Cell:
        return self.cells[column]

    def value(self, column: str) -> RawValue:
        return self.cells[column].value

    @property
    def where(self) -> str:
        return f"{self.sheet} row {self.row}"


class RawSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str  # workbook file name
    sha256: str  # of the workbook bytes
    project: dict[str, Cell]  # Project sheet, keyed by Setting
    wrapper: dict[str, Cell]  # Wrapper settings, keyed by Setting
    clocks: tuple[Row, ...]
    resets: tuple[Row, ...]
    fabrics: tuple[Row, ...]
    masters: tuple[Row, ...]
    slaves: tuple[Row, ...]
    bridges: tuple[Row, ...]
    topology: tuple[Row, ...]
    access_rules: tuple[Row, ...]
    memory_map: tuple[Row, ...]
    address_decode: tuple[Row, ...]
    port_options: tuple[Row, ...]
    sub_wrappers: tuple[Row, ...]  # first row is the top wrapper
    placement: tuple[Row, ...]
    exposed_ports: tuple[Row, ...]
