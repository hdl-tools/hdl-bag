# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""gen.prefix: the token scanner, the rename map and the output paths."""

from importlib.resources import files
from pathlib import Path

import pytest

from hdl_bag.gen.prefix import note, out_path, renames, rewrite, source_note, transform, unit
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
        "rtl/vendor/pulp/axi/include/axi/typedef.svh"
    )
    assert out_path("formal/vendor/zipcpu/LICENSE", "soc1") == "formal/vendor/zipcpu/LICENSE"


def test_the_derived_note_follows_the_spdx_line_and_only_under_a_prefix() -> None:
    text = "// SPDX-License-Identifier: SHL-0.51\n// Derived\n"
    rel = "rtl/vendor/derived/bag_axi_mux.sv"
    assert note(rel, text, "soc1") == (
        "// SPDX-License-Identifier: SHL-0.51\n"
        '// Unit names carry the prefix "soc1_" (hdl-bag Project > Module prefix).\n// Derived\n'
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
        '// Modified by hdl-bag: design unit names carry the prefix "p_" (Project > Module prefix).'
        " No other change.\n// Copyright ETH\nmodule p_axi_cut;\n"
    )
    plain = "// Copyright ETH\n`define X 1\n"
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
