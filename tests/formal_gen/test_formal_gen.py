# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal generation without EDA tools: what the quiet-in-reset properties watch, their
environment, the SymbiYosys job, and that every port they bind to exists on the top."""

import re
from pathlib import Path

import pytest

from hdl_bag.formal_gen import BMC_DEPTH, COVER_DEPTH, props_module
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.ir import load
from hdl_bag.templates import builtin_registry

TESTS = Path(__file__).parents[1]
GOLDEN = TESTS / "golden" / "demo_ss.ir.yaml"
P2, P3 = TESTS / "configs" / "p2", TESTS / "configs" / "p3"
CONFIGS = [GOLDEN, *sorted(P2.glob("*.ir.yaml")), *sorted(P3.glob("*.ir.yaml"))]
_PORT = re.compile(r"^\s*(?:input|output)\s+logic\s*(?:\[[^\]]+\]\s*)?(\w+)", re.M)


def _props(tmp_path: Path, config: Path, style: str | None = None) -> str:
    spec = load(config)
    generate(spec, tmp_path, config.parent, style)
    return (tmp_path / "formal" / f"{spec.project.name}_reset.sv").read_text(encoding="utf-8")


def _opaque(config: Path) -> bool:
    registry = builtin_registry()
    return any((m := registry.get(f.template)) and m.opaque for f in load(config).fabrics)


def test_each_protocol_watches_what_its_port_carries_in_reset(tmp_path: Path) -> None:
    """From demo_ss: an AXI master port (cpu0) gets no response VALID, and its requests are
    assumed quiet; an AXI slave port (ddr0) gets no request VALID; an AHB-Lite master (mcu0)
    sees HREADY high and drives HTRANS IDLE; an AHB-Lite slave (itcm0) gets HTRANS IDLE; an
    APB slave (uart0) gets no PSEL."""
    text = _props(tmp_path, GOLDEN)
    cpu, sys = "always_comb if (bag_held_d_cpu)", "always_comb if (bag_held_d_sys)"
    aon, per = "always_comb if (bag_held_d_aon)", "always_comb if (bag_held_d_per)"
    for line in [
        f"{cpu} quiet_cpu0_axi_bvalid: assert (cpu0_axi_bvalid == 1'd0);",
        f"{cpu} quiet_cpu0_axi_rvalid: assert (cpu0_axi_rvalid == 1'd0);",
        f"{cpu} assume (cpu0_axi_awvalid == 1'd0);",
        f"{sys} quiet_ddr0_axi_awvalid: assert (ddr0_axi_awvalid == 1'd0);",
        f"{sys} assume (ddr0_axi_bvalid == 1'd0);",
        f"{aon} quiet_mcu0_ahb_hready: assert (mcu0_ahb_hready == 1'd1);",
        f"{aon} assume (mcu0_ahb_htrans == 2'd0);",
        f"{aon} quiet_itcm0_ahb_htrans: assert (itcm0_ahb_htrans == 2'd0);",
        f"{per} quiet_uart0_apb_psel: assert (uart0_apb_psel == 1'd0);",
    ]:
        assert f"  {line}\n" in text, line
    assert "quiet_cpu0_axi_awvalid" not in text  # the master drives it: assumed, not asserted


def test_every_asserted_signal_has_a_cover_and_every_group_resets_again(tmp_path: Path) -> None:
    """Covers keep the assertions honest: a signal stuck at its reset value passes vacuously."""
    text = _props(tmp_path, GOLDEN)
    asserted = re.findall(r"quiet_(\w+): assert", text)
    covered = re.findall(r"active_(\w+): cover \(bag_seen_\1\);", text)
    assert asserted and covered == asserted
    assert "reset_again_0: cover ((!rst_aon_n) && (" in text


def test_a_reset_group_asserts_together_and_starts_in_reset(tmp_path: Path) -> None:
    """demo_ss is one reset group of four domains (D51); apb4_min has two groups."""
    text = _props(tmp_path, GOLDEN)
    assert "  always_comb if (bag_first_0) assume (!rst_aon_n);\n" in text
    for r in ("rst_cpu_n", "rst_per_n", "rst_sys_n"):
        assert f"  always_comb assume ((!rst_aon_n) == (!{r}));\n" in text
    two = _props(tmp_path / "two", P2 / "apb4_min.ir.yaml")
    assert "  always_comb if (bag_first_0) assume (rst_a);\n" in two  # active-high
    assert "  always_comb if (bag_first_1) assume (!rst_u_n);\n" in two
    assert "== (" not in two  # nothing ties the two groups together


def test_a_sync_assertion_gets_the_synchronizer_stages_first(tmp_path: Path) -> None:
    """ahb_fixed's reset asserts synchronously: bag_rst_sync passes it on after 2 edges, so
    the domain is quiet from then on (the generated reset test allows the same)."""
    text = _props(tmp_path, P2 / "ahb_fixed.ir.yaml")
    assert "  wire bag_quiet_d_h = bag_held_d_h && bag_edges_d_h >= 2'd2;\n" in text
    assert re.search(r"if \(bag_quiet_d_h\) quiet_\w+: assert", text)
    assert not re.search(r"if \(bag_held_d_h\) quiet_\w+: assert", text)


@pytest.mark.parametrize("config", [c for c in CONFIGS if not _opaque(c)], ids=lambda c: c.stem)
def test_every_port_the_properties_bind_to_is_a_port_of_the_top(tmp_path: Path,
                                                                config: Path) -> None:  # fmt: skip
    """``bind ... (.*)`` connects by name: each input of the property module must be a port
    of the lint top, whatever the signal pattern and prefixes."""
    spec = load(config)
    for style in ("flat", "sv-interface"):
        out = generate(spec, tmp_path / style, config.parent, style)
        top = (tmp_path / style / "rtl" / f"{out.top}.sv").read_text(encoding="utf-8")
        props = (tmp_path / style / "formal" / f"{spec.project.name}_reset.sv").read_text(
            encoding="utf-8"
        )
        module = props.split(f"module {props_module(spec)} (", 1)[1].split(");", 1)[0]
        wanted = set(_PORT.findall(module))
        top_ports = set(_PORT.findall(top.split(");", 1)[0]))
        assert wanted and wanted <= top_ports, sorted(wanted - top_ports)
        assert props.endswith(f"bind {out.top} {props_module(spec)} u_{props_module(spec)} (.*);\n")


def test_the_job_reads_the_file_list_then_the_properties(tmp_path: Path) -> None:
    spec = load(GOLDEN)
    out = generate(spec, tmp_path, GOLDEN.parent)
    assert out.formal == ("formal/demo_ss_reset.sby",)
    job = (tmp_path / out.formal[0]).read_text(encoding="utf-8")
    read = " ".join([*(f"-I {d.as_posix()}" for d in include_dirs(out)), "--top", out.top,
                     *(f.as_posix() for f in sources(out)), "demo_ss_reset.sv"])  # fmt: skip
    assert f"\nread_slang {read}\nprep -top {out.top}\n" in job
    assert f"bmc: depth {BMC_DEPTH}\n" in job and f"cover: depth {COVER_DEPTH}\n" in job
    assert job.endswith("[files]\nrtl ../rtl\ndemo_ss_reset.sv\n")
    assert job.startswith("# // Copyright 2026 Example Corp.\n")  # the File header, commented


def test_no_formal_for_a_user_supplied_fabric_or_when_not_asked(tmp_path: Path) -> None:
    """D8: BAG does not know how a user-supplied fabric behaves."""
    config = P3 / "external_fabric.ir.yaml"
    out = generate(load(config), tmp_path / "ext", config.parent)
    assert out.formal == () and not (tmp_path / "ext" / "formal").exists()
    spec = load(GOLDEN)
    off = spec.model_copy(update={"project": spec.project.model_copy(update={
        "outputs": spec.project.outputs.model_copy(update={"formal": False})})})  # fmt: skip
    out = generate(off, tmp_path / "off", GOLDEN.parent)
    assert out.formal == () and not (tmp_path / "off" / "formal").exists()
