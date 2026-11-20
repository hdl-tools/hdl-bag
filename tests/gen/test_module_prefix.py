# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Project > Module prefix in the generated RTL (stage 2 of issue #106): the in-house modules,
the modules BAG derives from PULP and the sub-wrappers take it, nothing else does yet, and a
blank prefix changes nothing."""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import pytest
from simrun import REPO
from test_corpus import ALL, STYLES, WAIVED, _split, _waived_snapshot

from hdl_bag.flows import compile_design, lint
from hdl_bag.flows.formal import run_formal
from hdl_bag.flows.sim import run_tests
from hdl_bag.gen import MANIFEST, generate, include_dirs, sources
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir import Spec, load, loads
from hdl_bag.templates import builtin_registry

PREFIX = "zz9"
Pair = tuple[Spec, dict[str, bytes], dict[str, bytes]]  # the spec, blank tree, prefixed tree
Trees = Callable[[str, str | None], Pair]
BUILTIN = files("hdl_bag") / "templates" / "builtin"
GOLDEN = "golden/demo_ss"
INHOUSE, DERIVED = "rtl/inhouse/", "rtl/vendor/derived/"


def _stems(sub: str) -> set[str]:
    return {p.name.removesuffix(".sv") for p in (files("hdl_bag") / sub).iterdir()
            if p.name.endswith(".sv")}  # fmt: skip


# Computed from the package directories, not from gen.prefix, so the tests do not depend on it.
BAG = _stems("rtl/inhouse") | _stems("rtl/vendor/derived")
_STRIP = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"", re.DOTALL)
_IDENT = re.compile(r"[A-Za-z_]\w*")
_DECL = re.compile(
    r"^\s*(?:module|macromodule|interface|package)\s+(?:static\s+|automatic\s+)?(\w+)", re.M
)


def _with_prefix(spec: Spec) -> Spec:
    project = spec.project.model_copy(update={"module_prefix": PREFIX})
    return spec.model_copy(update={"project": project})


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _declared(text: str) -> set[str]:
    return set(_DECL.findall(_STRIP.sub("", text)))


def _tokens(text: str) -> set[str]:
    return set(_IDENT.findall(_STRIP.sub("", text)))


# ---- Every template names BAG's units through unit() -------------------------------------


def _templates() -> list[Path]:
    return sorted(Path(str(BUILTIN)).glob("*/instance.sv.j2"))


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.parent.name)
def test_no_template_names_a_bag_unit_without_the_prefix(template: Path) -> None:
    text = re.sub(r"\{#.*?#\}", "", template.read_text(encoding="utf-8"), flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    called = re.findall(r"unit\(\s*\"(\w+)\"\s*\)", text)
    bare = _tokens(re.sub(r"unit\(\s*\"\w+\"\s*\)", "", text)) & BAG
    assert not bare, f"{template.parent.name} names {sorted(bare)} without unit()"
    assert set(called) <= BAG, f"unit() of an unknown name: {sorted(set(called) - BAG)}"
    manifest = builtin_registry().get(template.parent.name)
    assert manifest is not None
    if not manifest.opaque:
        assert called, f"{template.parent.name} instantiates no BAG unit through unit()"


# ---- A blank prefix changes nothing ------------------------------------------------------


@pytest.mark.parametrize("config", [GOLDEN, "p3/axi_shared_rr", "p3/mixed_adapters_bridges"])
def test_a_blank_prefix_copies_every_file_as_packaged(config: str, tmp_path: Path) -> None:
    configs, name = _split(config)
    generate(load(configs / f"{name}.ir.yaml"), tmp_path, configs)
    copied = [r for r in _tree(tmp_path) if r.startswith((INHOUSE, "rtl/vendor/"))]
    assert copied
    for rel in copied:
        if rel.endswith("/LICENSE"):
            continue
        assert (tmp_path / rel).read_bytes() == (files("hdl_bag") / rel).read_bytes(), rel


# ---- The corpus under a prefix -----------------------------------------------------------

CASES = [pytest.param(c, None, id=c) for c in ALL] + [
    pytest.param(GOLDEN, "sv-interface", id=f"{GOLDEN}-sv-interface")
]


def _expect(spec: Spec) -> Callable[[str], str]:
    subs = {f"rtl/{w.name}.sv" for w in spec.hierarchy.wrappers}

    def expect(rel: str) -> str:
        head, _, base = rel.rpartition("/")
        if rel in subs or head in (INHOUSE.rstrip("/"), DERIVED.rstrip("/")):
            return f"{head}/{PREFIX}_{base}"
        return rel

    return expect


@pytest.fixture(scope="module")
def trees(tmp_path_factory: pytest.TempPathFactory) -> Trees:
    cache: dict[tuple[str, str | None], Pair] = {}

    def get(config: str, style: str | None) -> Pair:
        if (config, style) not in cache:
            configs, name = _split(config)
            spec = load(configs / f"{name}.ir.yaml")
            base = tmp_path_factory.mktemp("prefix")
            generate(spec, base / "blank", configs, style)
            generate(_with_prefix(spec), base / "pre", configs, style)
            cache[(config, style)] = (spec, _tree(base / "blank"), _tree(base / "pre"))
        return cache[(config, style)]

    return get


@pytest.mark.parametrize(("config", "style"), CASES)
def test_the_prefix_reaches_in_house_derived_and_sub_wrapper_files_and_no_others(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """A. The generator-side statement of checks/wrapper._fixed_units: the top, the package,
    <top>_flat, <project>_<proto>_if, rtl/ext and every formal file keep their paths."""
    spec, blank, pre = trees(config, style)
    expect = _expect(spec)
    assert set(pre) == {expect(rel) for rel in blank}
    assert any(r.startswith(f"{INHOUSE}{PREFIX}_") for r in pre)


@pytest.mark.parametrize(("config", "style"), CASES)
def test_no_generated_source_names_an_unprefixed_bag_unit(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """B. Outside the vendored PULP and ZipCPU trees, no identifier is a bare in-house or
    derived unit name (comments and strings keep theirs: messages are left untouched)."""
    _, _, pre = trees(config, style)
    for rel, data in pre.items():
        if rel.endswith(".sv") and not rel.startswith(("rtl/vendor/pulp/", "formal/vendor/")):
            assert not _tokens(data.decode()) & BAG, rel


@pytest.mark.parametrize(("config", "style"), CASES)
def test_a_copied_file_declares_the_prefixed_units_of_its_packaged_file(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """C. Not 'one unit named as its stem': a file may hold several units."""
    spec, blank, pre = trees(config, style)
    expect = _expect(spec)
    for rel in (r for r in blank if r.startswith((INHOUSE, DERIVED))):
        packaged = (files("hdl_bag") / rel).read_text(encoding="utf-8")
        want = {f"{PREFIX}_{u}" for u in _declared(packaged)}
        assert _declared(pre[expect(rel)].decode()) == want


@pytest.mark.parametrize(("config", "style"), CASES)
def test_a_parent_instantiates_the_prefixed_sub_wrapper(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """D. The module takes the prefix; the instance name keeps the spec's."""
    spec, _, pre = trees(config, style)
    texts = [d.decode() for r, d in pre.items() if r.endswith(".sv")]
    for w in spec.hierarchy.wrappers:
        assert any(f"{PREFIX}_{w.name} u_{w.name} (" in t for t in texts)
        assert not any(f"module {w.name} (" in t for t in texts)
        assert f"rtl/{PREFIX}_{w.name}.sv" in pre


@pytest.mark.parametrize(("config", "style"), CASES)
def test_the_file_list_the_jobs_and_the_manifest_follow_the_renamed_files(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """E."""
    spec, blank, pre = trees(config, style)
    expect = _expect(spec)
    name = spec.project.name
    old, new = blank[f"{name}.f"].decode().split(), pre[f"{name}.f"].decode().split()
    assert new == [t if t.startswith("+") else expect(t) for t in old]
    for rel, data in pre.items():
        if rel.startswith("formal/") and rel.endswith(".sby"):
            for token in re.findall(r"rtl/[\w/.-]+\.sv", data.decode()):
                assert token in pre, f"{rel} names {token}, which was not written"
    manifest = json.loads(pre[MANIFEST])
    assert set(manifest["files"]) == set(pre) - {MANIFEST}
    assert manifest["top"] == json.loads(blank[MANIFEST])["top"]


@pytest.mark.parametrize(("config", "style"), CASES)
def test_the_vendored_and_formal_trees_are_not_touched_yet(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """F. Stage 3 changes this on purpose."""
    _, blank, pre = trees(config, style)
    keep = [r for r in blank if r.startswith(("rtl/vendor/pulp/", "formal/vendor/"))
            or r in ("formal/bag_step.v", "formal/bag_faxi4.sv", "formal/bag_fahb.sv")]  # fmt: skip
    for rel in keep:
        assert pre[rel] == blank[rel], rel


def test_the_corpus_uses_every_builtin_template() -> None:
    """So a new template without a config fails here, not by escaping the corpus tests."""
    specs = [load(_split(c)[0] / f"{_split(c)[1]}.ir.yaml") for c in ALL]
    used = {
        x.template for s in specs for group in (s.fabrics, s.bridges, s.adapters) for x in group
    }
    assert used == set(builtin_registry().names())


def test_the_reset_page_names_the_prefixed_synchroniser(
    trees: Trees,
) -> None:
    _, blank, pre = trees(GOLDEN, None)
    assert f"in {PREFIX}_bag_rst_sync (" in pre["doc/reset.md"].decode()
    assert "in bag_rst_sync (" in blank["doc/reset.md"].decode()


def test_the_derived_files_say_the_prefix_and_in_house_files_do_not(
    trees: Trees,
) -> None:
    _, blank, pre = trees("p3/axi_shared_rr", None)
    note = f'// Unit names carry the prefix "{PREFIX}_" (hdl-bag Project > Module prefix).'
    derived = [r for r in pre if r.startswith(f"{DERIVED}{PREFIX}_")]
    assert derived
    for rel in derived:
        lines = pre[rel].decode().split("\n")
        assert lines[0].startswith("// SPDX-") and lines[1] == note
    assert not any(note.encode() in d for r, d in pre.items() if r.startswith(INHOUSE))
    assert not any(note.encode() in d for d in blank.values())


def test_changing_the_prefix_leaves_no_file_of_the_old_name(tmp_path: Path) -> None:
    configs, name = _split("p2/ahb2apb_nested")
    spec = load(configs / f"{name}.ir.yaml")
    generate(spec, tmp_path, configs)
    generate(_with_prefix(spec), tmp_path, configs)
    after = _tree(tmp_path)
    old = {f"{b}.sv" for b in BAG}
    assert not [r for r in after if r.startswith((INHOUSE, DERIVED)) and Path(r).name in old]
    assert any(r.startswith(f"{INHOUSE}{PREFIX}_") for r in after)


def test_a_prefixed_design_is_byte_identical_across_hash_seeds(tmp_path: Path) -> None:
    ir = tmp_path / "prefixed.ir.yaml"
    src = REPO / "tests/configs/p2/ahb2apb_nested.ir.yaml"
    ir.write_text(src.read_text().replace("module_prefix: null", f"module_prefix: {PREFIX}"))
    shutil_header = src.parent / "header.txt"
    (tmp_path / "header.txt").write_bytes(shutil_header.read_bytes())
    results = []
    for seed in ("0", "12345"):
        out = tmp_path / seed
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run([sys.executable, "-m", "hdl_bag.cli", "generate", str(ir), "-o", str(out)],
                       check=True, env=env, capture_output=True)  # fmt: skip
        results.append(_tree(out))
    assert results[0] == results[1]
    assert any(r.startswith(f"{INHOUSE}{PREFIX}_") for r in results[0])


# ---- EDA: a prefixed design is as clean as an unprefixed one ----------------------------

EDA = [(GOLDEN, "flat"), (GOLDEN, "sv-interface"), ("p2/nested_deep", "flat"),
       ("p3/axi_shared_rr", "flat"), ("p3/mixed_adapters_bridges", "flat")]  # fmt: skip


@pytest.mark.eda
@pytest.mark.parametrize(("config", "style"), EDA)
def test_a_prefixed_design_lints_and_elaborates(config: str, style: str) -> None:
    assert style in STYLES
    configs, name = _split(config)
    root = REPO / "out" / "prefix" / f"{name}_{style}"
    out = generate(_with_prefix(load(configs / f"{name}.ir.yaml")), root, configs, style)
    files_, inc = sources(out), include_dirs(out)
    key = f"{config}-{style}"
    lint_report = lint(files_, out.top, root, incdirs=inc)
    for report in (lint_report, compile_design(files_, out.top, root, incdirs=inc, build=False)):
        assert report.ok, report.format()
    assert _waived_snapshot(lint_report.findings) == WAIVED[key], lint_report.format()


@pytest.mark.eda
def test_a_prefixed_ahb_design_simulates_its_routes() -> None:
    configs, name = _split("p2/ahb_fixed")
    root = REPO / "out" / "prefix" / f"{name}_route"
    out = generate(_with_prefix(load(configs / f"{name}.ir.yaml")), root, configs)
    report = run_tests(out)
    assert report.ok, report.format()


@pytest.mark.eda
def test_a_prefixed_ahb_design_passes_formal() -> None:
    configs, name = _split("p2/ahb_fixed")
    root = REPO / "out" / "prefix" / f"{name}_formal"
    out = generate(_with_prefix(load(configs / f"{name}.ir.yaml")), root, configs)
    report = run_formal(out)
    assert report.ok, report.format()


# ---- A prefixed sub-wrapper name never equals a unit the prefix does not reach ------------

NESTED = REPO / "tests/configs/p3/nested_adapters.ir.yaml"


def _renamed_sub_wrapper(new: str, prefix: str) -> Spec:
    text = re.sub(r"\bss_a\b", new, NESTED.read_text(encoding="utf-8"))
    spec = loads(text)
    project = spec.project.model_copy(update={"module_prefix": prefix})
    return spec.model_copy(update={"project": project})


def _declarations(root: Path) -> list[str]:
    return [
        name
        for p in sorted(root.rglob("*.sv"))
        for name in _declared(_STRIP.sub("", p.read_text(encoding="utf-8")))
    ]


def test_a_prefixed_sub_wrapper_cannot_take_the_name_of_an_unprefixed_unit(
    tmp_path: Path,
) -> None:
    """Sub-wrapper cut under prefix axi is axi_cut, PULP's axi_cut, which the prefix does not
    reach: the generator must refuse, not write two modules of one name."""
    spec = _renamed_sub_wrapper("cut", "axi")
    try:
        generate(spec, tmp_path, NESTED.parent)
    except GenerateError as exc:
        assert "axi_cut" in str(exc)
        return
    names = _declarations(tmp_path)
    assert sorted(n for n in set(names) if names.count(n) > 1) == []
    raise AssertionError("generate() succeeded; expected a refusal naming axi_cut")


def test_a_prefixed_sub_wrapper_and_an_unprefixed_unit_get_distinct_module_names(
    tmp_path: Path,
) -> None:
    """Sub-wrapper axi_cut under prefix p is p_axi_cut next to PULP's axi_cut: distinct.

    This is the generator's side only. `bag check` refuses this workbook anyway, for the clash
    stage 3 of #106 will create once the PULP units take the prefix too -- see
    test_a_sub_wrapper_named_like_a_pulp_unit_is_refused_for_the_stage_3_clash. What is pinned
    here is that generation itself never writes two modules under one name.
    """
    generate(_renamed_sub_wrapper("axi_cut", "p"), tmp_path, NESTED.parent)
    names = _declarations(tmp_path)
    assert {"p_axi_cut", "axi_cut"} <= set(names)
    assert len(names) == len(set(names))
