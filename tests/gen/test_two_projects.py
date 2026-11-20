# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Two prefixed BAG subsystems and an unmodified PULP copy in one compile (issue #106, stage 5).

The tree is the one an integrator builds: ``upstream/`` is the package's own vendored PULP tree,
staged verbatim; ``alpha/`` and ``beta/`` are two different specs generated with two different
prefixes. Two outputs of ONE spec cannot share a compile whatever the prefix, because the top
wrapper and the parameter package keep the user's names, so the specs differ.

The include path is upstream's two directories first, then alpha's, then beta's: the first
directory that holds a path wins, so this is the order in which an unprefixed header of
upstream's would shadow a project's.

Neither flow BAG ships can judge this. ``VerilatorBuild`` keeps errors only and ``lint`` files
every warning under rtl/vendor/ as third-party, unjudged, and a duplicate module is a warning to
Verilator. So the findings come straight from ``run_backend``, as in
tests/rtl/test_negative_elaboration.py. slang compiles each file as its own unit, so it is the
strict judge of duplicate design units; Verilator carries defines across files on one command
line, so it judges cross-file macro and include state.

The positive case would have passed before stage 4 too (a same-version copy couples the
subsystems and elaborates), so the witness for stage 4 is the ``headers`` control, which restores
the upstream header names and must fail. The ``blank`` control is the state before #106.
"""

import json
import re
import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import pytest
from test_module_prefix import _declared
from test_negative_elaboration import USER_RULES, _VerilatorToElaboration

from hdl_bag.flows.backends import BACKENDS
from hdl_bag.flows.base import Finding, run_backend
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.gen.prefix import include_renames, macro_renames
from hdl_bag.ir import load
from hdl_bag.templates.sources import VENDOR, vendored_includes, vendored_units

TESTS = Path(__file__).parents[1]
ALPHA_IR = TESTS / "golden" / "demo_ss.ir.yaml"
BETA_IR = TESTS / "configs" / "p3" / "mixed_adapters_bridges.ir.yaml"
PKG = Path(str(files("hdl_bag"))) / VENDOR
TOOLS = ("slang", "verilator")

# A duplicate definition or a macro redefinition, by rule or by message. The messages are from
# memory of slang and Verilator and are pinned by the first CI run (see the PR description).
DUPLICATE_RULES = {"MODDUP", "REDEFMACRO", "duplicate-definition", "redef-macro"}
DUPLICATE_TEXT = re.compile(
    r"duplicate definition of|redefinition of|Duplicate declaration of module", re.I
)
UNDEFINED_TEXT = re.compile(r"unknown macro or compiler directive|Define or directive not defined")
_QUOTED = re.compile(r"'(\w+)'")
_MACRO = re.compile(r"`(\w+)")


@dataclass(frozen=True)
class Soc:
    files: list[Path]  # relative to root, in compile order
    incdirs: list[Path]
    tops: tuple[str, str]
    beta_files: list[Path]  # beta's own file list, relative to root


def _generate(root: Path, name: str, ir: Path, prefix: str | None, style: str):  # type: ignore[no-untyped-def]
    spec = load(ir)
    spec = spec.model_copy(
        update={"project": spec.project.model_copy(update={"module_prefix": prefix})}
    )
    return generate(spec, root / name, ir.parent, style)


def _restore_headers(tree: Path, prefix: str) -> None:
    """Undo the header renaming in TREE: the four basenames and the include strings, from the
    inverse of gen.prefix.include_renames, so the control cannot drift from the generator."""
    renames = include_renames(prefix)
    for inc, new in renames.items():
        packaged = vendored_includes()[inc]  # rtl/vendor/pulp/axi/include/axi/typedef.svh
        renamed = tree / packaged.removesuffix(inc) / new
        renamed.rename(tree / packaged)
    for path in tree.rglob("*"):
        if path.is_file() and path.suffix in (".sv", ".v", ".svh", ".vh"):
            text = original = path.read_text(encoding="utf-8")
            for inc, new in renames.items():
                text = text.replace(f'`include "{new}"', f'`include "{inc}"')
            if text != original:
                path.write_text(text, encoding="utf-8", newline="\n")


def _soc(
    tmp_path: Path, prefix_a: str | None, prefix_b: str | None, restore_headers: bool = False
) -> Soc:
    """The three trees and the top that instantiates all of them. A control differs from the
    positive case in exactly one argument."""
    shutil.copytree(PKG, tmp_path / "upstream" / VENDOR)
    out_a = _generate(tmp_path, "alpha", ALPHA_IR, prefix_a, "sv-interface")
    out_b = _generate(tmp_path, "beta", BETA_IR, prefix_b, "flat")
    if restore_headers:
        for name, prefix in (("alpha", prefix_a), ("beta", prefix_b)):
            assert prefix
            _restore_headers(tmp_path / name, prefix)
    upstream = [Path("upstream") / VENDOR / p for p in dict.fromkeys(vendored_units().values())]
    index = json.loads((PKG / "index.json").read_text(encoding="utf-8"))
    up_inc = [Path("upstream") / VENDOR / d for d in index["incdirs"]]
    beta_files = [Path("beta") / f for f in sources(out_b)]
    top = tmp_path / "soc_top.sv"
    top.write_text(
        "module soc_top;\n"
        "  localparam int unsigned UpstreamOkay = axi_pkg::RESP_OKAY;\n"
        f"  {out_a.top} u_alpha ();\n"
        f"  {out_b.top} u_beta ();\n"
        "  fifo_v3 u_upstream ();\n"
        "endmodule\n",
        encoding="utf-8",
        newline="\n",
    )
    return Soc(
        [*upstream, *(Path("alpha") / f for f in sources(out_a)), *beta_files, Path("soc_top.sv")],
        [*up_inc, *(Path("alpha") / d for d in include_dirs(out_a)),
         *(Path("beta") / d for d in include_dirs(out_b))],
        (out_a.top, out_b.top),
        beta_files,
    )  # fmt: skip


def _compile(tmp_path: Path, soc: Soc, tool: str) -> list[Finding]:
    backend = BACKENDS[tool] if tool == "slang" else _VerilatorToElaboration()
    return run_backend(backend, soc.files, "soc_top", tmp_path, soc.incdirs)


def _is_duplicate(f: Finding) -> bool:
    return f.rule in DUPLICATE_RULES or bool(DUPLICATE_TEXT.search(f.message))


def _duplicated_names(found: list[Finding]) -> set[str]:
    return {m[1] for f in found if _is_duplicate(f) and (m := _QUOTED.search(f.message))}


def _errors(found: list[Finding]) -> list[Finding]:
    return [f for f in found if f.severity == "error" or f.rule in USER_RULES]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.eda
@pytest.mark.parametrize("tool", TOOLS)
def test_two_prefixed_projects_elaborate_beside_an_unmodified_pulp_copy(
    tmp_path: Path, tool: str
) -> None:
    soc = _soc(tmp_path, "alpha", "beta")
    assert _tree_bytes(tmp_path / "upstream" / VENDOR) == _tree_bytes(PKG)
    assert len(set(soc.tops)) == 2
    text = (tmp_path / "soc_top.sv").read_text(encoding="utf-8")
    assert all(t in text for t in soc.tops)
    found = _compile(tmp_path, soc, tool)
    assert _errors(found) == [], "\n".join(f.format() for f in _errors(found))
    duplicates = [f.format() for f in found if _is_duplicate(f)]
    assert duplicates == []


def _blank_expectation(tmp_path: Path, soc: Soc) -> set[str]:
    """The units blank beta declares that upstream declares too: what must be reported."""
    declared: set[str] = set()
    for f in soc.beta_files:
        if f.suffix in (".sv", ".v"):
            declared |= _declared((tmp_path / f).read_text(encoding="utf-8"))
    return declared & set(vendored_units())


def _check_blank(tmp_path: Path, soc: Soc, found: list[Finding], tool: str) -> None:
    expected = _blank_expectation(tmp_path, soc)
    assert expected
    names = _duplicated_names(found)
    if tool == "slang":
        assert names == expected
    else:  # Verilator stops reporting at its error limit (50)
        assert names and names <= expected
    assert not [n for n in names if n.startswith("alpha_")]
    assert all(_is_duplicate(f) for f in _errors(found)), [f.format() for f in _errors(found)]


def _check_headers(found: list[Finding], tool: str) -> None:
    errors = _errors(found)
    assert errors
    assert all(UNDEFINED_TEXT.search(f.message) for f in errors), [f.format() for f in errors]
    names = {m[1] for f in errors if (m := _MACRO.search(f.message))}
    assert names
    prefixes = {n.split("_", 1)[0] for n in names}
    # Verilator may stop at its error limit before it reaches beta's files.
    assert prefixes == {"alpha", "beta"} if tool == "slang" else prefixes <= {"alpha", "beta"}
    assert {n.split("_", 1)[1] for n in names} <= set(macro_renames("p"))


CONTROLS = [(c, t) for c in ("blank", "headers") for t in TOOLS]


@pytest.mark.eda
@pytest.mark.parametrize(("control", "tool"), CONTROLS, ids=[f"{c}-{t}" for c, t in CONTROLS])
def test_the_joint_compile_fails_for_the_right_reason(
    tmp_path: Path, control: str, tool: str
) -> None:
    """``blank``: beta without a prefix redeclares upstream's units, the state before #106.
    ``headers``: both prefixed but the header names restored, so every include resolves to
    upstream's header and the prefixed macros are undefined, the state before stage 4."""
    if control == "blank":
        soc = _soc(tmp_path, "alpha", None)
    else:
        soc = _soc(tmp_path, "alpha", "beta", restore_headers=True)
    found = _compile(tmp_path, soc, tool)
    if control == "blank":
        _check_blank(tmp_path, soc, found, tool)
    else:
        _check_headers(found, tool)
