# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""gen.prefix: the token scanner, the rename map and the output paths."""

from importlib.resources import files
from pathlib import Path

import pytest

from hdl_bag.gen.prefix import note, out_path, renames, rewrite, unit
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
        ("/* bag_axi_cut\n bag_axi_cut */ bag_axi_cut", "/* bag_axi_cut\n bag_axi_cut */ p_bag_axi_cut"),
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
    assert out_path(pulp, "soc1") == pulp


def test_the_derived_note_follows_the_spdx_line_and_only_under_a_prefix() -> None:
    text = "// SPDX-License-Identifier: SHL-0.51\n// Derived\n"
    rel = "rtl/vendor/derived/bag_axi_mux.sv"
    assert note(rel, text, "soc1") == (
        '// SPDX-License-Identifier: SHL-0.51\n'
        '// Unit names carry the prefix "soc1_" (hdl-bag Project > Module prefix).\n// Derived\n'
    )
    assert note(rel, text, None) is text
    assert note("rtl/inhouse/bag_sync.sv", text, "soc1") is text


def test_a_name_that_is_not_a_unit_fails_at_render() -> None:
    spec = load(GOLDEN_IR)
    with pytest.raises(GenerateError, match="bag_nope is not an in-house or derived unit"):
        unit(spec, "bag_nope")
    assert unit(spec, "bag_sync") == "bag_sync"
