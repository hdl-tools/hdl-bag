# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Tests for the names shared by the checks and the generators."""

import json
from importlib.resources import files
from pathlib import PurePosixPath

import pytest

from hdl_bag.naming import _DECL, Unit, describe, emitted_units, label, unit_name
from hdl_bag.templates.sources import VENDOR, vendored_units

_PKG = files("hdl_bag")
_SOURCE_DIRS = ("rtl/inhouse", "rtl/vendor/derived", "formal", "formal/vendor/zipcpu")


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [("", "bag_sync"), (None, "bag_sync"), ("p", "p_bag_sync")],
)
def test_unit_name_joins_a_prefix_with_an_underscore_and_leaves_a_blank_one_alone(
    prefix: str | None, expected: str
) -> None:
    assert unit_name(prefix, "bag_sync") == expected


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("AXI_BUS", "a vendored PULP interface"),
        ("axi_pkg", "a vendored PULP package"),
        ("axi_mux", "a vendored PULP module"),
        ("bag_axi_mux", "a module BAG derives from PULP"),
        ("bag_sync", "an in-house module"),
        ("bag_faxi4", "a BAG formal checker module"),
        ("bag_step_ff1", "a BAG formal checker module"),
        ("faxil_slave", "a vendored ZipCPU module"),
    ],
)
def test_emitted_units_say_what_each_one_is(name: str, text: str) -> None:
    assert label(emitted_units()[name]) == text


def test_describe_leaves_the_article_off_so_callers_never_cut_a_label() -> None:
    assert describe(Unit("x", "p.sv", "module", "derived")) == "module BAG derives from PULP"


def test_emitted_units_cover_every_source_on_disk() -> None:
    """The inventory is what the collision checks refuse, so a unit missing from it is a name a
    user can take and clash with: every vendored unit and every .sv/.v file must be in it."""
    units = emitted_units()
    index = json.loads((_PKG / VENDOR / "index.json").read_text(encoding="utf-8"))
    declared = {u for f in index["files"] for u in f["defines"]}
    assert declared <= units.keys(), declared - units.keys()
    by_path: dict[str, set[str]] = {}
    for u in units.values():
        by_path.setdefault(u.path, set()).add(u.name)
    for sub in _SOURCE_DIRS:
        for f in (_PKG / sub).iterdir():
            if f.name.endswith((".sv", ".v")):
                path = f"{sub}/{f.name}"
                assert path in by_path, f"{path} contributes no unit to emitted_units()"
                if f.name != "bag_step.v":  # declares two modules, ff0 and ff1
                    assert by_path[path] == {PurePosixPath(f.name).stem}, path
    assert by_path["formal/bag_step.v"] == {"bag_step_ff0", "bag_step_ff1"}


def test_no_two_sources_claim_the_same_unit_name() -> None:
    """emitted_units() is one dict over five sources, so a shared name would overwrite silently:
    the name stays reserved and the checks keep working, but #106's renamer would rewrite one
    declaration of the two and emit RTL that does not elaborate."""
    index = json.loads((_PKG / VENDOR / "index.json").read_text(encoding="utf-8"))
    claims: dict[str, list[str]] = {}
    for f in index["files"]:
        for u in f["defines"]:
            claims.setdefault(u, []).append(f"{VENDOR}/{f['path']}")
    for sub in _SOURCE_DIRS:
        for f in (_PKG / sub).iterdir():
            if f.name.endswith((".sv", ".v")):
                text = (_PKG / sub / f.name).read_text(encoding="utf-8")
                for _, name in _DECL.findall(text):
                    claims.setdefault(name, []).append(f"{sub}/{f.name}")
    twice = {u: paths for u, paths in claims.items() if len(paths) > 1}
    assert not twice, twice
    assert set(claims) == set(emitted_units())


def test_every_emitted_unit_is_declared_in_its_own_file_with_its_kind() -> None:
    """kinds come from the declaration regex, whose miss would silently fall back to module."""
    for name, u in emitted_units().items():
        text = (_PKG / u.path).read_text(encoding="utf-8")
        kinds = {n: ("module" if k == "macromodule" else k) for k, n in _DECL.findall(text)}
        assert kinds.get(name) == u.kind, (name, u.path)
    assert set(emitted_units()) >= set(vendored_units())


def test_the_inventory_is_sorted_and_read_only() -> None:
    units = emitted_units()
    assert list(units) == sorted(units)
    with pytest.raises(TypeError):
        units["x"] = Unit("x", "p", "module", "pulp")  # type: ignore[index]


def test_the_only_unit_in_the_vendored_headers_is_the_uvm_only_assert_rpt_pkg() -> None:
    """assert_rpt_pkg sits inside `ifdef UVM, which BAG never defines, so it is never elaborated
    and is deliberately not reserved. A re-vendor that adds a real unit to a header must fail
    here, so someone decides whether to add it to emitted_units()."""
    index = json.loads((_PKG / VENDOR / "index.json").read_text(encoding="utf-8"))
    found = {
        name
        for h in index["headers"]
        for _, name in _DECL.findall((_PKG / VENDOR / h["path"]).read_text(encoding="utf-8"))
    }
    assert found == {"assert_rpt_pkg"}


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("macromodule m;", [("macromodule", "m")]),
        ("module static m;", [("module", "m")]),
        ("module\tm #(", [("module", "m")]),
        ("interface class c;", []),
        ("extern module m;", []),
        ("endmodule", []),
    ],
)
def test_the_declaration_regex_reads_the_forms_that_matter(
    text: str, found: list[tuple[str, str]]
) -> None:
    assert _DECL.findall(text) == found
