# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The generated cocotb tests: what `bag generate` writes, and which tests apply."""

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import hdl_bag.tests_gen
from hdl_bag.cli import main
from hdl_bag.flows import Report, simulate
from hdl_bag.gen import generate, load_outputs, sources
from hdl_bag.ir import load
from hdl_bag.tests_gen import TESTS, applicable_tests, ready_shim_module

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


# demo_ss has one master on its AHB-Lite bus, adapters or bridges on the paths the crossbar,
# head-of-line and exclusive tests need, and no slave narrower than 32 bits.
TESTS_NOT_ON_DEMO_SS = (
    "arbitration_follows_the_fabric_policy",
    "axi4_crossbars_admit_max_mst_trans_per_id",
    "shared_buses_block_behind_a_stalled_slave_and_crossbars_do_not",
    "exclusive_accesses_carry_lock_and_exokay_unchanged",
    "an_error_on_any_beat_of_a_split_access_reaches_the_master",
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
    assert text.startswith("# coding: utf-8\n# // Copyright Acme\n# // Second line\n")
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


class _FakeRunner:
    """Stands in for cocotb's Verilator runner: writes RESULTS and exits with CODE."""

    def __init__(self, results: str | None, code: int) -> None:
        self.results, self.code = results, code

    def build(self, **_: object) -> None:
        pass

    def test(self, *, results_xml: str, **_: object) -> None:
        if self.results is not None:
            Path(results_xml).parent.mkdir(parents=True, exist_ok=True)
            Path(results_xml).write_text(self.results, encoding="utf-8")
        if self.code:
            raise SystemExit(self.code)


def _case(name: str, child: str = "") -> str:
    return f'<testcase name="{name}">{child}</testcase>'


def _xml(*cases: str) -> str:
    return f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>"


def _simulate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, results: str | None, code: int
) -> Report:
    monkeypatch.setattr("cocotb_tools.runner.get_runner", lambda _: _FakeRunner(results, code))
    module = tmp_path / "m.py"
    module.write_text(
        "@cocotb.test()\nasync def a(dut): ...\n@cocotb.test()\nasync def b(dut): ...\n",
        encoding="utf-8",
    )
    return simulate([], "top", module, tmp_path / "sim")


def test_clean_results_and_exit_zero_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _simulate(tmp_path, monkeypatch, _xml(_case("a"), _case("b")), 0).ok


def test_a_crash_after_clean_results_is_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The results are written before the simulator stops, so a fault in teardown leaves them
    clean and only the exit code says so."""
    report = _simulate(tmp_path, monkeypatch, _xml(_case("a"), _case("b")), 134)
    assert [f.rule for f in report.findings] == ["SIM-EXIT"]
    assert "134" in report.findings[0].message


def test_a_failing_test_is_not_also_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _simulate(tmp_path, monkeypatch, _xml(_case("a", "<failure/>"), _case("b")), 1)
    assert [f.rule for f in report.findings] == ["TEST-FAILED"]


def test_a_skipped_test_is_not_a_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _simulate(tmp_path, monkeypatch, _xml(_case("a", "<skipped/>"), _case("b")), 0)
    assert [f.rule for f in report.findings] == ["TEST-SKIPPED"]


def test_the_right_count_of_the_wrong_tests_is_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _simulate(tmp_path, monkeypatch, _xml(_case("a"), _case("c")), 0)
    assert [f.rule for f in report.findings] == ["TEST-SET"]


@pytest.mark.parametrize("code", [0, 1])
def test_no_results_is_an_error_whatever_the_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    with pytest.raises(RuntimeError, match="wrote no results"):
        _simulate(tmp_path, monkeypatch, None, code)


def test_bad_filter_regex_is_reported(tmp_path: Path) -> None:
    out = generate(load(DEMO), tmp_path, DEMO.parent)
    assert out.tests is not None
    result = CliRunner().invoke(main, ["sim", str(tmp_path), "--filter", "("])
    assert result.exit_code == 1 and "not a valid regex" in result.output


def test_generated_tests_refuse_another_version_of_the_tool(tmp_path: Path) -> None:
    from hdl_bag.tests_gen import route_tests

    with pytest.raises(RuntimeError, match=r"generated by hdl-bag 0\.0\.0-other"):
        route_tests.load_spec(DEMO, generated_by="0.0.0-other")
    out = generate(load(DEMO), tmp_path, DEMO.parent)
    assert out.tests is not None
    assert f'generated_by="{hdl_bag.__version__}"' in (tmp_path / out.tests).read_text(
        encoding="utf-8"
    )


def test_a_file_header_cannot_set_the_modules_encoding() -> None:
    from hdl_bag.tests_gen import render_tests

    text = render_tests(load(DEMO), "// coding: not-a-codec\n")
    compile(text.encode("utf-8"), "generated", "exec", dont_inherit=True)


@pytest.mark.eda
def test_flow_builds_the_model_when_there_are_no_tests(tmp_path: Path) -> None:
    cfg = TESTS_DIR / "configs" / "p3" / "external_fabric.ir.yaml"
    result = CliRunner().invoke(main, ["flow", str(cfg), "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "compile (verilator-build): 0 unwaived" in result.output
    assert "no generated tests" in result.output


def test_the_ready_shim_gates_every_axi_ready_into_the_design(tmp_path: Path) -> None:
    out = generate(load(DEMO), tmp_path, DEMO.parent)
    shim = tmp_path / "tests" / f"{ready_shim_module(out.top)}.sv"
    assert shim.relative_to(tmp_path).as_posix() in out.files
    text = shim.read_text(encoding="utf-8")
    spec = load(DEMO)
    axi_masters = [m for m in spec.masters if m.protocol in ("AXI4", "AXI4-Lite")]
    axi_slaves = [s for s in spec.slaves if s.protocol in ("AXI4", "AXI4-Lite")]
    gated = re.findall(r"\.(\w+)\((\w+) & (\w+)\)", text)
    assert len(gated) == 2 * len(axi_masters) + 3 * len(axi_slaves)
    for port, valid, ready in gated:
        assert port == ready and valid == ready.removesuffix("ready") + "valid"
    # The same ports as the top, so the models bind to it unchanged.
    top = (tmp_path / "rtl" / f"{out.top}.sv").read_text(encoding="utf-8")
    assert re.findall(r"^\s+(?:input|output)\s.*$", text, re.M) == re.findall(
        r"^\s+(?:input|output)\s.*$", top, re.M
    )
    assert re.search(rf"\b{out.top} i_dut \(", text)


def test_a_design_with_no_axi_port_gets_no_ready_shim(tmp_path: Path) -> None:
    cfg = TESTS_DIR / "configs" / "p2" / "apb4_min.ir.yaml"
    out = generate(load(cfg), tmp_path, cfg.parent)
    assert out.tests is not None
    assert not (tmp_path / "tests" / f"{ready_shim_module(out.top)}.sv").exists()
    result = CliRunner().invoke(main, ["sim", str(tmp_path), "--ready-shim"])
    assert result.exit_code == 1 and "no READY shim" in result.output
