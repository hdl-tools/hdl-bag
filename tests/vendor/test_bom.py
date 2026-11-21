# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""docs/bom.md is exactly scripts/bom.py's output, and the script reads the tree, not the index.

The index's licence field is a constant and importlib.metadata is empty for some packages, so
these tests are what stop the bill of materials from being a guess.
"""

import importlib.metadata as md
import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_vendor import PERMISSIVE

REPO = Path(__file__).parents[2]
NOT_BESIDE = "not beside the file"


def _bom() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bom", REPO / "scripts" / "bom.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bom"] = module
    spec.loader.exec_module(module)
    return module


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_the_committed_bom_is_up_to_date() -> None:
    committed = (REPO / "docs" / "bom.md").read_text(encoding="utf-8")
    assert committed == _bom().render(), "run `uv run --no-project python scripts/bom.py`"


def test_every_vendored_file_on_disk_is_in_exactly_one_file_set() -> None:
    """Walks the disk, so a directory the index does not know (derived/) is caught."""
    listed = [p for row in _bom().file_sets() for p in row.paths]
    assert len(listed) == len(set(listed)), "a file is listed twice"
    skip = {"LICENSE", "SOURCE.md", "index.json"}
    src = REPO / "src" / "hdl_bag"
    on_disk = {
        p.relative_to(REPO).as_posix()
        for root in (src / "rtl" / "vendor", src / "formal" / "vendor")
        for p in root.rglob("*")
        if p.is_file() and p.name not in skip
    }
    assert on_disk == set(listed)


def test_no_file_set_is_empty() -> None:
    rows = _bom().file_sets()
    assert len(rows) >= 5 and all(row.paths for row in rows)


def test_every_vendored_file_names_an_allowed_licence() -> None:
    bom = _bom()
    checked = 0
    for row in bom.file_sets():
        for path in row.paths:
            assert bom.licence_of(REPO / path) in PERMISSIVE, path
            checked += 1
    assert checked >= 60


def test_a_named_licence_text_is_that_licence() -> None:
    bom = _bom()
    named = [row for row in bom.file_sets() if row.text is not None]
    assert len(named) >= 4
    for row in named:
        assert bom.licence_of_text(REPO / row.text) == row.licence, row.text


def test_the_two_files_without_a_text_say_so() -> None:
    """The gap is stated, and tracked by #172, not hidden."""
    gaps = [row for row in _bom().file_sets() if row.text is None]
    assert sorted(p for row in gaps for p in row.paths) == [
        "src/hdl_bag/rtl/vendor/pulp/common_cells/include/common_cells/assertions.svh",
        "src/hdl_bag/rtl/vendor/pulp/common_cells/src/read.sv",
    ]
    bom = (REPO / "docs" / "bom.md").read_text(encoding="utf-8")
    assert bom.count(NOT_BESIDE) == 2 and "#172" in bom


def test_a_file_with_no_licence_notice_stops_the_script(tmp_path: Path) -> None:
    path = tmp_path / "x.sv"
    path.write_text("module x; endmodule\n", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        _bom().licence_of(path)
    assert str(stop.value) == (
        f"{path}: no licence notice recognised in its first 40 lines; stop and check"
    )


def test_licences_cover_the_lock_closure_exactly() -> None:
    rows = _bom().python_rows()
    assert set(_bom().LICENCES) == {r.name for r in rows}, "enter or drop a licence in LICENCES"
    assert len(rows) >= 22


def test_declared_column_is_what_pyproject_asks_for() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    asked = {}
    for req in [*project["dependencies"], *project["optional-dependencies"]["sim"]]:
        m = re.fullmatch(r"([A-Za-z0-9._-]+)(.*)", req)
        assert m
        asked[_norm(m[1])] = m[2]
    declared = {
        r.name: r.declared for r in _bom().python_rows() if not r.declared.startswith("via")
    }
    assert declared == asked


def test_licence_agrees_with_installed_metadata_where_it_exists() -> None:
    compared = 0
    for row in _bom().python_rows():
        try:
            meta = md.metadata(row.name)
        except md.PackageNotFoundError:
            continue
        expr = meta.json.get("license_expression")
        if expr and meta["Version"] == row.locked:
            assert expr == row.licence, row.name
            compared += 1
    assert compared >= 5


def test_no_other_document_restates_a_bom_version_or_commit() -> None:
    bom = _bom()
    needles = [r.commit for r in bom.file_sets() if r.commit] + [
        "0.39.11",
        "1.39.0",
        "2.1.0",
        "0.1.28",
    ]
    for doc in ("docs/third-party.md", "docs/licensing.md", "NOTICE", "README.md"):
        text = (REPO / doc).read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{doc} restates {needle}; link to docs/bom.md instead"
