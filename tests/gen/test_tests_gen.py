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
from dsl import APB3_NA, APB4_NA, AXI4_NA, AXIL_NA, Cfg, resolve_cfg

import hdl_bag.tests_gen
from hdl_bag.cli import main
from hdl_bag.flows import Report, run_tests, simulate
from hdl_bag.gen import generate, load_outputs, sources
from hdl_bag.ir import Spec, load, resolve
from hdl_bag.regress.dsl import AHB_MASTER_NA, AHB_SLAVE_NA
from hdl_bag.tests_gen import SHIM_TEST, TESTS, applicable_tests, ready_shim_module
from hdl_bag.tests_gen.bfm_axi import AxiPortMaster
from hdl_bag.tests_gen.route_model import (
    TRAFFIC_HPROT,
    Outcome,
    _axi4_downsizer_in_front,
    _splitters,
    access_bytes,
    attributes,
    blocking_path,
    burst_path,
    burst_shapes,
    byte_write_lands,
    cache_after_tie,
    check_read,
    check_write,
    error_words,
    midpoint_buffers,
    midpoint_path,
    midpoint_reads,
    narrow_apb_path,
    plain_word,
    pstrb_path,
    reads_per_word,
    refused_access,
    rewrites_words,
    route,
    saw_access,
    serialises_writes,
    serves_by_priority,
    split_error_word,
    split_refused,
    writes_whole_words,
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


def _behind_lite64(kind: str) -> Cfg:
    """A 32-bit APB4 master reaching slave s0 (region 0x4000_0000, 4 KiB) over a 64-bit AXI4-Lite
    fabric, so each read is a full 64-bit beat. KIND says what s0 is and how it is reached."""
    c = Cfg(f"lite64{kind}", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("p", "APB4", 32, 32, "D0")
    c.master("m0", "APB4", 32, 32, "D0").link("m0", "p", 0).decode("m0", "PSLVERR")
    c.option("m0", **APB4_NA)
    c.fabric("l", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.bridge("br", 0x4000_0000, template="bag_apb2axi")
    c.link("p", "br", 0).link("br", "l", 0).decode("br", "DECERR").option("br", **AXIL_NA)
    c.region("br", "win", 0x4000_0000, 0x1_0000)
    if kind == "axi4":
        c.slave("s0", "AXI4", 32, 32, "D0").link("l", "s0", 0).option("s0", **AXI4_NA)
    elif kind == "lite":
        c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("l", "s0", 0).option("s0", **AXIL_NA)
    else:
        dw = int(kind[3:])
        proto, template = (
            ("AHB-Lite", "bag_axi2ahb") if kind[:3] == "ahb" else ("APB4", "bag_axi2apb")
        )
        c.fabric("d", proto, dw, 32, "D0")
        c.bridge("b2", 0x4000_0000, template=template)
        c.link("l", "b2", 0).link("b2", "d", 0).decode(
            "b2", "HRESP_ERROR" if proto != "APB4" else "PSLVERR"
        )
        c.option("b2", **(AHB_MASTER_NA if proto != "APB4" else APB4_NA))
        c.region("b2", "win2", 0x4000_0000, 0x1_0000)
        na = AHB_SLAVE_NA if proto != "APB4" else APB4_NA
        c.slave("s0", proto, dw, 32, "D0").link("d", "s0", 0).option("s0", **na)
    return c.region("s0", "regs", 0x4000_0000, 0x1000)


@pytest.mark.parametrize(
    ("kind", "whole_beat"),
    [("axi4", False), ("ahb32", False), ("apb8", False), ("apb16", True), ("apb32", True),
     ("lite", True), ("ahb64", True)],
)  # fmt: skip
def test_a_read_of_the_last_word_meets_the_error_word_only_if_its_beat_is_read_whole(
    tmp_path: Path, kind: str, whole_beat: bool
) -> None:
    """Seeds 005 and 009: an AXI4 downsizer starts at the address, so a 64-bit beat read at the
    last word never reads the error word below it. The AXI4-Lite downsizer reads the whole beat,
    and a 64-bit slave answers the whole beat in one word. Seeds 024, 030 and 038: from AXI4-Lite
    a bag_axi2apb instance is bag_axil2apb, on that AXI4-Lite downsizer, except onto 8 bits."""
    spec, findings = resolve_cfg(_behind_lite64(kind), tmp_path)
    assert findings == [] and spec is not None
    assert route(spec, "m0", 0x4000_0FFC, False, False, {}).slave_error is whole_beat


def test_an_axi4_master_on_an_axi4_lite_fabric_reads_whole_beats(tmp_path: Path) -> None:
    """Seed 037: the protocol converter turns any read into a full beat of the fabric."""
    c = Cfg("pcbeat", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("l", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 64, 32, "D0").link("m0", "l", 0).decode("m0", "DECERR")
    c.option("m0", **AXI4_NA)
    c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("l", "s0", 0).option("s0", **AXIL_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    first = route(spec, "m0", 0x4000_0000, False, False, {})
    assert first.words == {0x4000_0000 // 4, 0x4000_0004 // 4}
    assert route(spec, "m0", 0x4000_0FFF, False, False, {}).slave_error


@pytest.mark.parametrize(
    ("protocol", "seen", "write", "matches"),
    [("AHB-Lite", 0xFFF8, False, True), ("AHB-Lite", 0xFFFC, False, True),
     ("AHB-Lite", 0xFFF0, False, False), ("AHB-Lite", 0xFFF8, True, False),
     ("AHB-Lite", 0xFFFC, True, True), ("AXI4", 0xFFF8, False, False),
     ("AXI4", 0xFFFC, False, True)],
)  # fmt: skip
def test_a_wide_slave_sees_a_read_at_the_start_of_its_word(
    protocol: str, seen: int, write: bool, matches: bool
) -> None:
    """Five seeds: a full-size AHB transfer must be aligned, so a 64-bit slave logs a read of
    0xFFFC at 0xFFF8. A write keeps its exact address."""
    exp = SimpleNamespace(slave="s0", address=0xFFFC, word_bytes=8, bridges=())
    spec = SimpleNamespace(slaves=[SimpleNamespace(name="s0", protocol=protocol)])
    assert saw_access(spec, exp, seen, write) is matches  # type: ignore[arg-type]


def test_a_read_that_crossed_ahb_arrives_aligned_at_any_slave() -> None:
    """Seed 046: an AHB-Lite fabric on the way aligns the transfer before an AXI4-Lite slave."""
    exp = SimpleNamespace(slave="s0", address=0xFFFC, word_bytes=8, bridges=("br0",))
    spec = SimpleNamespace(
        slaves=[SimpleNamespace(name="s0", protocol="AXI4-Lite")],
        bridges=[SimpleNamespace(name="br0", downstream="f1")],
        fabrics=[SimpleNamespace(name="f1", protocol="AHB-Lite")],
    )
    assert saw_access(spec, exp, 0xFFF8, False)  # type: ignore[arg-type]
    assert not saw_access(spec, exp, 0xFFF8, True)  # type: ignore[arg-type]


_WORD = Outcome("s0", 0x40, word_bytes=2)


@pytest.mark.parametrize(
    ("written", "whole_word", "rewrites", "accepted"),
    [
        ([(0x40, 0x7B)], False, False, True),
        ([(0x40, 0x7B), (0x41, 0x00)], False, False, False),  # a neighbour, not asked for
        ([(0x40, 0x7B), (0x41, 0x00)], True, False, True),  # seed 019: the tie stores the word
        ([(0x40, 0x7B), (0x42, 0x00)], True, False, False),  # outside the word
        ([(0x40, 0x11), (0x41, 0x00)], True, False, False),  # the wrong value
        ([(0x41, 0x00)], True, False, False),  # the byte itself is missing
        ([(0x40, 0x7B), (0x40, 0x7B)], False, False, False),  # twice
        ([(0x40, 0x7B), (0x40, 0x7B)], False, True, True),  # seed 048: one write per strobe
        ([(0x40, 0x7B), (0x40, 0x11), (0x40, 0x7B)], False, True, False),  # two values
    ],
)  # fmt: skip
def test_a_write_stores_what_its_path_can_express(
    written: list[tuple[int, int]], whole_word: bool, rewrites: bool, accepted: bool
) -> None:
    """A tied PSTRB stores the whole slave word, and bag_axi2ahb repeats a word onto a slave with
    no strobes; neither licenses a wrong value or a byte outside the word."""
    if accepted:
        check_write("w", _WORD, b"\x7b", written, whole_word=whole_word, rewrites=rewrites)
    else:
        with pytest.raises(AssertionError):
            check_write("w", _WORD, b"\x7b", written, whole_word=whole_word, rewrites=rewrites)


@pytest.mark.parametrize(
    ("protocol", "template", "whole", "rewrites"),
    [("APB3", "bag_axi2ahb", True, True), ("APB3", "bag_ahb2apb", True, False),
     ("APB4", "bag_axi2ahb", False, False), ("AXI4", "bag_axi2ahb", False, False)],
)  # fmt: skip
def test_a_path_says_how_it_stores_a_partial_write(
    protocol: str, template: str, whole: bool, rewrites: bool
) -> None:
    slave = SimpleNamespace(name="s0", protocol=protocol, data_width=32)
    bridge = SimpleNamespace(name="br0", template=template, downstream="f1")
    fabric = SimpleNamespace(name="f1", protocol="AXI4", data_width=32)  # not an APB3 hop
    spec = SimpleNamespace(slaves=[slave], bridges=[bridge], fabrics=[fabric], port_options=[])
    exp = SimpleNamespace(slave="s0", bridges=("br0",))
    assert writes_whole_words(spec, exp) is whole  # type: ignore[arg-type]
    assert rewrites_words(spec, exp) is rewrites  # type: ignore[arg-type]


@pytest.mark.parametrize(("pstrb", "applies"), [("drive", True), ("tie-default", False)])
def test_the_unaligned_write_test_needs_a_path_that_keeps_strobes(
    tmp_path: Path, pstrb: str, applies: bool
) -> None:
    """Seed 022: a bridge with PSTRB tied stores whole words, so an unaligned write through it
    cannot store only its own bytes."""
    c = Cfg("strobes", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXI4_NA)
    c.fabric("p", "APB4", 32, 32, "D0")
    c.bridge("br", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "br", 0).link("br", "p", 0).decode("br", "PSLVERR")
    c.option("br", **{**APB4_NA, "pstrb": pstrb})
    c.region("br", "win", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    name = "unaligned_axi_writes_store_only_their_bytes"
    assert (name in applicable_tests(spec)) is applies
    # Seed 202611220001: the PSTRB byte-write test needs a path that keeps strobes too.
    assert (pstrb_path(spec, spec.masters[0], spec.slaves[0]) is not None) is applies
    # Seeds 202611240002, 034: so does the narrow APB test's byte write.
    found = plain_word(spec, spec.masters[0], spec.slaves[0])
    assert found is not None
    assert byte_write_lands(spec, spec.slaves[0], found[1]) is applies


def test_a_read_repeats_a_word_only_where_its_path_allows() -> None:
    exp = Outcome("s0", 0, word_bytes=8, words=frozenset({0}))
    check_read("r", exp, 4, [0, 0], repeats=2)
    with pytest.raises(AssertionError, match="read an address twice"):
        check_read("r", exp, 4, [0, 0])
    with pytest.raises(AssertionError, match="read an address twice"):
        check_read("r", exp, 4, [0, 0, 0], repeats=2)  # review: a bound, not a switch


@pytest.mark.parametrize(("middle", "repeats"), [(16, 4), (32, 2), (64, 1), (128, 1)])
def test_a_bus_narrower_than_the_slave_can_repeat_a_read(middle: int, repeats: int) -> None:
    """Seed 016: a 64-bit read crossing a 32-bit bus becomes two transfers, and each becomes a
    full 64-bit beat again on the AXI4-Lite fabric behind it, reading the slave's word twice."""
    port = SimpleNamespace(port="m0")
    spec = SimpleNamespace(
        fabrics=[SimpleNamespace(name="f0", data_width=64, master_ports=[port]),
                 SimpleNamespace(name="f1", data_width=middle, master_ports=[])],
        bridges=[SimpleNamespace(name="br0", downstream="f1")],
    )  # fmt: skip
    exp = SimpleNamespace(bridges=("br0",), word_bytes=8, span=4)
    m = SimpleNamespace(name="m0", data_width=64, protocol="AXI4")
    assert reads_per_word(spec, m, exp) == repeats  # type: ignore[arg-type]


def test_a_read_split_on_the_way_reaches_a_narrow_slave_once_per_transfer() -> None:
    """Seed 202611240040: a 4-byte read crosses a 16-bit APB3 bus as two transfers; a 64-bit
    AXI4-Lite bus behind widens each to a full beat, which bag_axil2apb reads from a 16-bit
    slave in four transfers. So every slave word is read twice, though the slave is no wider
    than the narrow bus."""
    assert reads_per_word(*_split_read(widening=True)) == 2  # type: ignore[arg-type]


def test_a_read_split_with_no_wider_bus_behind_reaches_each_word_once() -> None:
    """Review of #202: with no wider bus after the narrow one, each split transfer reaches its
    own slave word once, so a repeat there is a design reading a register twice."""
    assert reads_per_word(*_split_read(widening=False)) == 1  # type: ignore[arg-type]


def _split_read(widening: bool) -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    """A 64-bit master's 4-byte read onto a 16-bit bus, then (WIDENING) a 64-bit bus, to a
    16-bit slave: (spec, master, outcome) for reads_per_word."""
    port = SimpleNamespace(port="m0")
    fabrics = [SimpleNamespace(name="f0", data_width=64, master_ports=[port]),
               SimpleNamespace(name="f1", data_width=16, master_ports=[]),
               SimpleNamespace(name="f2", data_width=64, master_ports=[])]  # fmt: skip
    bridges = [SimpleNamespace(name="br0", downstream="f1"),
               SimpleNamespace(name="br1", downstream="f2")]  # fmt: skip
    spec = SimpleNamespace(fabrics=fabrics, bridges=bridges)
    exp = SimpleNamespace(bridges=("br0", "br1") if widening else ("br0",), word_bytes=2, span=4)
    return spec, SimpleNamespace(name="m0", data_width=64, protocol="AHB-Lite"), exp


@pytest.mark.parametrize(
    ("cache", "size", "addr", "beats", "width", "refused"),
    [
        (0b11, 4, 0, 1, 1, False),  # modifiable: split freely
        (0b00, 4, 0, 1, 1, True),  # seed 032: four bytes onto an 8-bit bus
        (0b01, 4, 0, 1, 1, True),  # AxCACHE[1] decides, not bufferable
        (0b00, 4, 0, 1, 4, False),  # one beat: nothing to split
        (0b00, 4, 2, 1, 2, False),  # the downsizer starts at the address: one beat left
        (0b00, 1, 0, 1, 1, False),
        (0b00, 4, 0, 4, 4, True),  # seed 022: an INCR burst
        (0b00, 4, 0, 17, 4, True),  # AxLEN 16
        (0b00, 4, 0, 18, 4, False),  # AxLEN 17: AXI allows splitting so long a burst
    ],
)  # fmt: skip
def test_a_non_modifiable_transaction_is_refused_where_it_would_be_split(
    cache: int, size: int, addr: int, beats: int, width: int, refused: bool
) -> None:
    """D104: PULP axi_burst_splitter's txn_supported, after axi_dw_downsizer's beat count."""
    assert split_refused(cache, size, addr, beats, width) is refused


def _tied_cache(cache: str) -> Cfg:
    """An AXI4 master with CACHE as given, reaching an 8-bit APB4 slave through bag_axi2apb."""
    c = Cfg(f"cache{cache.replace('-', '')}", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **{**AXI4_NA, "cache": cache})
    c.fabric("p", "APB4", 8, 32, "D0")
    c.bridge("br", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "br", 0).link("br", "p", 0).decode("br", "PSLVERR").option("br", **APB4_NA)
    c.region("br", "win", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 8, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    return c.region("s0", "regs", 0x4000_0000, 0x1000)


@pytest.mark.parametrize(
    ("cache", "refused"), [("drive", False), ("tie-0", True), ("tie-default", False)]
)
def test_a_tied_cache_makes_a_path_refuse_what_it_must_split(
    tmp_path: Path, cache: str, refused: bool
) -> None:
    """Seeds 022, 027, 032 (D104): CACHE tie-0 makes every access non-modifiable, so a word onto
    an 8-bit bus is answered SLVERR. The tests that need clean traffic keep off such a path."""
    spec, findings = resolve_cfg(_tied_cache(cache), tmp_path)
    assert findings == [] and spec is not None
    m, s, word = spec.masters[0], spec.slaves[0], 0x4000_0040
    exp = route(spec, "m0", word, True, False, {})
    assert exp.may_refuse is refused
    assert refused_access(spec, m, exp, 0, False, word, 4) is refused
    assert refused_access(spec, m, exp, 0, False, word, 1) is False
    assert (plain_word(spec, m, s) is None) is refused


@pytest.mark.eda
def test_the_generated_tests_pass_when_a_tied_cache_refuses_splits(tmp_path: Path) -> None:
    """Seeds 022, 027 and 032 (D104), on a correct design."""
    spec, findings = resolve_cfg(_tied_cache("tie-0"), tmp_path)
    assert findings == [] and spec is not None
    report = run_tests(generate(spec, tmp_path / "out", tmp_path))
    assert [f.format() for f in report.unwaived] == []


def test_concurrent_traffic_has_time_for_a_long_chain_of_bridges() -> None:
    """Seed 046: every master's words over four bridges and clock crossings took 56 us against
    a limit of 50, and passed with a longer one. The limit is there to catch a hang."""
    limit = next(t.timeout_us for t in TESTS if t.name.startswith("concurrent_traffic"))
    assert limit >= 500


@pytest.mark.parametrize(
    ("midpoint", "buffers"),
    [(None, False), ((0, "none"), False), ((0, "multicut-2"), True), ((2, "none"), True)],
)
def test_a_midpoint_that_holds_requests_decides_their_order(
    midpoint: tuple[int, str] | None, buffers: bool
) -> None:
    """Seed 037: behind a multicut-2 mid-point writes left in the order they arrived, shortest
    adapter chain first, so a fixed-priority round cannot be judged by priority there."""
    mid = None if midpoint is None else SimpleNamespace(fifo_depth=midpoint[0], slice=midpoint[1])
    assert midpoint_buffers(SimpleNamespace(midpoint=mid)) is buffers


@pytest.mark.parametrize(("pstrb", "narrow"), [("drive", True), ("tie-default", False)])
def test_a_narrow_burst_needs_a_path_that_keeps_strobes(
    tmp_path: Path, pstrb: str, narrow: bool
) -> None:
    """Seed 022: behind a bridge with PSTRB tied a narrow beat stores the whole word."""
    c = Cfg("nstrobes", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXI4_NA)
    c.fabric("p", "APB4", 32, 32, "D0")
    c.bridge("br", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "br", 0).link("br", "p", 0).decode("br", "PSLVERR")
    c.option("br", **{**APB4_NA, "pstrb": pstrb})
    c.region("br", "win", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    shapes = [shape[0] for shape in burst_shapes(spec, spec.masters[0], spec.slaves[0])]
    assert any(name.startswith("narrow") for name in shapes) is narrow


def test_a_beat_wider_than_a_downsizer_in_front_of_a_converter_is_refused(tmp_path: Path) -> None:
    """Review: the downsizer makes two beats of a 64-bit transfer on its way to a 32-bit
    axi_to_axi_lite, which refuses them when non-modifiable."""
    c = Cfg("dwpc", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 64, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 64, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **{**AXI4_NA, "cache": "tie-0"})
    c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("a", "s0", 0).option("s0", **AXIL_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000).wrap_refused("m0", "a_dw_2_s0", "s0")
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0040, False, False, {})
    assert refused_access(spec, spec.masters[0], exp, 0, False, 0x4000_0040, 8)
    assert not refused_access(spec, spec.masters[0], exp, 0, False, 0x4000_0040, 4)


def _far_side_adapter(adapter: SimpleNamespace) -> SimpleNamespace:
    """A bridge br0 from a 64-bit AHB-Lite fabric f0 to fabric f1, with ADAPTER on its f1 end."""
    port = SimpleNamespace(port="m0")
    return SimpleNamespace(
        masters=[SimpleNamespace(name="m0", protocol="AHB-Lite", data_width=64)],
        fabrics=[SimpleNamespace(name="f0", protocol="AHB-Lite", data_width=64,
                                 master_ports=[port]),
                 SimpleNamespace(name="f1", protocol="AXI4-Lite", data_width=64, master_ports=[])],
        bridges=[SimpleNamespace(name="br0", template="bag_ahb2axi", downstream="f1")],
        adapters=[adapter],
    )  # fmt: skip


def test_a_converter_on_a_bridges_far_end_is_on_the_path() -> None:
    """Review: a bridge is a port of both its fabrics, and has adapters on both."""
    pc = SimpleNamespace(port="br0", fabric="f1", chain_index=0, type="pc",
                         params={"from": "AXI4", "to": "AXI4-Lite"})  # fmt: skip
    spec = _far_side_adapter(pc)
    assert _splitters(spec, "m0", ("br0",), "s0") == [(1, 8, 8)]  # type: ignore[arg-type]


def test_a_downsizer_on_a_bridges_far_end_starts_at_the_address() -> None:
    dw = SimpleNamespace(port="br0", fabric="f1", chain_index=0, type="dw",
                         params={"from": 64, "to": 32})  # fmt: skip
    fabric = SimpleNamespace(name="f1", protocol="AXI4")
    spec = SimpleNamespace(adapters=[dw])
    assert _axi4_downsizer_in_front(spec, "br0", fabric, from_port=True)  # type: ignore[arg-type]


def test_ahb_traffic_is_modifiable_so_its_paths_stay_tested(tmp_path: Path) -> None:
    """Review: counting every HPROT made each AHB-Lite path through axi_to_axi_lite refusable,
    which dropped it from the traffic, reset and ordering tests. Their traffic is cacheable."""
    assert TRAFFIC_HPROT >> 3 & 1
    c = Cfg("ahbclean", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("h", "AHB-Lite", 32, 32, "D0")
    c.master("m0", "AHB-Lite", 32, 32, "D0").link("m0", "h", 0).decode("m0", "HRESP_ERROR")
    c.option("m0", **AHB_MASTER_NA)
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.bridge("b1", 0x4000_0000, template="bag_ahb2axi")
    c.link("h", "b1", 0).link("b1", "a", 0).decode("b1", "DECERR").option("b1", **AXI4_NA)
    c.region("b1", "win1", 0x4000_0000, 0x1_0000)
    c.fabric("p", "APB4", 32, 32, "D0")
    c.bridge("b2", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "b2", 0).link("b2", "p", 0).decode("b2", "PSLVERR").option("b2", **APB4_NA)
    c.region("b2", "win2", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    assert plain_word(spec, spec.masters[0], spec.slaves[0]) is not None


@pytest.mark.parametrize(
    ("midpoint", "direct", "same_clock", "by_priority"),
    [(None, False, True, True), ((0, "cut"), True, True, True), ((0, "cut"), False, True, False),
     (None, False, False, False), (None, True, False, True)],
)  # fmt: skip
def test_a_round_is_judged_by_priority_where_every_request_meets_the_arbiter(
    midpoint: tuple[int, str] | None, direct: bool, same_clock: bool, by_priority: bool
) -> None:
    """Review: with no adapters every request arrives together, so even a buffering mid-point
    takes them in priority order; only unequal adapter chains make arrival order decide. Behind
    adapters the test aligns the starts, which a clock crossing defeats: the arrival cycle then
    depends on the phase (#204 review)."""
    mid = None if midpoint is None else SimpleNamespace(fifo_depth=midpoint[0], slice=midpoint[1])
    assert serves_by_priority(SimpleNamespace(midpoint=mid), direct, same_clock) is by_priority


def test_a_master_behind_a_width_converter_sends_its_writes_one_at_a_time() -> None:
    """Seed 202611900021: PULP's width converters take an AW only once the last write's W has
    left, so the arbiter sees gaps in such a master's queue; the fixed-priority starvation
    check then starves with a master whose writes stay queued."""
    f = SimpleNamespace(name="f0")
    spec = SimpleNamespace(adapters=[
        SimpleNamespace(fabric="f0", port="m1", template="bag_pc"),
        SimpleNamespace(fabric="f0", port="m1", template="bag_dw"),
        SimpleNamespace(fabric="f0", port="m0", template="bag_cut"),
        SimpleNamespace(fabric="f1", port="m2", template="bag_dw"),
    ])  # fmt: skip
    assert serialises_writes(spec, f, "m1")  # type: ignore[arg-type]
    assert not serialises_writes(spec, f, "m0")  # type: ignore[arg-type]
    assert not serialises_writes(spec, f, "m2")  # type: ignore[arg-type]
    route = ROUTE.read_text(encoding="utf-8")
    assert "if not serialises_writes(SPEC, f, p)), first)" in route


@pytest.mark.parametrize(
    ("protocol", "max_trans", "id_width", "expected"),
    [("AXI4", 1, 4, 1), ("AXI4", 2, 4, 6), ("AXI4", 4, 0, 4), ("AXI4-Lite", 2, 0, 2)],
)  # fmt: skip
def test_a_guard_with_one_transaction_lets_one_read_through(
    protocol: str, max_trans: int, id_width: int, expected: int
) -> None:
    """Seed 202611220012: with Max mst trans 1 the guard's axi_demux_simple has 1-bit ID
    counters, full after one read, and a full counter blocks every ID (D80), so a mid-point of
    six entries still holds one read."""
    f = SimpleNamespace(protocol=protocol, max_mst_trans=max_trans)
    m = SimpleNamespace(id_width=id_width)
    assert midpoint_reads(f, m, 6) == expected


def test_a_read_split_by_a_narrow_bus_keeps_the_words_below_the_address(tmp_path: Path) -> None:
    """Seed 202611220028: a 64-bit AXI4-Lite beat read at a region's last word is split by
    bag_axil2apb into 16-bit APB reads from the start of the beat; behind the next bridge each
    is its own transfer, so an AXI4 downsizer in front of the slave cannot trim the word below
    the address (it starts each transfer at that transfer's own address)."""
    c = Cfg("splitapb", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("p", "APB4", 32, 32, "D0")
    c.master("m0", "APB4", 32, 32, "D0").link("m0", "p", 0).decode("m0", "PSLVERR")
    c.option("m0", **APB4_NA)
    c.fabric("l", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.bridge("b1", 0x4000_0000, template="bag_apb2axi")
    c.link("p", "b1", 0).link("b1", "l", 0).decode("b1", "DECERR").option("b1", **AXIL_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("q", "APB4", 16, 32, "D0")
    c.bridge("b2", 0x4000_0000, template="bag_axi2apb")
    c.link("l", "b2", 0).link("b2", "q", 0).decode("b2", "PSLVERR").option("b2", **APB4_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.fabric("a", "AXI4", 64, 32, "D0", "round-robin")
    c.bridge("b3", 0x4000_0000, template="bag_apb2axi")
    c.link("q", "b3", 0).link("b3", "a", 0).decode("b3", "DECERR").option("b3", **AXI4_NA)
    c.region("b3", "w3", 0x4000_0000, 0x1_0000)
    c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("a", "s0", 0).option("s0", **AXIL_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.words == {(exp.address - 4) // 4, exp.address // 4} and exp.slave_error


def _through_apb3(tmp_path: Path) -> Spec:
    """AXI4 master m0, bag_axi2apb onto a 32-bit APB3 bus, bag_apb2axi onto an AXI4 bus with
    slave s0."""
    c = Cfg("apb3hop", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXI4_NA)
    c.fabric("p", "APB3", 32, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "b1", 0).link("b1", "p", 0).decode("b1", "PSLVERR").option("b1", **APB3_NA)
    c.region("b1", "win1", 0x4000_0000, 0x1_0000)
    c.fabric("x", "AXI4", 32, 32, "D0", "round-robin")
    c.bridge("b2", 0x4000_0000, template="bag_apb2axi")
    c.link("p", "b2", 0).link("b2", "x", 0).decode("b2", "DECERR").option("b2", **AXI4_NA)
    c.region("b2", "win2", 0x4000_0000, 0x1_0000)
    c.slave("s0", "AXI4", 32, 32, "D0").link("x", "s0", 0).option("s0", **AXI4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    return spec


def test_an_apb3_bus_on_the_way_drops_the_protection_bits(tmp_path: Path) -> None:
    """Seeds 202611220002, 016, 017, 037: APB3 has no PPROT, so the bridge after an APB3 bus
    drives its default, a non-secure data access (D28), whatever the master sent."""
    spec = _through_apb3(tmp_path)
    m = spec.masters[0]
    assert attributes(spec, m, 0b101, False, ("b1", "b2"))[:3] == (0, 1, 0)
    assert route(spec, "m0", 0x4000_0040, True, False, {}).nonsec


def test_an_apb3_bus_on_the_way_stores_whole_words(tmp_path: Path) -> None:
    """Seed 202611220017: APB3 has no PSTRB either, so a byte write crossing a 32-bit APB3 bus
    reaches the slave behind it as a whole word."""
    spec = _through_apb3(tmp_path)
    assert writes_whole_words(spec, route(spec, "m0", 0x4000_0040, True, False, {}))


def test_a_downsizer_on_a_bridges_far_end_trims_before_the_bus_counts_as_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #200: a 64-bit AXI4-Lite beat read at a region's last word, then a bridge whose
    far end narrows on AXI4 (forced here, as no workbook builds one today). That downsizer starts
    at the address, so the word below it, the region's error word, is not read; the bus behind
    is narrower than the untrimmed beat, but it must not count as having split it yet."""
    import hdl_bag.tests_gen.route_model as model

    spec, findings = resolve_cfg(_behind_lite64("ahb32"), tmp_path)
    assert findings == [] and spec is not None
    real = model._axi4_downsizer_in_front
    monkeypatch.setattr(model, "_downsizes_on_axi4", lambda b, up, down: False)
    monkeypatch.setattr(model, "_axi4_downsizer_in_front",
                        lambda s, port, fabric, from_port=False: (port == "b2" and from_port)
                        or real(s, port, fabric, from_port))  # fmt: skip
    assert not route(spec, "m0", 0x4000_0FFC, False, False, {}).slave_error


def test_the_reset_test_applies_only_where_its_body_finds_a_slave_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeds 202611240036, 048: the reset test applied because private_words found traffic, but
    its body resets around the slaves plain_word reaches, and found none (`assert reached`).
    Its predicate now uses the same helper as its body."""
    spec = load(DEMO)
    name = "reset_during_traffic_leaves_nothing_behind"
    assert name in applicable_tests(spec)
    monkeypatch.setattr(hdl_bag.tests_gen, "plain_word", lambda *a: None)
    assert name not in applicable_tests(spec)


def test_a_converter_on_the_masters_own_port_refuses_a_burst_to_a_miss_too(
    tmp_path: Path,
) -> None:
    """Seed 202611240024 (D104): an AXI4 master with CACHE tied to 0 on an AXI4-Lite bus has its
    own axi_to_axi_lite, whose burst splitter answers a 4-beat burst SLVERR before the address
    is decoded, so a burst to an unmapped address gets SLVERR, not DECERR."""
    c = Cfg("ownpc", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **{**AXI4_NA, "cache": "tie-0"})
    c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("a", "s0", 0).option("s0", **AXIL_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    m = spec.masters[0]
    miss = burst_path(spec, m, 0)[1]
    assert miss is not None
    exp = route(spec, "m0", miss, False, False, {})
    assert exp.slave is None
    assert refused_access(spec, m, exp, 0, False, miss, 4, beats=4)
    assert not refused_access(spec, m, exp, 0, False, miss, 4)


def test_a_slave_behind_a_translating_bridge_errors_at_the_word_its_region_ends_with(
    tmp_path: Path,
) -> None:
    """Seed 202611240025: behind a pass-through N bridge a slave sees addresses less the window
    base, but its error word was the region's system address, so no master ever reached it and
    the route test saw no slave error. The error word is now where the slave sees it."""
    c = Cfg("offseterr", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("p", "APB4", 32, 32, "D0")
    c.bridge("br", 0x4000_0000, pass_through=False, template="bag_axi2apb")
    c.link("a", "br", 0).link("br", "p", 0).decode("br", "PSLVERR").option("br", **APB4_NA)
    c.region("br", "win", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    assert error_words(spec, "s0") == {0xFF8}
    exp = route(spec, "m0", 0x4000_0FF8, True, False, {})
    assert (exp.slave, exp.address, exp.slave_error) == ("s0", 0xFF8, True)


def test_a_slave_with_no_region_has_no_plain_word() -> None:
    """The reset test's predicate asks plain_word of every pair at generate time, and a
    shrinker candidate may leave a slave without a region: that is no word, not a crash."""
    spec = load(DEMO)
    s = spec.slaves[0]
    bare = spec.model_copy(update={"memory_map": [r for r in spec.memory_map if r.slave != s.name]})
    assert plain_word(bare, bare.masters[0], s) is None


def test_a_read_that_crossed_ahb_starts_at_its_aligned_beat_at_a_later_downsizer(
    tmp_path: Path,
) -> None:
    """Seed 202611240029: a 64-bit AXI4-Lite read of the word at 0x...ffc crosses AHB-Lite as a
    64-bit transfer aligned to 0x...ff8, so the axi_dw_downsizer in front of an 8-bit APB slave
    further on starts there and reads the error word at 0x...ff8 too."""
    c = Cfg("ahbalign", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 64, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("h", "AHB-Lite", 64, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_axi2ahb")
    c.link("a", "b1", 0).link("b1", "h", 0).decode("b1", "HRESP_ERROR")
    c.option("b1", **AHB_MASTER_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("l", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.bridge("b2", 0x4000_0000, template="bag_ahb2axi")
    c.link("h", "b2", 0).link("b2", "l", 0).decode("b2", "DECERR").option("b2", **AXIL_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.fabric("p", "APB4", 8, 32, "D0")
    c.bridge("b3", 0x4000_0000, template="bag_axi2apb")
    c.link("l", "b3", 0).link("b3", "p", 0).decode("b3", "PSLVERR").option("b3", **APB4_NA)
    c.region("b3", "w3", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 8, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.slave == "s0" and 0x4000_0FF8 in exp.words and exp.slave_error
    # The same 64-bit beat, written with half its strobes, becomes one AHB write per byte, so
    # the narrow APB test (one access per APB word) leaves this path out.
    assert narrow_apb_path(spec, spec.masters[0], spec.slaves[0]) is None


def test_a_downsizer_after_a_split_and_a_lite_widening_starts_each_transfer_at_its_address(
    tmp_path: Path,
) -> None:
    """Seed 202611250040: an 8-bit APB bus splits a 32-bit AHB read of 0x...ffc into byte
    transfers; an AXI4-Lite crossbar further on widens each to its full 64-bit beat, but the
    axi_dw_downsizer after it starts each at the byte's own address, so the 8-bit APB slave
    never sees 0x...ff8 to 0x...ffb."""
    c = Cfg("splitwiden", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("h", "AHB-Lite", 32, 32, "D0")
    c.master("m0", "AHB-Lite", 32, 32, "D0").link("m0", "h", 0).decode("m0", "HRESP_ERROR")
    c.option("m0", **AHB_MASTER_NA)
    c.fabric("p1", "APB4", 8, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_ahb2apb")
    c.link("h", "b1", 0).link("b1", "p1", 0).decode("b1", "PSLVERR").option("b1", **APB4_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("l", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.bridge("b2", 0x4000_0000, template="bag_apb2axi")
    c.link("p1", "b2", 0).link("b2", "l", 0).decode("b2", "DECERR").option("b2", **AXIL_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.fabric("p2", "APB4", 8, 32, "D0")
    c.bridge("b3", 0x4000_0000, template="bag_axi2apb")
    c.link("l", "b3", 0).link("b3", "p2", 0).decode("b3", "PSLVERR").option("b3", **APB4_NA)
    c.region("b3", "w3", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 8, 32, "D0").link("p2", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.slave == "s0" and sorted(exp.words) == [0x4000_0FFC + k for k in range(4)]
    assert not exp.hits_error_word  # the slave's error word is 0x...ff8


def test_a_wide_lite_read_split_by_the_masters_converter_reads_both_halves(
    tmp_path: Path,
) -> None:
    """Seed 202611250042: a 64-bit AXI4-Lite master's read of 0x...ffc is the whole beat at
    0x...ff8; its converter onto the 32-bit crossbar splits it into two reads, and the
    downsizer in front of the 8-bit APB bus starts each at its own address, so the slave sees
    0x...ff8 too."""
    c = Cfg("litesplit", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 64, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("p", "APB4", 8, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "b1", 0).link("b1", "p", 0).decode("b1", "PSLVERR").option("b1", **APB4_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 8, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.slave == "s0" and sorted(exp.words) == [0x4000_0FF8 + k for k in range(8)]
    assert exp.hits_error_word and exp.slave_error


def test_a_lite_converter_after_a_split_realigns_the_read_to_its_beat(tmp_path: Path) -> None:
    """Review of #209 (seed 341 of the generator): after an 8-bit APB split and an AXI4-Lite
    crossbar's 64-bit widening, bag_axi2apb onto a 16-bit APB bus is bag_axil2apb, whose
    axi_lite_dw_converter reads the whole beat from its start; the AXI4 downsizer in front of
    the 32-bit slave further on then starts at 0x...ff8, so the slave sees that word too."""
    c = Cfg("splitrealign", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("h", "AHB-Lite", 32, 32, "D0")
    c.master("m0", "AHB-Lite", 32, 32, "D0").link("m0", "h", 0).decode("m0", "HRESP_ERROR")
    c.option("m0", **AHB_MASTER_NA)
    c.fabric("p1", "APB4", 8, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_ahb2apb")
    c.link("h", "b1", 0).link("b1", "p1", 0).decode("b1", "PSLVERR").option("b1", **APB4_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("l", "AXI4-Lite", 64, 32, "D0", "round-robin")
    c.bridge("b2", 0x4000_0000, template="bag_apb2axi")
    c.link("p1", "b2", 0).link("b2", "l", 0).decode("b2", "DECERR").option("b2", **AXIL_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.fabric("p2", "APB4", 16, 32, "D0")
    c.bridge("b3", 0x4000_0000, template="bag_axi2apb")
    c.link("l", "b3", 0).link("b3", "p2", 0).decode("b3", "PSLVERR").option("b3", **APB4_NA)
    c.region("b3", "w3", 0x4000_0000, 0x1_0000)
    c.fabric("x", "AXI4", 64, 32, "D0", "round-robin")
    c.bridge("b4", 0x4000_0000, template="bag_apb2axi")
    c.link("p2", "b4", 0).link("b4", "x", 0).decode("b4", "DECERR").option("b4", **AXI4_NA)
    c.region("b4", "w4", 0x4000_0000, 0x1_0000)
    c.slave("s0", "AXI4", 32, 32, "D0").link("x", "s0", 0).option("s0", **AXI4_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.slave == "s0" and sorted(exp.words) == [0x4000_0FF8 // 4, 0x4000_0FFC // 4]
    assert exp.hits_error_word and exp.slave_error


def _rewidened(tmp_path: Path, master_bits: int = 64, apb: bool = True) -> Spec:
    """Generator seed 327's path to s6, cut down: a MASTER_BITS AXI4-Lite master on a 32-bit
    AXI4 bus, an 8-bit APB bus (or, without APB, bag_axi2axil straight on), a 64-bit AXI4-Lite
    bus, then bag_axi2ahb to a 32-bit AHB-Lite slave."""
    c = Cfg("rewiden", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", master_bits, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("l", "AXI4-Lite", 64, 32, "D0", "round-robin")
    if apb:
        c.fabric("p", "APB3", 8, 32, "D0")
        c.bridge("b1", 0x4000_0000, template="bag_axi2apb")
        c.link("a", "b1", 0).link("b1", "p", 0).decode("b1", "PSLVERR").option("b1", **APB3_NA)
        c.region("b1", "w1", 0x4000_0000, 0x1_0000)
        c.bridge("b2", 0x4000_0000, template="bag_apb2axi")
        c.link("p", "b2", 0).link("b2", "l", 0).decode("b2", "DECERR").option("b2", **AXIL_NA)
    else:
        c.bridge("b2", 0x4000_0000, template="bag_axi2axil")
        c.link("a", "b2", 0).link("b2", "l", 0).decode("b2", "DECERR").option("b2", **AXIL_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.fabric("h", "AHB-Lite", 32, 32, "D0")
    c.bridge("b3", 0x4000_0000, template="bag_axi2ahb")
    c.link("l", "b3", 0).link("b3", "h", 0).decode("b3", "HRESP_ERROR")
    c.option("b3", **AHB_MASTER_NA)
    c.region("b3", "w3", 0x4000_0000, 0x1_0000)
    c.slave("s0", "AHB-Lite", 32, 32, "D0").link("h", "s0", 0).option("s0", **AHB_SLAVE_NA)
    c.region("s0", "mem", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    return spec


def test_a_split_read_rewidened_before_a_downsizer_reads_a_word_once_per_byte_before_it(
    tmp_path: Path,
) -> None:
    """Generator seed 327: the 64-bit beat the Lite master reads crosses the 8-bit APB bus as
    eight byte reads; the 64-bit AXI4-Lite bus makes each a full beat again, and bag_axi2ahb's
    downsizer starts each at its byte, so the upper word of the beat is read once for each of
    the eight bytes (its own four and the lower word's four)."""
    spec = _rewidened(tmp_path)
    m = spec.masters[0]
    exp = route(spec, "m0", 0x4000_0100, False, False, {})
    assert exp.slave == "s0" and reads_per_word(spec, m, exp) == 8


@pytest.mark.parametrize(("master_bits", "apb", "repeats"), [(32, True, 4), (32, False, 1)])
def test_a_widening_after_the_split_adds_no_reads(
    tmp_path: Path, master_bits: int, apb: bool, repeats: int
) -> None:
    """Review of #210: what counts is the access where the 8-bit bus splits it, not what the
    64-bit AXI4-Lite bus widens it to later. A 32-bit Lite master's 4-byte read is four byte
    reads (each word at most four times); with no narrow bus at all, nothing repeats."""
    spec = _rewidened(tmp_path, master_bits, apb)
    exp = route(spec, "m0", 0x4000_0100, False, False, {})
    assert exp.slave == "s0" and reads_per_word(spec, spec.masters[0], exp) == repeats


def test_ahb_aligns_a_lite_masters_beat_it_carries_as_one_axi4_transfer(tmp_path: Path) -> None:
    """Seed 202611200006: a 64-bit AXI4-Lite master's read of 0x...ffc, behind its pc onto a
    128-bit AXI4 bus, is one 8-byte AXI4 transfer at 0x...ffc; bag_axi2ahb aligns HADDR to that
    transfer, so the AHB read starts at 0x...ff8 and the 16-bit APB slave behind it sees the
    error word there."""
    c = Cfg("ahbsize", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 128, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 64, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("h", "AHB-Lite", 64, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_axi2ahb")
    c.link("a", "b1", 0).link("b1", "h", 0).decode("b1", "HRESP_ERROR")
    c.option("b1", **AHB_MASTER_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("p", "APB3", 16, 32, "D0")
    c.bridge("b2", 0x4000_0000, template="bag_ahb2apb")
    c.link("h", "b2", 0).link("b2", "p", 0).decode("b2", "PSLVERR").option("b2", **APB3_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB3", 16, 32, "D0").link("p", "s0", 0).option("s0", **APB3_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.slave == "s0" and min(exp.words) * 2 == 0x4000_0FF8
    assert exp.hits_error_word and exp.slave_error


def test_a_downsizer_onto_a_narrower_bus_narrows_the_transfer_ahb_aligns_to(
    tmp_path: Path,
) -> None:
    """Generator seed 2 (review of the seed 006 fix): behind its pc and a downsizer onto a
    32-bit AXI4 bus, a 64-bit AXI4-Lite master's beat becomes 4-byte transfers from the
    address, so bag_axi2ahb aligns HADDR to 4 bytes and 0x...ff8 is not read."""
    c = Cfg("ahbsize32", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 64, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("h", "AHB-Lite", 64, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_axi2ahb")
    c.link("a", "b1", 0).link("b1", "h", 0).decode("b1", "HRESP_ERROR")
    c.option("b1", **AHB_MASTER_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("p", "APB3", 16, 32, "D0")
    c.bridge("b2", 0x4000_0000, template="bag_ahb2apb")
    c.link("h", "b2", 0).link("b2", "p", 0).decode("b2", "PSLVERR").option("b2", **APB3_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB3", 16, 32, "D0").link("p", "s0", 0).option("s0", **APB3_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.slave == "s0" and min(exp.words) * 2 == 0x4000_0FFC and not exp.hits_error_word


def test_a_split_onto_an_apb_bus_makes_transfers_of_its_width_before_ahb(tmp_path: Path) -> None:
    """Review of #214: after a 16-bit APB bus, bag_apb2axi issues 2-byte transfers, so
    bag_axi2ahb aligns HADDR to 2 bytes and the 64-bit Lite master's read of 0x...ffc does not
    reach 0x...ff8 (with the split's size not set, the model read it)."""
    c = Cfg("apbsize", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4", 64, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 64, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("p", "APB4", 16, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "b1", 0).link("b1", "p", 0).decode("b1", "PSLVERR").option("b1", **APB4_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("x", "AXI4", 64, 32, "D0", "round-robin")
    c.bridge("b2", 0x4000_0000, template="bag_apb2axi")
    c.link("p", "b2", 0).link("b2", "x", 0).decode("b2", "DECERR").option("b2", **AXI4_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.fabric("h", "AHB-Lite", 64, 32, "D0")
    c.bridge("b3", 0x4000_0000, template="bag_axi2ahb")
    c.link("x", "b3", 0).link("b3", "h", 0).decode("b3", "HRESP_ERROR")
    c.option("b3", **AHB_MASTER_NA)
    c.region("b3", "w3", 0x4000_0000, 0x1_0000)
    c.fabric("q", "APB3", 16, 32, "D0")
    c.bridge("b4", 0x4000_0000, template="bag_ahb2apb")
    c.link("h", "b4", 0).link("b4", "q", 0).decode("b4", "PSLVERR").option("b4", **APB3_NA)
    c.region("b4", "w4", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB3", 16, 32, "D0").link("q", "s0", 0).option("s0", **APB3_NA)
    c.region("s0", "regs", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert findings == [] and spec is not None
    exp = route(spec, "m0", 0x4000_0FFC, False, False, {})
    assert exp.slave == "s0" and min(exp.words) * 2 == 0x4000_0FFC and not exp.hits_error_word


def test_an_axi_master_that_reaches_no_slave_is_left_out_of_the_handshake_orderings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shrunk seed 202611250042 (#213): an AXI master that Access_Rules leave no slave has no
    B or R its model answers, as a slave nothing reaches has no AW, W or AR (D105): the test
    leaves it out instead of failing "it reaches no slave"."""
    from hdl_bag.regress.generate import check
    from hdl_bag.tests_gen import route_tests

    c = Cfg("ordernone", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("x", "AXI4", 32, 32, "D0", "round-robin")
    for k in (0, 1):
        c.master(f"m{k}", "AXI4", 32, 32, "D0").link(f"m{k}", "x", k).decode(f"m{k}", "DECERR")
        c.option(f"m{k}", **AXI4_NA)
    c.slave("s0", "AXI4", 32, 32, "D0").link("x", "s0", 0).option("s0", **AXI4_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    c.access("m0", "s0", "-")
    _, result = check(c, tmp_path)
    assert not result.errors
    monkeypatch.setattr(route_tests, "SPEC", resolve(result), raising=False)
    found, required = route_tests._ordering_targets()
    assert {t.master for t in found} == {"m1"} and required["m0"] == ()


def test_the_handshake_ordering_test_applies_only_where_an_axi_port_is_reached(
    tmp_path: Path,
) -> None:
    """Review of #218: with every AXI master and slave out of reach, the test has nothing to
    plan, so it must not apply (rather than pass having checked nothing)."""
    from hdl_bag.regress.generate import check
    from hdl_bag.tests_gen import applicable_tests

    c = Cfg("ordernone2", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("x", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "x", 0).decode("m0", "DECERR")
    c.option("m0", **AXI4_NA)
    c.slave("s0", "AXI4", 32, 32, "D0").link("x", "s0", 0).option("s0", **AXI4_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    c.access("m0", "s0", "-")
    _, result = check(c, tmp_path)
    assert not result.errors
    assert "axi_ports_take_every_handshake_ordering" not in applicable_tests(resolve(result))


def test_the_handshake_ordering_test_needs_an_access_that_is_not_refused(tmp_path: Path) -> None:
    """CI on #218: with CACHE tied to 0 every AXI access is refused (D104), so the test has no
    traffic to watch the orderings of, and must not apply."""
    from hdl_bag.tests_gen import applicable_tests

    spec, findings = resolve_cfg(_tied_cache("tie-0"), tmp_path)
    assert findings == [] and spec is not None
    assert "axi_ports_take_every_handshake_ordering" not in applicable_tests(spec)


def test_an_axi4_master_on_an_axi4_lite_fabric_loses_its_tied_cache(tmp_path: Path) -> None:
    """Seed 202611260015: an AXI4 master with CACHE tied to 0 enters an AXI4-Lite crossbar
    through its pc; AXI4-Lite has no AxCACHE, so the pc in front of an AXI4 slave drives the tie
    default, 0b0011, whatever the master tied."""
    from hdl_bag.tests_gen.route_model import attributes

    c = Cfg("litecache", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **{**AXI4_NA, "cache": "tie-0"})
    c.slave("s0", "AXI4", 32, 32, "D0").link("a", "s0", 0).option("s0", **AXI4_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert spec is not None, findings
    m = spec.masters[0]
    assert attributes(spec, m, 0, False, (), at_slave=True)[3] == 0b11
    # The master's own axi_to_axi_lite still sees the tie, and refuses its bursts (D104).
    assert attributes(spec, m, 0, False, ())[3] == 0


def test_a_bridge_off_an_axi4_lite_fabric_takes_the_default_cache(tmp_path: Path) -> None:
    """Review of #222: bag_axi2ahb on an AXI4-Lite fabric has no AxCACHE to read, so it
    takes 0b0011 and HPROT[3:2] is 0b11 at the AHB-Lite slave, whatever the master tied."""
    from hdl_bag.tests_gen.route_model import attributes

    c = Cfg("litecache2", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **{**AXI4_NA, "cache": "tie-0"})
    c.fabric("h", "AHB-Lite", 32, 32, "D0")
    c.bridge("b1", 0x4000_0000, template="bag_axi2ahb")
    c.link("a", "b1", 0).link("b1", "h", 0).decode("b1", "HRESP_ERROR")
    c.option("b1", **AHB_MASTER_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.slave("s0", "AHB-Lite", 32, 32, "D0").link("h", "s0", 0).option("s0", **AHB_SLAVE_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert spec is not None, findings
    assert attributes(spec, spec.masters[0], 0, False, ("b1",), at_slave=True)[3] == 0b11


def test_a_split_read_whose_second_transfer_misses_is_an_error(tmp_path: Path) -> None:
    """#216 (shrunk 202611200006, 202611250042): a 64-bit AXI4-Lite master's read is one
    full-width beat, split onto a 32-bit fabric into two transfers, each decoded on its own
    address. The second falls past a 4-byte region and misses, so the master gets an error;
    the model expected OKAY. (Here onto APB4 behind bag_axi2apb, as in the seed.)"""
    from hdl_bag.tests_gen.route_model import route

    c = Cfg("splitmiss", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("f0", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 64, 32, "D0").link("m0", "f0", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("p", "APB4", 32, 32, "D0")
    c.bridge("b0", 0x4000_0000, template="bag_axi2apb")
    c.link("f0", "b0", 0).link("b0", "p", 0).decode("b0", "PSLVERR").option("b0", **APB4_NA)
    c.region("b0", "w0", 0x4000_0000, 0x1000)
    c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "r0", 0x4000_0000, 0x4)
    spec, findings = resolve_cfg(c, tmp_path)
    assert spec is not None, findings
    read = route(spec, "m0", 0x4000_0000, False, False, {})
    assert (read.slave, read.slave_error, read.partial) == ("s0", True, "PSLVERR")
    assert read.words == {0x4000_0000 // 4}  # the second word misses: it never reaches s0
    write = route(spec, "m0", 0x4000_0000, True, False, {})  # its 4 bytes: one transfer
    assert (write.slave, write.slave_error, write.partial) == ("s0", False, None)
    # Bytes 2 to 5 cross the 32-bit beat: bytes 4 and 5 are a second transfer, and miss.
    late = route(spec, "m0", 0x4000_0002, True, False, {})
    assert (late.slave, late.slave_error, late.partial) == ("s0", True, "PSLVERR")
    # Only the transfers before the first miss surely reached s0, byte by byte (review of #226).
    assert read.sure == set(range(0x4000_0000, 0x4000_0004))
    assert late.sure == {0x4000_0002, 0x4000_0003}


def _split_shapes(tmp_path: Path, shape: str) -> Spec:
    """Review of #226: variants of the split-miss config. "axi4": s0 straight on the AXI4
    fabric, which decodes a burst once (bag_axi_guard), so its beats all reach s0. "next":
    s1's region right after s0's, so the second transfer reaches s1, not a miss. "masked":
    PSLVERR tied on b0's APB end, which hides the miss behind it."""
    from hdl_bag.regress.generate import check

    c = Cfg(f"split{shape}", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("f0", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 64, 32, "D0").link("m0", "f0", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    if shape == "axi4":
        c.slave("s0", "AXI4", 32, 32, "D0").link("f0", "s0", 0).option("s0", **AXI4_NA)
        c.region("s0", "r0", 0x4000_0000, 0x4)
    else:
        c.fabric("p", "APB4", 32, 32, "D0")
        c.bridge("b0", 0x4000_0000, template="bag_axi2apb")
        tie = {"pslverr": "tie-0"} if shape == "masked" else {}
        c.link("f0", "b0", 0).link("b0", "p", 0).decode("b0", "PSLVERR")
        c.option("b0", **{**APB4_NA, **tie}).region("b0", "w0", 0x4000_0000, 0x1000)
        c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
        c.region("s0", "r0", 0x4000_0000, 0x4)
        if shape == "next":
            c.slave("s1", "APB4", 32, 32, "D0").link("p", "s1", 1).option("s1", **APB4_NA)
            c.region("s1", "r0", 0x4000_0004, 0x4)
    _, result = check(c, tmp_path)
    assert not result.errors, [f.message for f in result.errors]
    return resolve(result)


@pytest.mark.parametrize("shape", ["axi4", "next", "masked"])
def test_a_split_access_is_an_error_only_where_a_transfer_really_misses(
    tmp_path: Path, shape: str
) -> None:
    """Review of #226: only a fabric that decodes each transfer (AHB-Lite, APB, AXI4-Lite)
    can miss one; a transfer another slave takes is not a miss; a tied PSLVERR on the way
    hides it."""
    from hdl_bag.tests_gen.route_model import route

    read = route(_split_shapes(tmp_path, shape), "m0", 0x4000_0000, False, False, {})
    want = {"axi4": (None, False), "next": (None, False), "masked": ("PSLVERR", False)}[shape]
    assert (read.slave, read.partial, read.slave_error) == ("s0", *want)


def test_a_single_byte_at_a_regions_last_byte_is_one_transfer(tmp_path: Path) -> None:
    """CI on #226: a byte written at a region's last byte is one transfer, so the bytes past the
    region a 4-byte access would cover never miss (single_bytes_reach_both_ends_of_every_region,
    p2/ahb_single, examples/mcu_ahb). route() takes the access's length."""
    from hdl_bag.tests_gen.route_model import region_edges

    c = Cfg("lastbyte", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("f0", "AXI4", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "f0", 0).decode("m0", "DECERR")
    c.option("m0", **AXI4_NA)
    c.fabric("p", "APB4", 32, 32, "D0")
    c.bridge("b0", 0x4000_0000, template="bag_axi2apb")
    c.link("f0", "b0", 0).link("b0", "p", 0).decode("b0", "PSLVERR").option("b0", **APB4_NA)
    c.region("b0", "w0", 0x4000_0000, 0x2000)
    c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert spec is not None, findings
    edges = {a: (w, r) for a, w, r in region_edges(spec, spec.masters[0])}
    write, read = edges[0x4000_0FFF]
    assert (write.slave, write.partial, read.partial) == ("s0", None, None)


def test_a_byte_write_does_not_land_alone_after_a_wider_apb3_bus(tmp_path: Path) -> None:
    """Seed 202611270035: an 8-bit APB3 slave's word is a byte, but the byte write first crosses
    a 16-bit APB3 bus, which has no PSTRB: it becomes a halfword, and bag_axi2apb later writes
    both bytes. The narrow-APB test's single-byte check must not run on such a path."""
    from hdl_bag.tests_gen.route_model import byte_write_lands, narrow_apb_path

    c = Cfg("byteapb3", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("p", "APB3", 16, 32, "D0")
    c.bridge("b0", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "b0", 0).link("b0", "p", 0).decode("b0", "PSLVERR").option("b0", **APB3_NA)
    c.region("b0", "w0", 0x4000_0000, 0x1_0000)
    c.fabric("x", "AXI4", 32, 32, "D0", "round-robin")
    c.bridge("b1", 0x4000_0000, template="bag_apb2axi")
    c.link("p", "b1", 0).link("b1", "x", 0).decode("b1", "DECERR").option("b1", **AXI4_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("q", "APB3", 8, 32, "D0")
    c.bridge("b2", 0x4000_0000, template="bag_axi2apb")
    c.link("x", "b2", 0).link("b2", "q", 0).decode("b2", "PSLVERR").option("b2", **APB3_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB3", 8, 32, "D0").link("q", "s0", 0).option("s0", **APB3_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert spec is not None, findings
    m, s = spec.masters[0], spec.slaves[0]
    found = narrow_apb_path(spec, m, s)
    assert found is not None
    assert not byte_write_lands(spec, s, found[1])


def test_two_masters_never_plan_the_same_slave_word(tmp_path: Path) -> None:
    """Seed 202611270046: each master's private words are offset by 0x100 per master index in
    the lowest region it can use. For m1 that was a bridge window, and its offset landed in the
    slave region behind it that m0 had taken: they overwrote each other's words."""
    from hdl_bag.tests_gen.route_model import private_plans, route

    c = Cfg("twoplans", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("f", "AXI4-Lite", 32, 32, "D0", "round-robin")
    for k, name in enumerate(("m0", "m1")):
        c.master(name, "AXI4-Lite", 32, 32, "D0").link(name, "f", k).decode(name, "DECERR")
        c.option(name, **AXIL_NA)
    c.fabric("p", "APB4", 32, 32, "D0")
    c.bridge("b0", 0x4000_0000, template="bag_axi2apb")
    c.link("f", "b0", 0).link("b0", "p", 0).decode("b0", "PSLVERR").option("b0", **APB4_NA)
    c.region("b0", "w0", 0x4000_0000, 0x1000)
    c.slave("s0", "APB4", 32, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "r0", 0x4000_0100, 0x100)
    c.slave("s1", "APB4", 32, 32, "D0").link("p", "s1", 1).option("s1", **APB4_NA)
    c.region("s1", "r0", 0x4000_0800, 0x400)  # where m1 goes instead
    spec, findings = resolve_cfg(c, tmp_path)
    assert spec is not None, findings
    seen: dict[tuple[str | None, int], str] = {}
    plans = private_plans(spec, 8)
    for m in spec.masters:
        for a in plans[m.name]:
            e = route(spec, m.name, a, True, False, {})
            assert (e.slave, e.address) not in seen, (m.name, hex(a), seen[e.slave, e.address])
            seen[e.slave, e.address] = m.name
    assert set(seen.values()) == {"m0", "m1"}


@pytest.mark.parametrize("width", [8, 16])
def test_a_word_split_by_an_earlier_narrow_bus_is_no_narrow_apb_word(
    tmp_path: Path, width: int
) -> None:
    """Seed 202611210018: an 8-bit APB4 bus on the way splits every word write into byte writes,
    so the 16-bit APB4 slave behind it takes one write per byte, not one per 16-bit word: the
    narrow-APB test's word check does not apply on such a path. A 16-bit bus there is as wide
    as the word, and the path stays (review of #229)."""
    from hdl_bag.tests_gen.route_model import narrow_apb_path, plain_word, splits_word_writes

    c = Cfg("narrowsplit", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("p8", "APB4", width, 32, "D0")
    c.bridge("b0", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "b0", 0).link("b0", "p8", 0).decode("b0", "PSLVERR").option("b0", **APB4_NA)
    c.region("b0", "w0", 0x4000_0000, 0x1_0000)
    c.fabric("l", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.bridge("b1", 0x4000_0000, template="bag_apb2axi")
    c.link("p8", "b1", 0).link("b1", "l", 0).decode("b1", "DECERR").option("b1", **AXIL_NA)
    c.region("b1", "w1", 0x4000_0000, 0x1_0000)
    c.fabric("p16", "APB4", 16, 32, "D0")
    c.bridge("b2", 0x4000_0000, template="bag_axi2apb")
    c.link("l", "b2", 0).link("b2", "p16", 0).decode("b2", "PSLVERR").option("b2", **APB4_NA)
    c.region("b2", "w2", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 16, 32, "D0").link("p16", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    spec, findings = resolve_cfg(c, tmp_path)
    assert spec is not None, findings
    m, s = spec.masters[0], spec.slaves[0]
    found = plain_word(spec, m, s)  # the path is there: only the split can leave it out
    assert found is not None and found[1].bridges == ("b0", "b1", "b2")
    assert splits_word_writes(spec, found[1]) == (width == 8)
    assert (narrow_apb_path(spec, m, s) is None) == (width == 8)


@pytest.mark.parametrize("tie", ["tie-0", "tie-default"])
def test_no_single_byte_check_behind_a_tied_pstrb_even_onto_an_8_bit_slave(
    tmp_path: Path, tie: str
) -> None:
    """Review of #228: since #228 byte_write_lands is stores_single_bytes, so a bridge with PSTRB
    tied keeps the narrow-APB test's single-byte check off an 8-bit APB4 slave too. With tie-0
    that is required (no write stores any byte); with tie-default the byte would land (the word
    is the byte), so this only gives up coverage, which is accepted."""
    from hdl_bag.tests_gen.route_model import byte_write_lands, narrow_apb_path, plain_word

    c = Cfg("tied8", phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    c.fabric("a", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4-Lite", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.option("m0", **AXIL_NA)
    c.fabric("p", "APB4", 8, 32, "D0")
    c.bridge("b0", 0x4000_0000, template="bag_axi2apb")
    c.link("a", "b0", 0).link("b0", "p", 0).decode("b0", "PSLVERR")
    c.option("b0", **{**APB4_NA, "pstrb": tie}).region("b0", "w0", 0x4000_0000, 0x1_0000)
    c.slave("s0", "APB4", 8, 32, "D0").link("p", "s0", 0).option("s0", **APB4_NA)
    c.region("s0", "r0", 0x4000_0000, 0x1000)
    from hdl_bag.regress.generate import check

    _, result = check(c, tmp_path)
    assert not result.errors, [f.message for f in result.errors]
    spec = resolve(result)
    found = plain_word(spec, spec.masters[0], spec.slaves[0])
    assert found is not None
    assert not byte_write_lands(spec, spec.slaves[0], found[1])
    # Review of #235: with tie-0 no write stores anything, so the narrow-APB test's word checks
    # cannot hold either: the path is no narrow-APB word at all. With tie-default it is.
    assert (narrow_apb_path(spec, spec.masters[0], spec.slaves[0]) is None) == (tie == "tie-0")
