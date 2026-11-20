# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Project > Module prefix in the generated RTL (stages 2 to 4 of issue #106): every unit BAG
emits takes it (in-house, derived, vendored PULP, BAG's formal units, vendored ZipCPU, and the
sub-wrappers), so do the macros the packaged files define, their include guards and the vendored
headers' file names; the formal jobs follow the renamed files, and a blank prefix changes
nothing."""

import json
import os
import posixpath
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
from hdl_bag.gen.hierarchy import plan, problems
from hdl_bag.gen.netlist import build_netlist
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
PULP, ZIPCPU = "rtl/vendor/pulp/", "formal/vendor/zipcpu/"
NOTE = (
    '// Modified by hdl-bag: design unit, macro and include-file names carry the prefix "{}_" '
    "(Project > Module prefix). No other change."
)
_STRIP_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_STRIP = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"", re.DOTALL)
_IDENT = re.compile(r"[A-Za-z_]\w*")
_DECL = re.compile(
    r"^\s*(?:module|macromodule|interface|package)\s+(?:static\s+|automatic\s+)?(\w+)", re.M
)


def _package_units() -> set[str]:
    """Every unit name BAG emits, from the package directories and the PULP index."""
    names: set[str] = set()
    for sub in ("rtl/inhouse", "rtl/vendor/derived", "formal", "formal/vendor/zipcpu"):
        for f in (files("hdl_bag") / sub).iterdir():
            if f.name.endswith((".sv", ".v")):
                names |= _declared(f.read_text(encoding="utf-8"))
    index = json.loads((files("hdl_bag") / "rtl/vendor/pulp/index.json").read_text("utf-8"))
    for h in index["headers"]:  # assert_rpt_pkg is declared in a header
        names |= _declared((files("hdl_bag") / f"rtl/vendor/pulp/{h['path']}").read_text("utf-8"))
    return names | {u for f in index["files"] for u in f["defines"]}


FORMAL_OWN = {f.name for f in (files("hdl_bag") / "formal").iterdir()
              if f.name.endswith((".sv", ".v"))}  # fmt: skip
_DIRECTIVES = ("define", "ifdef", "ifndef", "elsif", "undef")
_BASED = re.compile(r"'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+")
_RTL_SUFFIX = (".sv", ".v", ".svh", ".vh")


def _packaged_texts() -> list[str]:
    out: list[str] = []
    stack = [files("hdl_bag") / "rtl", files("hdl_bag") / "formal"]
    while stack:
        for f in stack.pop().iterdir():
            if f.is_dir():
                stack.append(f)
            elif f.name.endswith(_RTL_SUFFIX):
                out.append(f.read_text(encoding="utf-8"))
    return out


def _defined_macros() -> set[str]:
    """Every name a packaged file defines, read line by line from comment-stripped text."""
    names: set[str] = set()
    for text in _packaged_texts():
        for line in re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.DOTALL).split("\n"):
            words = line.split()
            if len(words) > 1 and words[0] == "`define":
                names.add(re.match(r"\w+", words[1])[0])  # type: ignore[index]
    return names


def _header_includes() -> dict[str, str]:
    """Include string -> the string once the header's basename carries the prefix."""
    index = json.loads((files("hdl_bag") / "rtl/vendor/pulp/index.json").read_text("utf-8"))
    out = {}
    for h in index["headers"]:
        inc = next(d for d in index["incdirs"] if h["path"].startswith(d + "/"))
        rel = h["path"][len(inc) + 1 :]
        head, _, base = rel.rpartition("/")
        out[rel] = f"{head}/{PREFIX}_{base}"
    return out


MACROS, HEADERS = _defined_macros(), _header_includes()


def _ident_end(text: str, i: int) -> int:
    while i < len(text) and (text[i].isalnum() or text[i] in "_$"):
        i += 1
    return i


def _reference_rewrite(text: str) -> str:
    """The test's own scanner, written as a loop and not as a regex, so it shares no code and no
    pattern with gen.prefix: units are renamed as bare identifiers, macros after a backtick or
    after define/ifdef/ifndef/elsif/undef, and the path of an include of a vendored header."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        j = i + 1
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2) + 2 if "*/" in text[i + 2 :] else n
        elif c == '"':
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            j += 1
        elif c == "`":
            end = _ident_end(text, j)
            name = text[j:end]
            j = end
            if name in _DIRECTIVES or name == "include":
                k = j
                while k < n and text[k] in " \t":
                    k += 1
                if name == "include" and k > j and text.startswith('"', k):
                    close = text.index('"', k + 1)
                    inc = text[k + 1 : close]
                    out.append(text[i:k] + '"' + HEADERS.get(inc, inc) + '"')
                    i = close + 1
                    continue
                if name != "include" and k > j:
                    end = _ident_end(text, k)
                    arg = text[k:end]
                    out.append(text[i:k] + (f"{PREFIX}_{arg}" if arg in MACROS else arg))
                    i = end
                    continue
            elif name in MACROS:
                out.append(f"`{PREFIX}_{name}")
                i = j
                continue
        elif c == "\\":
            while j < n and not text[j].isspace():
                j += 1
        elif c == "$":
            j = _ident_end(text, j)
        elif c == "'" and (m := _BASED.match(text, i)):
            j = m.end()
        elif c.isalpha() or c == "_":
            j = _ident_end(text, i)
            word = text[i:j]
            out.append(f"{PREFIX}_{word}" if word in UNITS else word)
            i = j
            continue
        out.append(text[i:j])
        i = j
    return "".join(out)


def _with_prefix(spec: Spec) -> Spec:
    project = spec.project.model_copy(update={"module_prefix": PREFIX})
    return spec.model_copy(update={"project": project})


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _declared(text: str) -> set[str]:
    return set(_DECL.findall(_STRIP.sub("", text)))


def _tokens(text: str) -> set[str]:
    return set(_IDENT.findall(_STRIP.sub("", text)))


UNITS = _package_units()


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


@pytest.mark.parametrize("config", [GOLDEN, "p3/mixed_apb_masters", "p3/mixed_adapters_bridges"])
def test_a_blank_prefix_copies_every_file_as_packaged(config: str, tmp_path: Path) -> None:
    configs, name = _split(config)
    generate(load(configs / f"{name}.ir.yaml"), tmp_path, configs)
    packaged = [r for r in _tree(tmp_path) if r.startswith((INHOUSE, "rtl/vendor/", "formal/"))
                and (files("hdl_bag") / r).is_file()]  # fmt: skip
    assert any(r.startswith("formal/vendor/") for r in packaged)
    assert any(r.startswith("formal/bag_") for r in packaged)
    assert any(r.endswith("/LICENSE") for r in packaged)
    for rel in packaged:
        assert (tmp_path / rel).read_bytes() == (files("hdl_bag") / rel).read_bytes(), rel


# ---- The corpus under a prefix -----------------------------------------------------------

CASES = [pytest.param(c, None, id=c) for c in ALL] + [
    pytest.param(GOLDEN, "sv-interface", id=f"{GOLDEN}-sv-interface")
]


def _expect(spec: Spec) -> Callable[[str], str]:
    subs = {f"rtl/{w.name}.sv" for w in spec.hierarchy.wrappers}

    def expect(rel: str) -> str:
        head, _, base = rel.rpartition("/")
        rtl = rel.endswith((".sv", ".v")) or (rel.startswith(PULP) and rel.endswith(".svh"))
        own = head == "formal" and base in FORMAL_OWN  # not the generated formal files
        under = rel.startswith((INHOUSE, DERIVED, PULP, ZIPCPU))
        if rel in subs or (rtl and (under or own)):
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
def test_the_prefix_reaches_every_unit_file_and_no_other(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """A. The generator-side statement of checks/wrapper._fixed_units: the top, the package,
    <top>_flat, <project>_<proto>_if, rtl/ext, LICENSE, SOURCE.md and the generated formal
    files keep their paths; every other unit file, and every vendored header, takes the prefix on
    its basename."""
    spec, blank, pre = trees(config, style)
    expect = _expect(spec)
    assert set(pre) == {expect(rel) for rel in blank}
    assert any(r.startswith(f"{INHOUSE}{PREFIX}_") for r in pre)
    if any(r.startswith(PULP) for r in blank):
        assert any(r.startswith(PULP) and "/" + PREFIX + "_" in r for r in pre)


@pytest.mark.parametrize(("config", "style"), CASES)
def test_no_generated_source_names_an_unprefixed_unit(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """B. No identifier in any generated source or header is a bare unit name (comments and
    strings keep theirs: messages are left untouched)."""
    _, blank, pre = trees(config, style)
    assert len(UNITS) >= 143 and {"axi_pkg", "AXI_BUS", "lzc", "faxil_slave"} <= UNITS
    scanned = {r: d for r, d in pre.items() if r.endswith((".sv", ".v", ".svh", ".vh"))}
    for rel, data in scanned.items():
        assert not _tokens(data.decode()) & UNITS, rel
    for tree in (PULP, "formal/vendor/"):
        if any(r.startswith(tree) for r in blank):
            assert any(r.startswith(tree) for r in scanned), tree


@pytest.mark.parametrize(("config", "style"), CASES)
def test_a_copied_file_declares_the_prefixed_units_of_its_packaged_file(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """C. Not 'one unit named as its stem': a file may hold several units (axi_intf.sv declares
    seven interfaces, bag_step.v two modules)."""
    spec, blank, pre = trees(config, style)
    expect = _expect(spec)
    checked = 0
    for rel in (r for r in blank if r.endswith((".sv", ".v")) and expect(r) != r):
        if not (files("hdl_bag") / rel).is_file():
            continue  # a sub-wrapper
        checked += 1
        packaged = (files("hdl_bag") / rel).read_text(encoding="utf-8")
        want = {f"{PREFIX}_{u}" for u in _declared(packaged)}
        assert _declared(pre[expect(rel)].decode()) == want, rel
    assert checked


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


def _resolve(files_: dict[str, str], tree: dict[str, bytes], name: str) -> str | None:
    """The written file that NAME (as a job's script names it, inside the job's [files]) is."""
    for dest, src in files_.items():
        if name == dest or name.startswith(dest + "/"):
            found = posixpath.normpath(posixpath.join("formal", src, name[len(dest) :].lstrip("/")))
            return found if found in tree else None
    return None


def _job_problems(rel: str, tree: dict[str, bytes]) -> list[str]:
    """What is wrong with the SymbiYosys job REL of TREE: a source it names that was not
    written, or a `copy` of a module that none of the files it reads declares."""
    section, files_, reads, copies, bad = "", {}, [], [], []
    for line in tree[rel].decode().split("\n"):
        if line.startswith("["):
            section = line
        elif section == "[files]" and line.strip():
            dest, _, src = line.partition(" ")
            files_[dest] = src or dest
        elif section == "[script]":
            words = re.sub(r"^\w+: ", "", line).split()
            if words[:2] == ["read_verilog", "-formal"]:
                reads.append(words[2])
            elif words[:1] == ["read_slang"]:
                reads += [w for w in words if w.endswith((".sv", ".v"))]
            elif words[:1] == ["copy"]:
                copies.append(words[1])
    for dest, src in files_.items():
        target = posixpath.normpath(posixpath.join("formal", src))
        if target not in tree and not any(t.startswith(target + "/") for t in tree):
            bad.append(f"[files] {dest} <- {src} was not written")
    declared: set[str] = set()
    for name in dict.fromkeys(reads):
        found = _resolve(files_, tree, name)
        if found is None:
            bad.append(f"{name} is read but not in [files] or not written")
        elif not name.startswith("rtl/"):
            declared |= _declared(tree[found].decode())
    bad += [f"copy {m}: no file the job reads declares it" for m in copies if m not in declared]
    return bad


@pytest.mark.parametrize(("config", "style"), CASES)
def test_every_formal_job_names_only_files_and_modules_it_has(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """E. Blank and prefixed alike: a job's [files], read_verilog, read_slang and copy lines all
    resolve in the written tree (the job is the only thing that knows the renamed file names)."""
    _, blank, pre = trees(config, style)
    jobs = [r for r in blank if r.startswith("formal/") and r.endswith(".sby")]
    for tree in (blank, pre):
        assert [r for r in tree if r.startswith("formal/") and r.endswith(".sby")] == jobs
        for rel in jobs:
            assert _job_problems(rel, tree) == [], rel
    assert jobs or not any(r.startswith("formal/") for r in blank)


def test_the_job_check_sees_a_missing_file_and_a_copy_of_an_undeclared_module(
    trees: Trees,
) -> None:
    _, blank, _ = trees("p3/mixed_apb_masters", None)
    job = "formal/mixed_apb_masters_protocol.sby"
    assert _job_problems(job, blank) == []
    gone = {r: d for r, d in blank.items() if r != "formal/bag_step.v"}
    assert _job_problems(job, gone)
    text = blank[job].decode().replace("copy fapb_slave ", "copy nope ", 1)
    assert _job_problems(job, {**blank, job: text.encode()})


@pytest.mark.parametrize(("config", "style"), CASES)
def test_renamed_upstream_files_differ_from_the_packaged_ones_by_the_note_and_the_names(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """F. (a) Each upstream file the rewrite changed is the note line plus the packaged bytes
    with unit, macro and include-file names replaced, nothing else, by a tokenizer written
    independently of gen.prefix; (b) only the LICENSE files stay byte for byte as packaged."""
    spec, blank, pre = trees(config, style)
    expect = _expect(spec)
    upstream = [r for r in blank if r.startswith((PULP, ZIPCPU)) and r.endswith(
        (".sv", ".v", ".svh"))]  # fmt: skip
    for rel in upstream:
        packaged = (files("hdl_bag") / rel).read_text(encoding="utf-8")
        renamed = _reference_rewrite(packaged)
        want = f"{NOTE.format(PREFIX)}\n{renamed}" if renamed != packaged else packaged
        assert pre[expect(rel)].decode() == want, rel
        assert renamed != packaged, rel
    same = {r for r in blank if r.startswith((PULP, "formal/vendor/"))
            and pre[expect(r)] == (files("hdl_bag") / r).read_bytes()}  # fmt: skip
    # Only the licences stay byte for byte: every header now has a renamed macro or guard.
    assert same == {r for r in blank if r.endswith("/LICENSE")
                    and r.startswith((PULP, "formal/vendor/"))}  # fmt: skip
    if any(r.startswith(PULP) for r in blank):
        assert same
    if config == GOLDEN:
        assert {Path(r).name for r in upstream if r.endswith(".svh")} == {
            "assign.svh", "typedef.svh", "assertions.svh", "registers.svh"}  # fmt: skip


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
    note = (f'// Unit, macro and include-file names carry the prefix "{PREFIX}_"'
            " (Project > Module prefix).")  # fmt: skip
    derived = [r for r in pre if r.startswith(f"{DERIVED}{PREFIX}_")]
    assert derived
    for rel in derived:
        lines = pre[rel].decode().split("\n")
        assert lines[0].startswith("// SPDX-") and lines[1] == note
    assert not any(note.encode() in d for r, d in pre.items() if r.startswith(INHOUSE))
    assert not any(note.encode() in d for d in blank.values())


def test_upstream_files_say_what_the_rewrite_changed_on_their_first_line(
    trees: Trees,
) -> None:
    _, blank, pre = trees(GOLDEN, None)
    note = NOTE.format(PREFIX)
    noted = {r for r, d in pre.items() if d.decode().split("\n")[0] == note}
    assert noted
    assert all(r.endswith((".sv", ".v", ".svh")) for r in noted)
    assert any(r.startswith(PULP) for r in noted) and any(r.startswith(ZIPCPU) for r in noted)
    headers = {r for r in pre if r.endswith(".svh")}
    assert len(headers) == 4 and headers <= noted
    for rel in pre:
        if rel.startswith((INHOUSE, "formal/bag_")):
            assert rel not in noted, rel
    assert not any(note.encode() in d or b"Modified by hdl-bag" in d for d in blank.values())
    # Derived files keep the stage-2 note and position.
    assert all(note.encode() not in d for r, d in pre.items() if r.startswith(DERIVED))


def test_the_generated_source_note_says_the_names_carry_the_prefix_only_under_one(
    trees: Trees,
) -> None:
    _, blank, pre = trees(GOLDEN, None)
    rel = f"{ZIPCPU}SOURCE.md"
    packaged = (files("hdl_bag") / rel).read_bytes()
    assert blank[rel] == packaged
    assert pre[rel].startswith(packaged) and pre[rel] != packaged
    extra = pre[rel][len(packaged) :].decode()
    assert PREFIX in extra and "first line" in extra and extra.endswith("\n")
    # Its own paragraph, or Markdown folds it into the preceding one.
    assert extra.startswith("\n")


def _regenerate_blank(tmp_path: Path, configs: Path, name: str) -> Path:
    out = tmp_path.with_name(tmp_path.name + "_blank")
    generate(load(configs / f"{name}.ir.yaml"), out, configs)
    return out


def test_changing_the_prefix_leaves_no_file_of_the_old_name(tmp_path: Path) -> None:
    configs, name = _split("p2/ahb2apb_nested")
    spec = load(configs / f"{name}.ir.yaml")
    generate(spec, tmp_path, configs)
    generate(_with_prefix(spec), tmp_path, configs)
    after = _tree(tmp_path)
    old = {f"{b}.sv" for b in BAG}
    assert not [r for r in after if r.startswith((INHOUSE, DERIVED)) and Path(r).name in old]
    blank_tree = _tree(_regenerate_blank(tmp_path, configs, name))
    stale = [r for r in blank_tree if r.startswith((PULP, "formal/")) and r.endswith((".sv", ".v"))
             and (files("hdl_bag") / r).is_file() and r in after]  # fmt: skip
    assert stale == [], "files of the unprefixed names remain after the prefix was set"
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
@pytest.mark.parametrize("config", ["p2/ahb_fixed", "p3/mixed_apb_masters"])
def test_a_prefixed_design_passes_formal(config: str) -> None:
    """mixed_apb_masters adds Yosys's own Verilog frontend, `copy` of the renamed ZipCPU modules
    and a route job, none of which ahb_fixed reaches."""
    configs, name = _split(config)
    root = REPO / "out" / "prefix" / f"{name}_formal"
    out = generate(_with_prefix(load(configs / f"{name}.ir.yaml")), root, configs)
    report = run_formal(out)
    assert report.ok, report.format()


# ---- A prefixed sub-wrapper name never equals a unit's emitted name ------------------------

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


def test_a_sub_wrapper_cut_under_prefix_axi_sits_beside_pulps_prefixed_axi_cut(
    tmp_path: Path,
) -> None:
    """The sub-wrapper is axi_cut and PULP's axi_cut is axi_axi_cut: both generate, once each."""
    generate(_renamed_sub_wrapper("cut", "axi"), tmp_path, NESTED.parent)
    names = _declarations(tmp_path)
    assert names.count("axi_cut") == 1 and names.count("axi_axi_cut") == 1
    assert len(names) == len(set(names))


def test_a_sub_wrapper_named_like_a_pulp_unit_is_refused_under_a_prefix(
    tmp_path: Path,
) -> None:
    """Sub-wrapper axi_cut under prefix p is p_axi_cut, which is also PULP's axi_cut."""
    with pytest.raises(GenerateError, match="p_axi_cut") as exc:
        generate(_renamed_sub_wrapper("axi_cut", "p"), tmp_path, NESTED.parent)
    assert "HIER-RESERVED-NAME" in str(exc.value)


def test_a_prefix_that_makes_units_collide_with_their_own_bases_renames_each_exactly_once(
    tmp_path: Path,
) -> None:
    """Prefix bag: bag_axi_cut is the in-house/derived name, and PULP's axi_cut becomes it.
    The rewrite is single-pass, so every unit is bag_<name> and none is declared twice."""
    configs, name = _split("p3/axi_adapters")
    spec = load(configs / f"{name}.ir.yaml")
    blank = tmp_path / "blank"
    generate(spec, blank, configs)
    project = spec.project.model_copy(update={"module_prefix": "bag"})
    pre = tmp_path / "pre"
    generate(spec.model_copy(update={"project": project}), pre, configs)
    old, names = _declarations(blank), _declarations(pre)
    assert len(names) == len(set(names)) and len(names) == len(old)
    assert {f"bag_{u}" for u in old if u in UNITS} <= set(names)
    # In-house bag_axi_cut is bag_bag_axi_cut; PULP's axi_cut is bag_axi_cut, which it instantiates.
    text = (pre / "rtl/inhouse/bag_bag_axi_cut.sv").read_text(encoding="utf-8")
    assert re.search(r"^\s*bag_axi_cut\b", text, re.M)


# ---- The generated formal units are reserved (issue #152) ----------------------------------

DEMO = REPO / "tests/golden/demo_ss.ir.yaml"


def _with_project(spec: Spec, **update: str | None) -> Spec:
    return spec.model_copy(update={"project": spec.project.model_copy(update=update)})


@pytest.mark.parametrize("prefix", [None, "bag"])
def test_a_project_named_addr_is_refused_because_its_formal_decode_harness_is_taken(
    tmp_path: Path, prefix: str | None
) -> None:
    """Blank prefix: bag_addr_decode is the in-house unit. Prefix bag: it is PULP's addr_decode."""
    spec = _with_project(load(DEMO), name="addr", module_prefix=prefix)
    with pytest.raises(GenerateError, match="bag_addr_decode") as exc:
        generate(spec, tmp_path, DEMO.parent)
    assert "NAME-RESERVED" in str(exc.value)


def _with_top(spec: Spec, top: str) -> Spec:
    """SPEC with the top wrapper renamed, and every reference to it retargeted, so that only
    the new name is in question."""
    old = spec.hierarchy.top
    h = spec.hierarchy.model_copy(update={
        "top": top,
        "wrappers": tuple(w.model_copy(update={"parent": top}) if w.parent == old else w
                          for w in spec.hierarchy.wrappers),
        "placement": tuple(x.model_copy(update={"wrapper": top}) if x.wrapper == old else x
                           for x in spec.hierarchy.placement),
    })  # fmt: skip
    return spec.model_copy(update={"hierarchy": h})


@pytest.mark.parametrize(
    ("top", "expected"),
    [
        ("bag_demo_ss_decode",
         "the top module is named bag_demo_ss_decode, which is also the formal decode harness; "
         "choose another Top module name"),
        ("bag_demo_ss_unit",
         "the flat harness of top module bag_demo_ss_unit is named bag_demo_ss_unit_flat, which "
         "is also a formal unit harness; choose another Top module name"),
    ],
    ids=["top", "flat"],
)  # fmt: skip
def test_a_top_module_name_that_takes_a_formal_unit_name_is_refused(
    top: str, expected: str
) -> None:
    """The second arm of NAME-RESERVED: a name the user chose, not one BAG derived. The flat
    harness is the case where the name BAG writes is not the one the user typed."""
    netlist = build_netlist(_with_top(load(DEMO), top))
    reserved = [p for p in problems(netlist, plan(netlist)) if p.rule == "NAME-RESERVED"]
    assert [(p.setting, p.what) for p in reserved] == [("Top module name", expected)]


@pytest.mark.parametrize("prefix", [None, "bag"])
def test_no_two_written_files_of_demo_ss_declare_the_same_unit(
    tmp_path: Path, prefix: str | None
) -> None:
    generate(_with_project(load(DEMO), module_prefix=prefix), tmp_path, DEMO.parent)
    names = _declarations(tmp_path)
    assert names and len(names) == len(set(names))


# ---- Macros and include files (stage 4) -----------------------------------------------------

ENV = {"ASSERTS_OFF", "ASSERTS_OVERRIDE_ON", "COMMON_CELLS_ASSERTS_OFF", "FPV_ON", "QUESTA",
       "SYNTHESIS", "TARGET_GENUS", "UVM", "VCS", "VERILATOR", "XSIM"}  # fmt: skip
BUILTIN_MACROS = {"__FILE__", "__LINE__", "uvm_error", "include", "define", "ifdef", "ifndef",
                  "else", "elsif", "endif", "undef", "timescale", "default_nettype", "resetall",
                  "begin_keywords", "end_keywords", "celldefine", "endcelldefine", "line",
                  "pragma", "unconnected_drive", "nounconnected_drive"}  # fmt: skip
_MACRO_USE = re.compile(r"(?<!`)`(?:(?:define|ifdef|ifndef|elsif|undef)[ \t]+)?(\w+)")


def _rtl_texts(tree: dict[str, bytes]) -> dict[str, str]:
    return {r: d.decode() for r, d in tree.items() if r.endswith(_RTL_SUFFIX)}


@pytest.mark.parametrize(("config", "style"), CASES)
def test_no_generated_source_defines_tests_or_uses_an_unprefixed_macro(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """G. Every macro name in a prefixed tree is prefixed, except the environment's (tested,
    never defined in the tree), which occur exactly as often as in the blank tree."""
    assert len(MACROS) == 193 and not MACROS & ENV
    _, blank, pre = trees(config, style)
    names: set[str] = set()
    for text in _rtl_texts(pre).values():
        names |= set(_MACRO_USE.findall(_STRIP.sub("", text)))
    # BAG_COVER_<port>, BAG_IDLE_<port>, BAG_READS_<port> and the like are made per design by
    # formal_gen and defined by that design's own .sby; no packaged file defines them, so they are
    # not among the 193 (asserted above) and they carry the project's own port names.
    per_design = {n for n in names if n.startswith("BAG_")}
    unprefixed = {n for n in names if not n.startswith(f"{PREFIX}_")}
    unprefixed -= ENV | BUILTIN_MACROS | per_design
    assert not unprefixed, sorted(unprefixed)
    assert not names & MACROS
    count = {n: 0 for n in ENV}
    for tree in (blank, pre):
        found = {n: sum(len(re.findall(rf"\b{n}\b", t)) for t in _rtl_texts(tree).values())
                 for n in ENV}  # fmt: skip
        if tree is blank:
            count = found
        else:
            assert found == count
    assert any(count.values()) or not any(r.startswith(PULP) for r in blank)


@pytest.mark.parametrize(("config", "style"), CASES)
def test_every_include_of_a_prefixed_tree_names_a_header_only_that_tree_has(
    trees: Trees,
    config: str,
    style: str | None,
) -> None:
    """H. The coupling this stage removes: two projects on one include path no longer pick up
    each other's headers, because no header path of a prefixed tree exists in a blank one."""
    _, blank, pre = trees(config, style)
    name = _spec_name(pre)
    incdirs = [t.removeprefix("+incdir+") for t in pre[f"{name}.f"].decode().split()
               if t.startswith("+incdir+")]  # fmt: skip
    seen = 0
    for rel, text in _rtl_texts(pre).items():
        for inc in re.findall(r'`include[ \t]+"([^"\n]+)"', _STRIP_COMMENTS.sub("", text)):
            if inc == "uvm_macros.svh":
                continue
            seen += 1
            hits = [d for d in incdirs if posixpath.join(d, inc) in pre]
            assert len(hits) == 1, (rel, inc, hits)
            assert posixpath.join(hits[0], inc) not in blank
    pre_headers = {r for r in pre if r.endswith(".svh")}
    assert not pre_headers & set(blank)
    if any(r.endswith(".svh") for r in blank):
        assert pre_headers and seen


def _spec_name(tree: dict[str, bytes]) -> str:
    (name,) = {r.removesuffix(".f") for r in tree if r.endswith(".f") and "/" not in r}
    return name
