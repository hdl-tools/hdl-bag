# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The generated cocotb tests: what `bag generate` writes, and which tests apply."""

import ast
import asyncio
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from cocotbext.axi import AxiProt
from dsl import APB4_NA, AXI4_NA, AXIL_NA, Cfg, resolve_cfg

import hdl_bag.tests_gen
from hdl_bag.cli import main
from hdl_bag.flows import Report, run_tests, simulate
from hdl_bag.gen import generate, load_outputs, sources
from hdl_bag.ir import Spec, load
from hdl_bag.tests_gen import SHIM_TEST, TESTS, applicable_tests, ready_shim_module
from hdl_bag.tests_gen.bfm_axi import AxiPortMaster
from hdl_bag.tests_gen.route_model import (
    access_bytes,
    blocking_path,
    cache_after_tie,
    midpoint_path,
    split_error_word,
)

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


def test_the_ready_shim_refuses_a_filter(tmp_path: Path) -> None:
    assert CliRunner().invoke(main, ["generate", str(DEMO), "-o", str(tmp_path)]).exit_code == 0
    result = CliRunner().invoke(main, ["sim", str(tmp_path), "--ready-shim", "--filter", "x"])
    assert result.exit_code == 1 and "does not take a filter" in result.output


def test_every_ready_shim_config_has_its_test() -> None:
    shim_configs = (
        "axi_shared_rr",
        "axil_xbar_rules",
        "axi_adapters",
        "bridges_inhouse",
        "bridges_pulp",
    )
    for config in shim_configs:
        assert SHIM_TEST in applicable_tests(
            load(TESTS_DIR / "configs" / "p3" / f"{config}.ir.yaml")
        )
    assert SHIM_TEST in applicable_tests(load(DEMO))


def test_head_of_line_runs_on_both_topologies() -> None:
    """The test applies only where a path exists, so a config edit could drop one side of it."""
    seen = set()
    for config in sorted((TESTS_DIR / "configs" / "p3").glob("*.ir.yaml")):
        spec = load(config)
        seen |= {f.topology for f in spec.fabrics if blocking_path(spec, f)}
    assert seen == {"crossbar", "shared-bus"}


def _narrow_master(dw: int, bridged: bool) -> Cfg:
    """An APB4 master of DW bits on an APB4 fabric, whose slave is on that fabric or, BRIDGED,
    behind a bag_apb2axi bridge on a 32-bit AXI4-Lite fabric."""
    c = Cfg(f"narrow{dw}{'b' if bridged else ''}", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("p", "APB4", dw, 32, "D0")
    c.master("m0", "APB4", dw, 32, "D0").link("m0", "p", 0).decode("m0", "PSLVERR")
    c.option("m0", **APB4_NA)
    if not bridged:
        c.slave("s0", "APB4", dw, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
        return c.region("s0", "regs", 0x4000_0000, 0x1000)
    c.fabric("l", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.bridge("br", 0x4000_0000, template="bag_apb2axi")
    c.link("p", "br", 0).link("br", "l", 0).decode("br", "DECERR").option("br", **AXIL_NA)
    c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("l", "s0", 0).option("s0", **AXIL_NA)
    c.region("br", "win", 0x4000_0000, 0x1_0000)
    return c.region("s0", "regs", 0x4000_0000, 0x1000)


@pytest.mark.parametrize("bridged", [False, True])
@pytest.mark.parametrize("dw", [8, 16])
def test_a_narrow_master_config_is_legal(tmp_path: Path, dw: int, bridged: bool) -> None:
    spec, findings = resolve_cfg(_narrow_master(dw, bridged), tmp_path)
    assert findings == [] and spec is not None


@pytest.mark.eda
@pytest.mark.parametrize("bridged", [False, True])
@pytest.mark.parametrize("dw", [8, 16])
def test_the_generated_tests_pass_on_a_master_narrower_than_a_word(
    tmp_path: Path, dw: int, bridged: bool
) -> None:
    """First nightly (run 44188540781): the tests wrote 4 bytes through 8-bit and 16-bit APB
    masters, which carry 1 or 2, and failed 15 of 50 random seeds on a correct design."""
    spec, findings = resolve_cfg(_narrow_master(dw, bridged), tmp_path)
    assert findings == [] and spec is not None
    report = run_tests(generate(spec, tmp_path / "out", tmp_path))
    assert [f.format() for f in report.unwaived] == []


@pytest.mark.parametrize(("width", "size"), [(8, 1), (16, 2), (32, 4), (64, 4), (128, 4)])
def test_a_test_access_is_a_word_or_all_a_narrow_master_can_carry(width: int, size: int) -> None:
    assert access_bytes(SimpleNamespace(data_width=width)) == size


def _shared_bus(tmp_path: Path, master_slice: dict[str, str], slave_slice: dict[str, str]) -> Spec:
    """An AXI4 shared bus (mid-point FIFO 2) with masters m0 (ID 4 bits) and m1 (ID 1 bit) and
    slaves s0 and s1; the named ports have that register slice, so an adapter in front."""
    c = Cfg("midpt", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("b", "AXI4", 32, 32, "D0", "round-robin", topology="shared-bus", midpoint=(2, "none"))
    for k, idw in enumerate((4, 1)):
        name = f"m{k}"
        c.master(name, "AXI4", 32, 32, "D0", id_width=idw).link(name, "b", k).decode(name, "DECERR")
        c.option(name, slice=master_slice.get(name, "none"), **AXI4_NA)
    for k in range(2):
        name = f"s{k}"
        c.slave(name, "AXI4", 32, 32, "D0").link("b", name, k)
        c.region(name, "mem", 0x4000_0000 + k * 0x1_0000, 0x1_0000)
        c.option(name, slice=slave_slice.get(name, "none"), **AXI4_NA)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    return spec


def test_the_midpoint_test_reads_through_ports_with_no_adapter(tmp_path: Path) -> None:
    """First nightly: a register slice or FIFO beside the mid-point holds reads of its own, so
    a count of what the mid-point holds is only right with no adapter on the way."""
    clean = midpoint_path(s := _shared_bus(tmp_path, {}, {}), s.fabrics[0])
    assert clean is not None and clean[0].name == "m0" and clean[2] == 2
    # m0 has the widest ID but a slice in front: m1 is the master to measure with.
    found = midpoint_path(s := _shared_bus(tmp_path, {"m0": "cut"}, {}), s.fabrics[0])
    assert found is not None and found[0].name == "m1"
    # s0 has a slice: the words are s1's.
    found = midpoint_path(s := _shared_bus(tmp_path, {}, {"s0": "cut"}), s.fabrics[0])
    assert found is not None and all(0x4001_0000 <= a < 0x4002_0000 for a in found[1])


def test_the_midpoint_test_does_not_apply_when_every_path_has_an_adapter(tmp_path: Path) -> None:
    s = _shared_bus(tmp_path, {"m0": "cut", "m1": "cut"}, {})
    assert midpoint_path(s, s.fabrics[0]) is None
    s = _shared_bus(tmp_path, {}, {"s0": "cut", "s1": "cut"})
    assert midpoint_path(s, s.fabrics[0]) is None


@pytest.mark.parametrize(("pslverr", "applies"), [("drive", True), ("tie-0", False)])
def test_the_split_error_test_needs_a_slave_that_can_answer_an_error(
    tmp_path: Path, pslverr: str, applies: bool
) -> None:
    """First nightly: a slave with PSLVERR tied never reports an error, by definition."""
    c = Cfg("split", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("p", "APB4", 16, 32, "D0")
    c.master("m0", "APB4", 16, 32, "D0").link("m0", "p", 0).decode("m0", "PSLVERR")
    c.slave("s0", "APB4", 16, 32, "D0").link("p", "s0", 0)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    c.option("m0", **APB4_NA).option("s0", **{**APB4_NA, "pslverr": pslverr})
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    assert (split_error_word(spec, spec.masters[0], spec.slaves[0]) is not None) is applies
    name = "an_error_on_any_beat_of_a_split_access_reaches_the_master"
    assert (name in applicable_tests(spec)) is applies


class _Recorder:
    """Stands in for cocotbext-axi's master and keeps the keywords of each call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def write(self, addr: int, data: bytes, **kw: object) -> SimpleNamespace:
        self.calls.append(kw)
        return SimpleNamespace(resp=0)

    async def read(self, addr: int, length: int, **kw: object) -> SimpleNamespace:
        self.calls.append(kw)
        return SimpleNamespace(resp=0, data=bytes(length))


def test_a_burst_is_a_secure_access_like_every_other_helper() -> None:
    """Seed 029: cocotbext-axi's default PROT is non-secure, so a burst to a secure-only
    region, which the paths are chosen as a secure access, was answered DECERR."""
    port = AxiPortMaster.__new__(AxiPortMaster)
    recorder = _Recorder()
    port.master, port.has_prot = recorder, True
    asyncio.run(port.burst_write(0x40, bytes(8)))
    asyncio.run(port.burst_read(0x40, 8))
    assert [c["prot"] for c in recorder.calls] == [AxiProt(0), AxiProt(0)]


@pytest.mark.parametrize(
    ("option", "cache"), [("drive", 0b11), ("tie-0", 0), ("tie-default", 0b11)]
)
def test_a_cache_tie_sets_the_axcache_a_port_sends(option: str, cache: int) -> None:
    """Seed 022: a port with CACHE tied to 0 sends AxCACHE 0, whatever its master drives."""
    spec = SimpleNamespace(port_options=[SimpleNamespace(port="m0", cache=option)])
    assert cache_after_tie(spec, "m0", 0b11) == cache  # type: ignore[arg-type]


@pytest.mark.eda
def test_the_route_test_passes_when_no_slave_can_answer_an_error(tmp_path: Path) -> None:
    """Seed 004: with PSLVERR tied on the only slave no access can see a slave error, so the
    route test must not require one."""
    c = _narrow_master(16, False)
    c.data.port_options = [r for r in c.data.port_options if r[0] != "s0"]
    c.option("s0", **{**APB4_NA, "pslverr": "tie-0"})
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    report = run_tests(generate(spec, tmp_path / "out", tmp_path))
    assert [f.format() for f in report.unwaived] == []
