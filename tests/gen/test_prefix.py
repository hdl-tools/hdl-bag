# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""gen.prefix: the token scanner, the rename map and the output paths."""

import re
from importlib.resources import files
from pathlib import Path

import pytest

from hdl_bag.gen.prefix import (
    include_renames,
    macro_renames,
    note,
    out_path,
    renames,
    rewrite,
    source_note,
    transform,
    unit,
)
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir import load
from hdl_bag.naming import emitted_units

GOLDEN_IR = Path(__file__).parents[1] / "golden" / "demo_ss.ir.yaml"
M = {"bag_axi_cut": "p_bag_axi_cut"}
APPLIED = [u for u in emitted_units().values() if u.origin in ("inhouse", "derived")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("bag_axi_cut #(", "p_bag_axi_cut #("),
        ("bag_axi_cut_x u;", "bag_axi_cut_x u;"),
        ("x_bag_axi_cut u;", "x_bag_axi_cut u;"),
        ("a // bag_axi_cut\nbag_axi_cut", "a // bag_axi_cut\np_bag_axi_cut"),
        ("/* bag_axi_cut\n */ bag_axi_cut", "/* bag_axi_cut\n */ p_bag_axi_cut"),
        ('$error("bag_axi_cut: bad"); bag_axi_cut', '$error("bag_axi_cut: bad"); p_bag_axi_cut'),
        ("`bag_axi_cut", "`bag_axi_cut"),
        ("$bag_axi_cut", "$bag_axi_cut"),
        ("\\bag_axi_cut ", "\\bag_axi_cut "),
        ("endmodule : bag_axi_cut", "endmodule : p_bag_axi_cut"),
        ("8'hbag_axi_cut", "8'hbag_axi_cut"),
    ],
)  # fmt: skip
def test_rewrite_renames_whole_identifiers_outside_comments_and_strings(
    text: str, expected: str
) -> None:
    assert rewrite(text, M) == expected


def test_an_empty_map_returns_the_text_itself() -> None:
    text = "module bag_axi_cut;"
    assert rewrite(text, {}) is text
    assert renames(None) == {} == renames("")


@pytest.mark.parametrize("unit_", APPLIED, ids=lambda u: u.name)
def test_rewrite_only_adds_the_prefix_to_every_in_house_and_derived_file(unit_: object) -> None:
    """The stem identity (declared unit == file stem) holds because this runs only over
    in-house and derived files; formal files declare several units per file."""
    m = renames("zz9")
    path = unit_.path  # type: ignore[attr-defined]
    text = (files("hdl_bag") / path).read_text(encoding="utf-8")
    out = rewrite(text, m)
    assert out.replace("zz9_", "") == text
    stem = path.rsplit("/", 1)[1].removesuffix(".sv")
    assert f"module zz9_{stem}" in out


def test_out_path_prefixes_the_basename_of_applied_files_only() -> None:
    assert out_path("rtl/inhouse/bag_axi_cut.sv", "soc1") == "rtl/inhouse/soc1_bag_axi_cut.sv"
    assert out_path("rtl/vendor/derived/bag_axi_mux.sv", "soc1") == (
        "rtl/vendor/derived/soc1_bag_axi_mux.sv"
    )
    assert out_path("rtl/inhouse/bag_axi_cut.sv", None) == "rtl/inhouse/bag_axi_cut.sv"
    pulp = next(u.path for u in emitted_units().values() if u.origin == "pulp")
    head, _, base = pulp.rpartition("/")
    assert out_path(pulp, "soc1") == f"{head}/soc1_{base}"
    assert out_path("formal/bag_step.v", "soc1") == "formal/soc1_bag_step.v"
    assert out_path("rtl/vendor/pulp/axi/include/axi/typedef.svh", "soc1") == (
        "rtl/vendor/pulp/axi/include/axi/soc1_typedef.svh"
    )
    assert out_path("rtl/vendor/pulp/axi/include/axi/typedef.svh", None) == (
        "rtl/vendor/pulp/axi/include/axi/typedef.svh"
    )
    assert out_path("formal/vendor/zipcpu/LICENSE", "soc1") == "formal/vendor/zipcpu/LICENSE"


def test_the_derived_note_follows_the_spdx_line_and_only_under_a_prefix() -> None:
    text = "// SPDX-License-Identifier: SHL-0.51\n// Derived\n"
    rel = "rtl/vendor/derived/bag_axi_mux.sv"
    assert note(rel, text, "soc1") == (
        "// SPDX-License-Identifier: SHL-0.51\n"
        '// Unit, macro and include-file names carry the prefix "soc1_"'
        " (Project > Module prefix).\n// Derived\n"
    )
    assert note(rel, text, None) is text
    assert note("rtl/inhouse/bag_sync.sv", text, "soc1") is text


def test_a_name_that_is_not_a_unit_fails_at_render() -> None:
    spec = load(GOLDEN_IR)
    with pytest.raises(GenerateError, match="bag_nope is not a unit BAG emits"):
        unit(spec, "bag_nope")
    assert unit(spec, "bag_sync") == "bag_sync"
    assert unit(spec, "bag_faxi4") == "bag_faxi4"


def test_only_rtl_files_pass_through_the_rewrite() -> None:
    text = "axi_cut is licensed under the Solderpad licence"
    assert transform("rtl/vendor/pulp/axi/LICENSE", text, "p") is text
    assert transform("formal/vendor/zipcpu/SOURCE.md", text, "p") is text


def test_an_upstream_file_the_rewrite_changed_gets_the_note_as_line_one() -> None:
    rel = "rtl/vendor/pulp/axi/src/axi_cut.sv"
    out = transform(rel, "// Copyright ETH\nmodule axi_cut;\n", "p")
    assert out == (
        '// Modified by hdl-bag: design unit, macro and include-file names carry the prefix "p_"'
        " (Project > Module prefix). No other change.\n// Copyright ETH\nmodule p_axi_cut;\n"
    )
    plain = "// Copyright ETH\nint x;\n"
    assert transform("rtl/vendor/pulp/axi/include/axi/assign.svh", plain, "p") == plain
    assert transform(rel, "module axi_cut;\n", None) == "module axi_cut;\n"
    assert not transform("rtl/inhouse/bag_sync.sv", "module bag_sync;\n", "p").startswith(
        "// Modified"
    )
    assert not transform("formal/bag_step.v", "module bag_step_ff0;\n", "p").startswith(
        "// Modified"
    )


def test_the_source_note_is_one_appended_line_and_only_under_a_prefix() -> None:
    text = "Files ... unmodified.\n"
    assert source_note(text, None) is text
    out = source_note(text, "p")
    # A blank line between: Markdown would otherwise fold the sentence into the last paragraph.
    assert out.startswith(f"{text}\n") and out.count("\n") == 3 and '"p_"' in out


# ---- Macros, include guards and header names (stage 4 of #106) ------------------------------

MAC = {"FOO": "p_FOO", "__AXI": "p___AXI"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("`define FOO 1", "`define p_FOO 1"),
        ("`define FOO(a) a", "`define p_FOO(a) a"),
        ("`ifdef FOO", "`ifdef p_FOO"),
        ("`ifndef FOO", "`ifndef p_FOO"),
        ("`elsif FOO", "`elsif p_FOO"),
        ("`undef\tFOO", "`undef\tp_FOO"),
        ("x = `FOO(x);", "x = `p_FOO(x);"),
        ("`define __AXI 1", "`define p___AXI 1"),
        ("`ifdef VERILATOR", "`ifdef VERILATOR"),
        ("`__FILE__ `__LINE__", "`__FILE__ `__LINE__"),
        ("// `define FOO 1", "// `define FOO 1"),
        ('$display("`FOO");', '$display("`FOO");'),
        ("FOO", "FOO"),
        ("x``FOO y``_z", "x``FOO y``_z"),
        ("`FOO``_z", "`p_FOO``_z"),
    ],
)  # fmt: skip
def test_a_macro_is_renamed_where_it_is_defined_tested_or_used(text: str, expected: str) -> None:
    assert rewrite(text, {}, MAC) == expected


def test_a_define_operand_is_left_alone_when_the_macro_map_is_empty() -> None:
    """A macro named like a unit used to be renamed by the bare-identifier alternative."""
    text = "`define bag_axi_cut 1\n`ifdef bag_axi_cut\n`endif\n"
    assert rewrite(text, M) == text


def test_a_macro_and_a_unit_of_one_spelling_are_each_renamed_once_in_their_own_namespace() -> None:
    text = "`define bag_axi_cut 1\nbag_axi_cut u (`bag_axi_cut);\n"
    out = rewrite(text, M, {"bag_axi_cut": "p_bag_axi_cut"})
    assert out == "`define p_bag_axi_cut 1\np_bag_axi_cut u (`p_bag_axi_cut);\n"
    # One pass: a name that is already the other's output is not renamed twice.
    out = rewrite("`FOO p_FOO", {"p_FOO": "p_p_FOO"}, MAC)
    assert out == "`p_FOO p_p_FOO"


def test_a_multi_line_define_keeps_every_continuation() -> None:
    text = "`define FOO(a, b) \\\n  assign a = b; \\\n  assign b = `FOO(a)\n"
    out = rewrite(text, {}, MAC)
    assert out == text.replace("FOO", "p_FOO")
    assert out.count("\n") == text.count("\n") and out.count("\\\n") == 2


def test_the_include_of_a_vendored_header_is_rewritten_and_an_external_one_is_not() -> None:
    inc = {"axi/typedef.svh": "axi/p_typedef.svh"}
    assert rewrite('`include "axi/typedef.svh"', {}, {}, inc) == '`include "axi/p_typedef.svh"'
    assert rewrite('`include\t"axi/typedef.svh" // x', {}, {}, inc) == (
        '`include\t"axi/p_typedef.svh" // x'
    )
    assert rewrite('`include "uvm_macros.svh"', {}, {}, inc) == '`include "uvm_macros.svh"'
    assert rewrite('// `include "axi/typedef.svh"', {}, {}, inc) == '// `include "axi/typedef.svh"'
    assert rewrite('x = "axi/typedef.svh";', {}, {}, inc) == 'x = "axi/typedef.svh";'


def _packaged_rtl() -> list[str]:
    root = files("hdl_bag")
    out = []
    for tree in ("rtl", "formal"):
        stack = [root / tree]
        while stack:
            for f in stack.pop().iterdir():
                if f.is_dir():
                    stack.append(f)
                elif f.name.endswith((".sv", ".v", ".svh", ".vh")):
                    out.append(f.read_text(encoding="utf-8"))
    return out


def test_the_macro_map_is_exactly_the_set_of_names_packaged_files_define() -> None:
    """Computed here with line-based matching over comment-stripped text, not by gen.prefix."""
    strip = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
    defined = {
        m
        for text in _packaged_rtl()
        for m in re.findall(r"^[ \t]*`define[ \t]+(\w+)", strip.sub("", text), re.M)
    }
    got = macro_renames("soc1")
    assert set(got) == defined and len(defined) == 193
    assert {
        "AXI_ASSIGN_SVH_", "AXI_TYPEDEF_SVH_", "COMMON_CELLS_ASSERTIONS_SVH",
        "COMMON_CELLS_REGISTERS_SVH_", "TARGET_VSIM", "SLAVE_ASSUME", "SLAVE_ASSERT",
        "NO_SYNOPSYS_FF", "ASSERT_RPT", "INC_ASSERT", "__AXI_TO_AW",
    } <= got.keys()  # fmt: skip
    assert not got.keys() & {"VERILATOR", "SYNTHESIS", "UVM", "QUESTA", "__FILE__", "uvm_error"}
    assert got["__AXI_TO_AW"] == "soc1___AXI_TO_AW"
    assert got["AXI_TYPEDEF_ALL"] == "soc1_AXI_TYPEDEF_ALL"
    assert macro_renames(None) == {} == macro_renames("")


def test_the_include_map_names_each_vendored_header_by_its_include_string() -> None:
    assert include_renames("soc1") == {
        "axi/assign.svh": "axi/soc1_assign.svh",
        "axi/typedef.svh": "axi/soc1_typedef.svh",
        "common_cells/assertions.svh": "common_cells/soc1_assertions.svh",
        "common_cells/registers.svh": "common_cells/soc1_registers.svh",
    }
    assert include_renames(None) == {}


def test_a_blank_prefix_transforms_a_header_to_itself() -> None:
    text = '`include "axi/typedef.svh"\n`define X 1\n'
    assert transform("rtl/vendor/pulp/axi/include/axi/assign.svh", text, None) is text


def test_a_prefixed_header_rewrite_is_reversible_and_keeps_its_lines() -> None:
    rel = "rtl/vendor/pulp/axi/include/axi/typedef.svh"
    text = (files("hdl_bag") / rel).read_text(encoding="utf-8")
    out = rewrite(text, renames("soc1"), macro_renames("soc1"), include_renames("soc1"))
    assert out.count("\n") == text.count("\n") and out != text
    assert out.replace("soc1_", "") == text


def test_no_pasted_fragment_in_a_packaged_file_is_a_macro_or_unit_name() -> None:
    """Pasting (a``b) is skipped by the rewrite, which is only right while no fragment is a
    name that would have been renamed."""
    names = set(macro_renames("p")) | set(renames("p"))
    strip = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
    fragments = {
        f
        for text in _packaged_rtl()
        for chain in re.findall(r"\w*(?:``\w*)+", strip.sub("", text))
        for f in chain.split("``")
    }
    assert len(fragments) > 20 and not fragments & names


def test_no_packaged_file_writes_an_escaped_identifier() -> None:
    """gen.prefix skips an escaped identifier (backslash, name, space), which IEEE 1800 says is
    the same name as the plain one, so a vendored one would be silently missed. None exists
    today; this fires at the next re-vendor that brings one in."""
    strip = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"", re.DOTALL)
    found = {m for text in _packaged_rtl() for m in re.findall(r"\\\S+", strip.sub("", text))}
    assert not found, sorted(found)
