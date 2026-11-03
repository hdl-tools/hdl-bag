# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The generated cocotb tests: what `bag generate` writes, and which tests apply."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import hdl_bag.tests_gen
from hdl_bag.cli import main
from hdl_bag.flows import simulate
from hdl_bag.gen import generate, load_outputs, sources
from hdl_bag.ir import load
from hdl_bag.tests_gen import TESTS, applicable_tests

TESTS_DIR = Path(__file__).parents[1]
DEMO = TESTS_DIR / "golden" / "demo_ss.ir.yaml"
ROUTE = Path(hdl_bag.tests_gen.__file__).with_name("route_tests.py")


def test_every_route_test_is_in_the_list() -> None:
    """A test added to route_tests.py that no generated module calls would never run."""
    tree = ast.parse(ROUTE.read_text(encoding="utf-8"))
    public = [n.name for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_")]  # fmt: skip
    assert public == [t.name for t in TESTS]


def test_demo_ss_drops_only_tests_it_has_nothing_for() -> None:
    spec = load(DEMO)
    assert set(TESTS_NOT_ON_DEMO_SS) == {t.name for t in TESTS} - set(applicable_tests(spec))


# demo_ss has one master on its AHB-Lite bus, and no narrow APB behind bag_axi2apb.
TESTS_NOT_ON_DEMO_SS = (
    "arbitration_follows_the_fabric_policy",
    "narrow_apb_behind_axi2apb_keeps_lanes_strobes_and_errors",
)


def test_generate_writes_a_module_that_imports_without_a_simulator(tmp_path: Path) -> None:
    out = generate(load(DEMO), tmp_path, DEMO.parent)
    assert out.tests == "tests/demo_ss_tests.py"
    assert load_outputs(tmp_path).tests == out.tests
    assert {out.tests, "tests/demo_ss.ir.yaml"} <= set(out.files)
    text = (tmp_path / out.tests).read_text(encoding="utf-8")
    assert "DO NOT EDIT" in text and "IR sha256" in text
    names = [n.name for n in ast.parse(text).body if isinstance(n, ast.AsyncFunctionDef)]
    assert names == applicable_tests(load(DEMO))
    assert load(tmp_path / "tests" / "demo_ss.ir.yaml").project.name == "demo_ss"
    # Importing the package must not need cocotb, or the tool would need the extra.
    code = (
        "import sys, hdl_bag.tests_gen; sys.exit(any(m.startswith('cocotb') for m in sys.modules))"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_the_generate_tests_flag_is_honoured(tmp_path: Path) -> None:
    spec = load(DEMO)
    off = spec.model_copy(update={"project": spec.project.model_copy(update={
        "outputs": spec.project.outputs.model_copy(update={"tests": False})})})  # fmt: skip
    out = generate(off, tmp_path, DEMO.parent)
    assert out.tests is None
    assert not any(f.startswith("tests/") for f in out.files)
    result = CliRunner().invoke(main, ["sim", str(tmp_path)])
    assert result.exit_code == 1 and "no generated tests" in result.output


def test_a_user_supplied_fabric_gets_no_tests(tmp_path: Path) -> None:
    cfg = TESTS_DIR / "configs" / "p3" / "external_fabric.ir.yaml"
    assert generate(load(cfg), tmp_path, cfg.parent).tests is None


@pytest.mark.parametrize("header", ["// Copyright Acme\n// Second line\n"])
def test_a_systemverilog_file_header_stays_a_python_comment(tmp_path: Path, header: str) -> None:
    from hdl_bag.tests_gen import render_tests

    text = render_tests(load(DEMO), header)
    assert text.startswith("# // Copyright Acme\n# // Second line\n")
    ast.parse(text)


@pytest.mark.eda
def test_sim_reports_a_failing_test_by_name(tmp_path: Path) -> None:
    cfg = TESTS_DIR / "configs" / "p2" / "apb4_min.ir.yaml"
    out = generate(load(cfg), tmp_path, cfg.parent)
    report = simulate(
        [tmp_path / f for f in sources(out)], out.top,
        Path(__file__).with_name("cocotb_one_fails.py"), tmp_path / "sim",
    )  # fmt: skip
    assert not report.ok
    assert [f.rule for f in report.findings] == ["TEST-FAILED"]
    assert "this_one_fails" in report.findings[0].message


@pytest.mark.eda
def test_bag_sim_runs_what_its_filter_finds(tmp_path: Path) -> None:
    cfg = TESTS_DIR / "configs" / "p2" / "apb4_min.ir.yaml"
    assert CliRunner().invoke(main, ["generate", str(cfg), "-o", str(tmp_path)]).exit_code == 0
    result = CliRunner().invoke(main, ["sim", str(tmp_path), "--filter", "concurrent_traffic"])
    assert result.exit_code == 0, result.output
    assert "sim (verilator, cocotb): 0 unwaived" in result.output
    none = CliRunner().invoke(main, ["sim", str(tmp_path), "--filter", "no_such_test"])
    assert none.exit_code == 1 and "selects no test" in none.output
