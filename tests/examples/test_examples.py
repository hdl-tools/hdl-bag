# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The workbooks in examples/: each is the committed, recalculated output of its builder
(tests/examples/build.py), checks clean with zero findings, and is named in the docs.

Content is compared as a signature, never as bytes: a recalculated workbook records the
machine's locale and LibreOffice build, so its bytes differ from one rebuild to the next.
"""

import io
from pathlib import Path

import openpyxl
import pytest
from build import BUILT, REBUILD
from click.testing import CliRunner
from test_golden_workbook import _bad_checks

from hdl_bag.checks import run_checks
from hdl_bag.cli import main
from hdl_bag.ir import resolve
from hdl_bag.reader import read_workbook
from hdl_bag.template import build_workbook
from hdl_bag.template.signature import signature

REPO = Path(__file__).parents[2]
EXAMPLES = REPO / "examples"
NAMES = sorted(BUILT)


def test_the_examples_directory_holds_exactly_the_built_workbooks() -> None:
    files = {p.name for p in EXAMPLES.iterdir() if not p.name.startswith(".")}
    assert files == {*(f"{n}.xlsx" for n in NAMES), "header.txt", "README.md"}


@pytest.mark.parametrize("name", NAMES)
def test_the_committed_workbook_is_what_the_builder_makes(name: str) -> None:
    buf = io.BytesIO()
    build_workbook(data=BUILT[name]().data).save(buf)
    buf.seek(0)
    committed = signature(openpyxl.load_workbook(EXAMPLES / f"{name}.xlsx"))
    assert committed == signature(openpyxl.load_workbook(buf)), f"stale: run `{REBUILD}`"


@pytest.mark.parametrize("name", NAMES)
def test_the_committed_workbook_was_recalculated(name: str) -> None:
    wb = openpyxl.load_workbook(EXAMPLES / f"{name}.xlsx", data_only=True)
    assert wb["Topology"]["L5"].value == "ok", f"no cached results: run `{REBUILD}`"
    assert _bad_checks(wb) == []


@pytest.mark.parametrize("name", NAMES)
def test_bag_check_finds_nothing(name: str) -> None:
    result = run_checks(read_workbook(EXAMPLES / f"{name}.xlsx"), EXAMPLES)
    assert [f"{f.severity}: {f.message}" for f in result.findings] == []


def test_the_gate_target_has_four_masters_and_eight_slaves() -> None:
    spec = resolve(run_checks(read_workbook(EXAMPLES / "soc_4x8.xlsx"), EXAMPLES))
    assert (len(spec.masters), len(spec.slaves)) == (4, 8)


@pytest.mark.parametrize("name", NAMES)
def test_every_example_is_named_in_the_docs(name: str) -> None:
    for doc in (EXAMPLES / "README.md", REPO / "docs/manual/chapters/first.tex"):
        assert f"{name}.xlsx" in doc.read_text(encoding="utf-8").replace("\\_", "_"), doc


@pytest.mark.eda
@pytest.mark.parametrize("name", NAMES)
def test_flow_takes_the_example_from_workbook_to_passing_tests(name: str, tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["flow", str(EXAMPLES / f"{name}.xlsx"), "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
