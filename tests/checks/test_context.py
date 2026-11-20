# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Context's parse cache: a cell is parsed once per parser, not once per cell (#149).

Keyed on the cell alone, whichever accessor touched a cell first decided what every later
accessor saw: ``text`` caches the raw string, and ``name`` then handed it back without
reporting ``NAME-IDENT``. A cell still carries at most one finding per parser.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from hdl_bag.checks import Context
from hdl_bag.reader import read_workbook
from hdl_bag.reader.model import Cell, RawSpec
from hdl_bag.templates import builtin_registry

GOLDEN = Path(__file__).parents[1] / "golden" / "demo_ss.xlsx"


@pytest.fixture(scope="module")
def raw() -> RawSpec:
    return read_workbook(GOLDEN)


@pytest.fixture
def ctx(raw: RawSpec) -> Context:
    return Context(raw=raw, registry=builtin_registry(), base_dir=GOLDEN.parent)


def _cell(value: str, row: int = 16) -> Cell:
    return Cell(sheet="Hierarchy", row=row, column="Sub-wrapper", value=value)


def test_a_name_is_still_refused_when_text_read_the_cell_first(ctx: Context) -> None:
    """The order the checks run in must not decide whether a rule fires: ``check_names``
    reads a sub-wrapper name with ``name``, ``wrapper._prefixed_units`` with ``text``."""
    cell = _cell("Foo-bar")
    assert ctx.text(cell) == "Foo-bar"
    assert ctx.name(cell) is None
    assert [f.rule for f in ctx.findings] == ["NAME-IDENT"]


def test_a_cell_reports_once_however_often_one_parser_reads_it(ctx: Context) -> None:
    cell = _cell("Foo-bar")
    assert [ctx.name(cell), ctx.name(cell), ctx.name(cell)] == [None, None, None]
    assert [f.rule for f in ctx.findings] == ["NAME-IDENT"]


def test_a_strict_parser_refuses_what_text_accepted(ctx: Context) -> None:
    """Every parser, not just ``name``: all of them end in an isinstance check over the one
    cached value, so any pair over one cell had the same hole."""
    reads: tuple[tuple[Cell, Callable[[Cell], object]], ...] = (
        (_cell("Foo-bar", 16), lambda c: ctx.name(c)),
        (_cell("maybe", 17), lambda c: ctx.yes_no(c)),
        (_cell("zz", 18), lambda c: ctx.hexval(c)),
        (_cell("two", 19), lambda c: ctx.integer(c)),
        (_cell("sideways", 20), lambda c: ctx.enum(c, ("up", "down"))),
    )
    for cell, read in reads:
        assert ctx.text(cell) is not None
        assert read(cell) is None, f"{cell.value!r} was accepted after text() cached it"
    assert [f.rule for f in ctx.findings] == [
        "NAME-IDENT",
        *["VALUE-ILLEGAL"] * 4,
    ]


def test_the_two_name_dialects_do_not_decide_for_each_other(ctx: Context) -> None:
    """``lowercase`` changes what is accepted, so it is part of the key: a cell read as an
    any-case identifier must still be refused as a lowercase one."""
    cell = _cell("Foo")
    assert ctx.name(cell, lowercase=False) == "Foo"
    assert ctx.name(cell) is None
    assert [f.rule for f in ctx.findings] == ["NAME-IDENT"]


def test_one_enum_does_not_decide_for_another(ctx: Context) -> None:
    """``allowed`` changes what is accepted, so it is part of the key too."""
    cell = _cell("cut")
    assert ctx.enum(cell, ("cut", "none")) == "cut"
    assert ctx.enum(cell, ("yes", "no")) is None
    assert [f.rule for f in ctx.findings] == ["VALUE-ILLEGAL"]
