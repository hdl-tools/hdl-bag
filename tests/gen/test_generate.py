# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generator outputs without EDA tools: layout, determinism, header, tables, CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main
from hdl_bag.gen import MANIFEST, MissingRtl, generate, load_outputs, sources
from hdl_bag.gen.decode import decode_tables
from hdl_bag.gen.render import camel, ir_sha256, lit
from hdl_bag.ir import load
from hdl_bag.templates import builtin_registry

TESTS = Path(__file__).parents[1]
CONFIG = TESTS / "configs" / "p2" / "ahb2apb_pt.ir.yaml"
GOLDEN_IR = TESTS / "golden" / "demo_ss.ir.yaml"


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("flat", ["rtl/pkg/demo_ss_bus_pkg.sv", *(f"rtl/inhouse/{m}.sv" for m in (
            "bag_addr_decode", "bag_ahb2apb", "bag_ahb_lite_bus", "bag_apb_mux",
            "bag_rst_sync", "bag_sync")), "rtl/demo_ss_bus_top.sv"]),
        ("sv-interface", ["rtl/pkg/demo_ss_bus_pkg.sv", "rtl/demo_ss_ahb_if.sv",
                          "rtl/demo_ss_apb4_if.sv", *(f"rtl/inhouse/{m}.sv" for m in (
            "bag_addr_decode", "bag_ahb2apb", "bag_ahb_lite_bus", "bag_apb_mux",
            "bag_rst_sync", "bag_sync")), "rtl/demo_ss_bus_top.sv",
            "rtl/demo_ss_bus_top_flat.sv"]),
    ],
)  # fmt: skip
def test_layout_and_filelist(tmp_path: Path, style: str, expected: list[str]) -> None:
    out = generate(load(CONFIG), tmp_path, CONFIG.parent, style)
    assert [p.as_posix() for p in sources(out)] == expected
    assert load_outputs(tmp_path) == out
    manifest = json.loads((tmp_path / MANIFEST).read_text())
    assert sorted(manifest["files"]) == sorted(out.files)
    assert out.top == ("demo_ss_bus_top" if style == "flat" else "demo_ss_bus_top_flat")


def test_header_is_user_text_then_banner(tmp_path: Path) -> None:
    spec = load(CONFIG)
    generate(spec, tmp_path, CONFIG.parent)
    user = (CONFIG.parent / "header.txt").read_text()
    for rel in ("rtl/pkg/demo_ss_bus_pkg.sv", "rtl/demo_ss_bus_top.sv"):
        text = (tmp_path / rel).read_text()
        assert text.startswith(user)
        banner = text[len(user) :].splitlines()[:5]
        assert "DO NOT EDIT" in banner[1] and banner[3].endswith(ir_sha256(spec))
    # In-house IP is copied verbatim, with its own licence header.
    assert (tmp_path / "rtl/inhouse/bag_sync.sv").read_text().startswith("// SPDX-License-")


@pytest.mark.parametrize("style", ["flat", "sv-interface"])
def test_byte_identical_and_lf(tmp_path: Path, style: str) -> None:
    generate(load(CONFIG), tmp_path / "a", CONFIG.parent, style)
    generate(load(CONFIG), tmp_path / "b", CONFIG.parent, style)
    a = _tree(tmp_path / "a")
    assert a == _tree(tmp_path / "b")
    assert not any(b"\r" in v for v in a.values())


def test_byte_identical_across_processes(tmp_path: Path) -> None:
    trees = []
    for seed in ("0", "12345"):
        out = tmp_path / seed
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run(
            [sys.executable, "-m", "hdl_bag.cli", "generate", str(CONFIG), "-o", str(out),
             "--style", "sv-interface"],
            check=True, env=env, capture_output=True,
        )  # fmt: skip
        trees.append(_tree(out))
    assert trees[0] == trees[1]


@pytest.mark.parametrize("config", ["p3/axi_xbar_rules", "p2/ahb_fixed"])
def test_formal_output_is_byte_identical_across_processes(tmp_path: Path, config: str) -> None:
    """The formal harnesses under two hash seeds: an AXI4 crossbar (ID-prefixed masters,
    miss responses) and an AHB-Lite bus with aliases and fixed priorities."""
    ir = TESTS / "configs" / f"{config}.ir.yaml"
    trees = []
    for seed in ("0", "12345"):
        out = tmp_path / seed
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run([sys.executable, "-m", "hdl_bag.cli", "generate", str(ir), "-o", str(out)],
                       check=True, env=env, capture_output=True)  # fmt: skip
        trees.append({k: v for k, v in _tree(out).items() if k.startswith("formal/")})
    assert trees[0] and trees[0] == trees[1]


def test_golden_generates_fully(tmp_path: Path) -> None:
    # Phase 3 PR 6 gave the last templates demo_ss uses (the in-house bridges) RTL.
    out = generate(load(GOLDEN_IR), tmp_path, GOLDEN_IR.parent)
    assert out.top == "demo_ss_bus_top"
    assert json.loads((tmp_path / MANIFEST).read_text())["top"] == "demo_ss_bus_top"
    assert load_outputs(tmp_path).files == out.files


def test_vendored_files_come_with_their_licence(tmp_path: Path) -> None:
    """Every vendored package the output holds files of brings its LICENSE (SHL-0.51 requires
    the notice to travel with the files); the licence is not a source to compile."""
    from importlib.resources import files

    out = generate(load(GOLDEN_IR), tmp_path, GOLDEN_IR.parent)
    vendored = [f for f in out.files if f.startswith("rtl/vendor/pulp/")]
    packages = {f.split("/")[3] for f in vendored}
    assert packages >= {"axi", "common_cells"}
    for pkg in packages:
        rel = f"rtl/vendor/pulp/{pkg}/LICENSE"
        assert rel in out.files
        assert (tmp_path / rel).read_text(encoding="utf-8") == (files("hdl_bag") / rel).read_text(
            encoding="utf-8"
        )
    assert not any(p.name == "LICENSE" for p in sources(out))


_LICENCES = "rtl/vendor/licences"
_ASSERTIONS = "rtl/vendor/pulp/common_cells/include/common_cells/assertions.svh"
_READ = "rtl/vendor/pulp/common_cells/src/read.sv"


@pytest.mark.parametrize(
    ("config", "holds", "texts"),
    [
        ("golden/demo_ss.ir.yaml", [_ASSERTIONS, _READ], ["Apache-2.0.txt", "SHL-2.1.txt"]),
        ("configs/p3/axi_xbar_pow2.ir.yaml", [_ASSERTIONS], ["Apache-2.0.txt"]),
        ("configs/p2/apb4_min.ir.yaml", [], []),
    ],
)
def test_a_vendored_file_under_another_licence_brings_that_licence_text(
    tmp_path: Path, config: str, holds: list[str], texts: list[str]
) -> None:
    """assertions.svh is Apache-2.0 and read.sv is Apache-2.0 WITH SHL-2.1 (a wraparound, so it
    needs both texts), but the package LICENSE beside them is Solderpad 0.51. A design ships the
    texts of exactly the licences of the files it holds, and they are not sources to compile."""
    from importlib.resources import files

    ir = TESTS / config
    out = generate(load(ir), tmp_path, ir.parent)
    for rel in holds:  # the precondition, so the test cannot pass vacuously
        assert rel in out.files, rel
    assert not ({_ASSERTIONS, _READ} - set(holds)) & set(out.files)
    assert sorted(f for f in out.files if f.startswith(f"{_LICENCES}/")) == [
        f"{_LICENCES}/{t}" for t in texts
    ]
    for t in texts:
        rel = f"{_LICENCES}/{t}"
        assert (tmp_path / rel).read_bytes() == (files("hdl_bag") / rel).read_bytes()
    assert not any(p.parent.as_posix() == _LICENCES for p in sources(out))


@pytest.mark.parametrize("config", ["golden/demo_ss.ir.yaml", "configs/p3/axi_xbar_pow2.ir.yaml"])
def test_derived_files_come_with_the_axi_licence(tmp_path: Path, config: str) -> None:
    ir = TESTS / config
    out = generate(load(ir), tmp_path, ir.parent)
    if any(f.startswith("rtl/vendor/derived/") for f in out.files):
        assert "rtl/vendor/pulp/axi/LICENSE" in out.files


@pytest.fixture
def no_apb_rtl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every built-in template has RTL since Phase 3 PR 7; mark bag_apb_mux as having none
    so the MissingRtl path can still be tested."""
    import hdl_bag.gen as gen
    from hdl_bag.templates.registry import Registry

    reg = builtin_registry()
    manifests = []
    for name in reg.names():
        m = reg.get(name)
        assert m is not None
        manifests.append(m.model_copy(update={"rtl": False}) if name == "bag_apb_mux" else m)
    monkeypatch.setattr(gen, "builtin_registry", lambda: Registry(manifests))


def test_missing_rtl_writes_collateral_then_stops(tmp_path: Path, no_apb_rtl: None) -> None:
    with pytest.raises(MissingRtl) as exc:
        generate(load(GOLDEN_IR), tmp_path, GOLDEN_IR.parent)
    assert exc.value.templates == ["bag_apb_mux"]
    assert (tmp_path / "rtl/pkg/demo_ss_bus_pkg.sv").is_file()
    assert json.loads((tmp_path / MANIFEST).read_text())["top"] is None
    with pytest.raises(ValueError, match="no RTL was generated"):
        load_outputs(tmp_path)


def test_package_tables(tmp_path: Path) -> None:
    generate(load(CONFIG), tmp_path, CONFIG.parent)
    pkg = (tmp_path / "rtl/pkg/demo_ss_bus_pkg.sv").read_text()
    # aon_apb: wdt0 (secure) at 0x5000_0000, rtc0 at 0x5000_1000; highest index first.
    assert "localparam logic [1:0][31:0] AonApbRuleBase = '{32'h5000_1000, 32'h5000_0000};" in pkg
    assert "localparam logic [1:0] AonApbRuleSecure = 2'b01;" in pkg
    assert "localparam logic [63:0] Wdt0RegsBase = 64'h0000_0000_5000_0000;" in pkg
    assert "localparam logic [63:0] Wdt0RegsSize = 64'h0000_0000_0000_1000;" in pkg
    assert max(len(line) for line in pkg.splitlines()) <= 100


def test_decode_tables_from_golden() -> None:
    spec = load(GOLDEN_IR)
    xbar = next(f for f in spec.fabrics if f.name == "main_xbar")
    t = decode_tables(xbar)
    masters = {p.port: p.index for p in xbar.master_ports}
    slaves = {p.port: p.index for p in xbar.slave_ports}
    # Access_Rules: dma0 has no access to br_main2cfg (D6).
    assert not t.connect[masters["dma0"]][slaves["br_main2cfg"]]
    assert sum(not c for row in t.connect for c in row) == len(
        [a for a in xbar.access_rules if a.access == "none"]
    )
    # cpu0's boot alias is enabled by the boot_remap_i pin.
    assert [(a.master, a.enable) for a in t.aliases] == [(masters["cpu0"], "boot_remap_i")]
    assert t.alias_enables == ("boot_remap_i",)
    assert len(t.rules) == len(xbar.addr_rules)


def test_render_helpers() -> None:
    assert lit(32, 0x1000_3000) == "32'h1000_3000"
    assert lit(36, 0x1_2345_6789) == "36'h1_2345_6789"
    assert lit(8, -1) == "8'hFF"
    assert lit(3, 5) == "3'b101"
    assert camel("br_ahb2aon") == "BrAhb2aon"


def test_cli_generate_and_missing_rtl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    ok = runner.invoke(main, ["generate", str(CONFIG), "-o", str(tmp_path / "ok")])
    assert ok.exit_code == 0, ok.output
    assert "top demo_ss_bus_top" in ok.output
    import hdl_bag.cli as cli

    def missing(*_: object, **__: object) -> None:
        raise MissingRtl(["bag_apb_mux"])

    monkeypatch.setattr(cli, "generate", missing)
    bad = runner.invoke(main, ["generate", str(GOLDEN_IR), "-o", str(tmp_path / "bad")])
    assert bad.exit_code == 1
    # Every built-in template has RTL since Phase 3, so the message names no phase.
    wrote = f"wrote collateral to {tmp_path / 'bad'}"
    assert bad.output.splitlines()[-2:] == [wrote, "error: no RTL yet for template(s): bag_apb_mux"]


def test_generated_lines_fit_100_columns(tmp_path: Path) -> None:
    """The sv-interface flat harness wraps an interface instance that would overflow (a long
    project name with AXI4 ID/USER parameters)."""
    src = TESTS / "configs" / "p3" / "bridges_inhouse_cdc.ir.yaml"
    generate(load(src), tmp_path, src.parent, style="sv-interface")
    skip = {"vendor", "inhouse"}  # copied RTL, linted on its own
    files = [p for p in (tmp_path / "rtl").rglob("*.sv") if not skip & set(p.parts)]
    long = [(p.name, n) for p in files
            for n, line in enumerate(p.read_text().splitlines(), 1) if len(line) > 100]  # fmt: skip
    assert not long


def _no_rtl(tmp_path: Path) -> Path:
    """CONFIG with Project > Generate RTL = N, as an IR beside its header."""
    text = CONFIG.read_text(encoding="utf-8")
    assert text.count("    rtl: true\n") == 1
    ir_path = tmp_path / "no_rtl.ir.yaml"
    ir_path.write_text(text.replace("    rtl: true\n", "    rtl: false\n"), encoding="utf-8")
    header = CONFIG.parent / "header.txt"
    if header.is_file():
        (tmp_path / "header.txt").write_text(header.read_text(encoding="utf-8"), encoding="utf-8")
    return ir_path


def test_generate_rtl_n_writes_the_collateral_and_no_rtl(tmp_path: Path) -> None:
    """D100: Project > Generate RTL = N writes the C header and the documents that need no
    RTL, and no RTL, file list, tests or formal; the manifest records no top."""
    ir_path = _no_rtl(tmp_path)
    out = generate(load(ir_path), tmp_path / "out", tmp_path)
    name = load(ir_path).project.name
    docs = [f"doc/{d}" for d in ("memmap.html", "memmap.md", "port_map.md", "reset.md")]
    assert sorted(out.files) == [*docs, f"sw/{name}_memmap.h"]
    assert (out.top, out.tests, out.formal) == ("", None, ())
    manifest = json.loads((tmp_path / "out" / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["top"] is None
    with pytest.raises(ValueError, match="no RTL was generated"):
        load_outputs(tmp_path / "out")


def test_bag_generate_and_flow_say_there_is_no_rtl(tmp_path: Path) -> None:
    """D100: bag generate says why it wrote no RTL; bag flow stops after it, with status 0,
    since there is nothing to lint, compile, simulate or prove."""
    ir_path = _no_rtl(tmp_path)
    note = "no RTL generated (Project > Generate RTL is N): nothing to lint, simulate or prove"
    got = CliRunner().invoke(main, ["generate", str(ir_path), "-o", str(tmp_path / "out")])
    assert got.exit_code == 0, got.output
    assert got.output.splitlines()[-2:] == [f"wrote 5 file(s) to {tmp_path / 'out'}", note]
    got = CliRunner().invoke(main, ["flow", str(ir_path), "-o", str(tmp_path / "flow")])
    assert got.exit_code == 0, got.output
    assert got.output.splitlines()[-1] == note


def test_generate_rtl_n_after_y_removes_the_rtl_of_the_earlier_run(tmp_path: Path) -> None:
    """D100 review: generating with Generate RTL = N into a directory that holds an earlier
    run's RTL leaves only this run's files (the manifest removes the rest)."""
    ir_path = _no_rtl(tmp_path)
    out = tmp_path / "out"
    generate(load(CONFIG), out, CONFIG.parent)
    assert (out / "rtl").is_dir()
    done = generate(load(ir_path), out, tmp_path)
    left = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert left == sorted([*done.files, MANIFEST])


def test_generate_over_a_corrupt_manifest_writes_a_fresh_one(tmp_path: Path) -> None:
    """A broken bag_outputs.json from an earlier run is no reason to stop: running bag
    generate again is the remedy the error line names (cleanup review)."""
    out = tmp_path / "out"
    out.mkdir()
    (out / MANIFEST).write_text("{not json", encoding="utf-8")
    generate(load(CONFIG), out, CONFIG.parent)
    assert load_outputs(out).top
