# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The Phase 2 gate corpus (tests/configs/p2): 10+ APB/AHB-only configs.

Gate: every config lints (slang, Verilator, Verible) and compiles in both port styles,
one with nested sub-wrappers. Beyond the gate, every config also passes the structural
connectivity oracle and an IR-driven route simulation through its generated top.
"""

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest
from hier_oracle import check
from simrun import REPO, run_cocotb

from hdl_bag.checks import run_checks
from hdl_bag.flows import compile_design, lint
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.ir import dumps, load, resolve
from hdl_bag.ir.to_workbook import to_workbook
from hdl_bag.reader import read_workbook
from hdl_bag.template.builder import build_workbook

CONFIGS = REPO / "tests" / "configs" / "p2"
NAMES = sorted(p.name.removesuffix(".ir.yaml") for p in CONFIGS.glob("*.ir.yaml"))
HERE = Path(__file__).parent


def _corpus() -> ModuleType:
    spec = importlib.util.spec_from_file_location("corpus", CONFIGS / "corpus.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ir_text(text: str) -> str:
    return text.split("provenance:")[0]


def test_corpus_has_ten_configs_with_nesting() -> None:
    assert len(NAMES) >= 10
    nested = [n for n in NAMES if load(CONFIGS / f"{n}.ir.yaml").hierarchy.wrappers]
    assert nested, "the gate needs a config with nested sub-wrappers"


@pytest.mark.parametrize("name", sorted(_corpus().BUILT))
def test_ir_matches_its_workbook_source(tmp_path: Path, name: str) -> None:
    """The committed IR is what `bag check` + resolve make of the corpus.py workbook."""
    path = tmp_path / f"{name}.xlsx"
    build_workbook(data=_corpus().BUILT[name]().data).save(path)
    shutil.copy(CONFIGS / "header.txt", tmp_path / "header.txt")
    result = run_checks(read_workbook(path), tmp_path)
    assert [f.message for f in result.findings] == []
    committed = (CONFIGS / f"{name}.ir.yaml").read_text()
    assert _ir_text(dumps(resolve(result))) == _ir_text(committed), (
        "stale: run `uv run python tests/configs/p2/corpus.py`"
    )


@pytest.mark.parametrize("name", NAMES)
def test_round_trip_and_oracle(tmp_path: Path, name: str) -> None:
    spec = load(CONFIGS / f"{name}.ir.yaml")
    # IR -> workbook -> check -> IR is the identity (apart from provenance).
    wb = tmp_path / "rt.xlsx"
    to_workbook(spec).save(wb)
    shutil.copy(CONFIGS / "header.txt", tmp_path / "header.txt")
    result = run_checks(read_workbook(wb), tmp_path)
    assert [f.message for f in result.findings] == []
    assert _ir_text(dumps(resolve(result))) == _ir_text(dumps(spec))
    # The generated hierarchy matches the IR, port by port and net by net.
    generate(spec, tmp_path / "out", CONFIGS, "flat")
    assert check(spec, tmp_path / "out" / "rtl") == []


@pytest.mark.eda
@pytest.mark.parametrize("style", ["flat", "sv-interface"])
@pytest.mark.parametrize("name", NAMES)
def test_gate_lint_and_compile(name: str, style: str) -> None:
    root = REPO / "out" / "corpus" / f"{name}_{style}"
    out = generate(load(CONFIGS / f"{name}.ir.yaml"), root, CONFIGS, style)
    files, inc = sources(out), include_dirs(out)
    for report in (
        lint(files, out.top, root, incdirs=inc),
        compile_design(files, out.top, root, incdirs=inc),
    ):
        assert report.ok, report.format()


@pytest.mark.eda
@pytest.mark.parametrize("name", NAMES)
def test_route_simulation(name: str) -> None:
    src = CONFIGS / f"{name}.ir.yaml"
    spec = load(src)
    root = REPO / "out" / "corpus" / f"{name}_route"
    out = generate(spec, root, CONFIGS)  # the config's own port style
    run_cocotb([root / f for f in sources(out)], out.top, HERE / "cocotb_route.py",
               f"route_{name}", env={"BAG_IR": str(src)},
               incdirs=[root / d for d in include_dirs(out)])  # fmt: skip
