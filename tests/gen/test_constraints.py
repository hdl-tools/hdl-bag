# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The CDC constraint files (constraints/<project>_cdc.sdc and .xdc).

No tool reads them here. What can be proved without one: they name exactly the crossings the IR
has, every instance, pin and register they name exists in the generated RTL, the periods are
right, and the text is deterministic.
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from hdl_bag.gen import MANIFEST, generate
from hdl_bag.ir import Spec, load
from hdl_bag.templates import builtin_registry

TESTS = Path(__file__).parents[1]
CONFIGS = {
    "p2/ahb2apb_cdc": TESTS / "configs/p2/ahb2apb_cdc.ir.yaml",
    "p2/nested_deep": TESTS / "configs/p2/nested_deep.ir.yaml",
    "p3/bridges_inhouse_cdc": TESTS / "configs/p3/bridges_inhouse_cdc.ir.yaml",
    "p3/bridges_pulp_cdc": TESTS / "configs/p3/bridges_pulp_cdc.ir.yaml",
    "p3/axil_adapters": TESTS / "configs/p3/axil_adapters.ir.yaml",
    "p3/nested_adapters": TESTS / "configs/p3/nested_adapters.ir.yaml",
    "golden/demo_ss": TESTS / "golden/demo_ss.ir.yaml",
}
DEMO = CONFIGS["golden/demo_ss"]
FORMATS = ("sdc", "xdc")
RULE = "# // " + "-" * 76  # the //-comment banner, commented again for SDC
SELECTOR = re.compile(r"\[get_(pins|cells) (?:\{([^}]*)\}|(\S+))\]")


def _generate(src: Path, out: Path, style: str | None = None, spec: Spec | None = None) -> Path:
    generate(spec or load(src), out, src.parent, style)
    return out


def _rel(spec: Spec, fmt: str) -> str:
    return f"constraints/{spec.project.name}_cdc.{fmt}"


def _body(text: str) -> str:
    """TEXT below the banner (which carries the IR hash, and so differs with a module prefix)."""
    lines = text.splitlines(keepends=True)
    rules = [i for i, line in enumerate(lines) if line.rstrip("\n") == RULE]
    return "".join(lines[rules[1] + 1 :])


def _expected_paths(spec: Spec) -> dict[str, str]:
    """Crossing name -> instance path relative to the top, from the IR alone: the placement
    table and the wrapper tree, with no help from the generator."""
    domain = {f.name: f.domain for f in spec.fabrics}
    names = {a.name for a in spec.adapters if a.type == "cdc"}
    names |= {b.name for b in spec.bridges if domain[b.upstream] != domain[b.downstream]}
    parent = {w.name: w.parent for w in spec.hierarchy.wrappers}
    place = {p.instance: p for p in spec.hierarchy.placement}
    out = {}
    for name in names:
        wrappers, w = [], place[name].wrapper
        while w != spec.hierarchy.top:
            wrappers.append(f"u_{w}")
            w = parent[w]
        out[name] = "/".join([*reversed(wrappers), place[name].rtl_name])
    return out


def _selectors(text: str) -> list[tuple[str, str]]:
    """(get_pins | get_cells, path) of every selector in TEXT."""
    out = []
    for m in SELECTOR.finditer(text):
        out += [(m[1], tok) for tok in (m[2] or m[3]).split()]
    return out


# ---------------------------------------------------------------- the generated RTL, walked


class Rtl:
    """The generated modules, by name, and a walker that follows an instance path through them
    (generate labels included) the way a tool's hierarchy browser would."""

    def __init__(self, root: Path) -> None:
        self.modules: dict[str, str] = {}
        for f in sorted((root / "rtl").rglob("*.sv")):
            text = re.sub(r"//[^\n]*", "", f.read_text(encoding="utf-8"))
            for m in re.finditer(r"^module\s+(\w+)", text, re.M):
                end = text.find("endmodule", m.end())
                self.modules[m[1]] = text[m.start() : end]

    @staticmethod
    def _block(text: str, label: str) -> str:
        """The text of the generate block LABEL (begin : LABEL ... its matching end)."""
        m = re.search(rf"\bbegin\s*:\s*{label}\b", text)
        assert m, f"no generate block {label}"
        depth = 0
        for t in re.finditer(r"\b(begin|end)\b", text[m.start() :]):
            depth += 1 if t[1] == "begin" else -1
            if depth == 0:
                return text[m.start() : m.start() + t.end()]
        raise AssertionError(f"generate block {label} is not closed")

    def _instance(self, scope: str, name: str) -> str:
        """The module that NAME instantiates in SCOPE (text of a module or a block)."""
        for m in re.finditer(rf"\b{re.escape(name)}\s*\(", scope):
            head = scope[: m.start()].rstrip()
            if head.endswith(")"):  # a parameter list: walk back to its opening parenthesis
                depth = 0
                for i in range(len(head) - 1, -1, -1):
                    depth += (head[i] == ")") - (head[i] == "(")
                    if depth == 0:
                        break
                head = head[:i].rstrip()
                assert head.endswith("#"), f"{name}: not an instantiation"
                head = head[:-1].rstrip()
            mod = re.search(r"(\w+)$", head)
            if mod and mod[1] in self.modules:
                return mod[1]
        raise AssertionError(f"no instance {name}")

    def module_of(self, top: str, segments: list[str]) -> str:
        mod = top
        for seg in segments:
            scope = self.modules[mod]
            *labels, inst = seg.split(".")
            for label in labels:
                scope = self._block(scope, label)
            mod = self._instance(scope, inst)
        return mod

    def check(self, top: str, kind: str, path: str) -> None:
        *inst, leaf = path.split("/")
        mod = self.modules[self.module_of(top, inst)]
        if kind == "pins":
            header = mod[: mod.index(");")]
            names = set(re.findall(r"\b[A-Za-z_]\w*\b", header))
            assert fnmatch.filter(sorted(names), leaf), f"{path}: no such port"
        else:
            assert leaf.endswith("_reg*"), f"{path}: a register pattern ends in _reg*"
            reg = leaf.removesuffix("_reg*")
            assert re.search(rf"\b{reg}\s*(\[[^\]]*\])?\s*<=", mod), f"{path}: not a register"


# ---------------------------------------------------------------- what the files name


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("config", CONFIGS)
def test_the_files_name_exactly_the_crossings_of_the_ir(
    tmp_path: Path, config: str, fmt: str
) -> None:
    """Every crossing has its constraints and no other instance is named: the set of instances
    the selectors sit under is the set the IR computes."""
    spec = load(CONFIGS[config])
    expected = _expected_paths(spec)
    assert expected, "the corpus config has a crossing"
    text = (_generate(CONFIGS[config], tmp_path) / _rel(spec, fmt)).read_text(encoding="utf-8")
    owners = []
    for _, path in _selectors(text):
        found = [p for p in expected.values() if path.startswith(p + "/")]
        assert len(found) == 1, f"{path} is under {len(found)} crossing instances"
        owners.append(found[0])
    assert sorted(set(owners)) == sorted(expected.values())
    for name in expected:
        assert f"# crossing {name}:" in text


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("config", CONFIGS)
def test_every_named_instance_pin_and_register_exists_in_the_generated_rtl(
    tmp_path: Path, config: str, fmt: str
) -> None:
    spec = load(CONFIGS[config])
    root = _generate(CONFIGS[config], tmp_path)
    rtl = Rtl(root)
    selectors = _selectors((root / _rel(spec, fmt)).read_text(encoding="utf-8"))
    assert selectors
    for kind, path in selectors:
        rtl.check(spec.hierarchy.top, kind, path)


def test_the_walker_rejects_a_path_that_does_not_exist(tmp_path: Path) -> None:
    """The check above would pass anything if the walker accepted anything."""
    spec = load(DEMO)
    rtl = Rtl(_generate(DEMO, tmp_path))
    top = spec.hierarchy.top
    good = next(p for _, p in _selectors((tmp_path / _rel(spec, "sdc")).read_text()))
    rtl.check(top, "pins", good)
    for bad_kind, bad in (
        ("pins", good.replace("gen_cdc", "gen_nocdc")),
        ("pins", good.rsplit("/", 1)[0] + "/no_such_pin"),
        ("pins", "u_nowhere/" + good),
        ("cells", good.rsplit("/", 1)[0] + "/no_such_reg_reg*"),
    ):
        with pytest.raises(AssertionError):
            rtl.check(top, bad_kind, bad)


# ---------------------------------------------------------------- the numbers


def _blocks(text: str) -> dict[str, str]:
    parts = re.split(r"^# crossing (\S+):", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def test_demo_ss_periods_and_max_delays(tmp_path: Path) -> None:
    """demo_ss runs at 50, 1000, 100 and 500 MHz, so min() has unequal periods to choose
    between: each crossing is bounded by the faster of its two clocks."""
    spec = load(DEMO)
    root = _generate(DEMO, tmp_path)
    mhz = {c.name: c.mhz for c in spec.clocks}
    assert mhz == {"clk_aon": 50, "clk_cpu": 1000, "clk_per": 100, "clk_sys": 500}
    for fmt in FORMATS:
        text = (root / _rel(spec, fmt)).read_text(encoding="utf-8")
        clocks = re.findall(
            r"^create_clock -name (\w+) -period (\S+) \[get_ports (\w+)\]$", text, re.M
        )
        assert clocks == [
            ("clk_aon", "20.000", "clk_aon"),
            ("clk_cpu", "1.000", "clk_cpu"),
            ("clk_per", "10.000", "clk_per"),
            ("clk_sys", "2.000", "clk_sys"),
        ]
        delay = r"set_max_delay (?:-datapath_only )?(\S+) "
        per_crossing = {n: set(re.findall(rf"^{delay}", b, re.M)) for n, b in _blocks(text).items()}
        assert all(len(v) == 1 for v in per_crossing.values()), per_crossing
        assert sorted(next(iter(v)) for v in per_crossing.values()) == ["1.000", "2.000", "2.000"]


def test_the_sdc_and_xdc_differ_in_exactly_the_spelling(tmp_path: Path) -> None:
    """-datapath_only is Vivado's, and already drops the hold check that the SDC removes with
    set_false_path -hold."""
    spec = load(DEMO)
    root = _generate(DEMO, tmp_path)
    sdc = (root / _rel(spec, "sdc")).read_text(encoding="utf-8")
    xdc = (root / _rel(spec, "xdc")).read_text(encoding="utf-8")
    assert "-datapath_only" not in sdc and "set_false_path -hold" in sdc
    assert "-datapath_only" in xdc and "set_false_path" not in xdc
    n_max = len(re.findall(r"^set_max_delay ", sdc, re.M))
    assert n_max == len(re.findall(r"^set_max_delay -datapath_only ", xdc, re.M)) > 0
    assert n_max == len(re.findall(r"^set_false_path -hold ", sdc, re.M))
    cmd = re.compile(r"^(?:set_max_delay (?:-datapath_only )?|set_false_path -hold )")
    assert [cmd.sub("", s) for s in sdc.splitlines() if cmd.match(s) and "false" not in s] == [
        cmd.sub("", x) for x in xdc.splitlines() if cmd.match(x)
    ]
    for text in (sdc, xdc):  # one command per line
        assert "\\\n" not in text


# ---------------------------------------------------------------- what they do not depend on


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_body_is_the_same_in_both_styles_and_with_a_module_prefix(
    tmp_path: Path, fmt: str
) -> None:
    spec = load(DEMO)
    prefixed = spec.model_copy(
        update={"project": spec.project.model_copy(update={"module_prefix": "zz9"})}
    )
    texts = [
        (_generate(DEMO, tmp_path / "flat", "flat") / _rel(spec, fmt)).read_text(),
        (_generate(DEMO, tmp_path / "svi", "sv-interface") / _rel(spec, fmt)).read_text(),
        (_generate(DEMO, tmp_path / "pfx", spec=prefixed) / _rel(spec, fmt)).read_text(),
    ]
    assert texts[0] != texts[2], "the banner carries the IR hash"
    assert _body(texts[0]) == _body(texts[1]) == _body(texts[2])
    # And the paths are still real: a prefix renames modules, never instances.
    rtl = Rtl(tmp_path / "pfx")
    for kind, path in _selectors(texts[2]):
        rtl.check(spec.hierarchy.top, kind, path)


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_design_with_no_crossing_still_gets_its_clocks_and_a_note(
    tmp_path: Path, fmt: str
) -> None:
    src = TESTS / "configs/p2/ahb2apb_pt.ir.yaml"
    spec = load(src)
    text = (_generate(src, tmp_path) / _rel(spec, fmt)).read_text(encoding="utf-8")
    assert len(re.findall(r"^create_clock ", text, re.M)) == len(spec.clocks) > 0
    assert "# No clock-domain crossings: nothing to constrain.\n" in text
    assert not re.search(r"^set_(max_delay|false_path)", text, re.M)


def test_no_rtl_writes_neither_file(tmp_path: Path) -> None:
    """The files name RTL instances, so Generate RTL = N has nothing to name."""
    spec = load(DEMO)
    off = spec.project.outputs.model_copy(update={"rtl": False})
    spec = spec.model_copy(update={"project": spec.project.model_copy(update={"outputs": off})})
    root = _generate(DEMO, tmp_path, spec=spec)
    assert not (root / "constraints").exists()
    assert not any(
        k.startswith("constraints/") for k in json.loads((root / MANIFEST).read_text())["files"]
    )


def test_both_files_are_listed_in_the_manifest_with_their_hashes(tmp_path: Path) -> None:
    import hashlib

    spec = load(DEMO)
    root = _generate(DEMO, tmp_path)
    listed = json.loads((root / MANIFEST).read_text())["files"]
    for fmt in FORMATS:
        rel = _rel(spec, fmt)
        assert listed[rel] == hashlib.sha256((root / rel).read_bytes()).hexdigest()


def test_output_is_byte_identical_across_hash_seeds(tmp_path: Path) -> None:
    trees = []
    for seed in ("0", "12345"):
        out = tmp_path / seed
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run([sys.executable, "-m", "hdl_bag.cli", "generate", str(DEMO), "-o", str(out)],
                       check=True, env=env, capture_output=True)  # fmt: skip
        trees.append({f.name: f.read_bytes() for f in sorted((out / "constraints").iterdir())})
    assert sorted(trees[0]) == ["demo_ss_cdc.sdc", "demo_ss_cdc.xdc"]
    assert trees[0] == trees[1]
    assert all(b"\r" not in v for v in trees[0].values())


# ---------------------------------------------------------------- the tables and the RTL


def _builtin_manifests() -> dict[str, list[str]]:
    reg = builtin_registry()
    return {n: list(m.sources) for n in reg.names() if (m := reg.get(n)) is not None}


def test_every_template_with_a_crossing_is_in_exactly_one_table() -> None:
    """A template that carries a synchronizer or axi_cdc but has no recipe would be a crossing
    with no constraint, silently."""
    from hdl_bag.gen.constraints import FIFO_SCOPE, HANDSHAKE

    crossing = {
        name for name, srcs in _builtin_manifests().items()
        if "rtl/inhouse/bag_sync.sv" in srcs or "bender:axi:axi_cdc" in srcs
    }  # fmt: skip
    assert crossing
    assert set(FIFO_SCOPE) | set(HANDSHAKE) == crossing
    assert not set(FIFO_SCOPE) & set(HANDSHAKE)
    assert set(HANDSHAKE) == {"bag_ahb2apb", "bag_ahb2axi", "bag_apb2ahb", "bag_apb2axi"}


def test_all_eight_templates_are_covered_by_the_corpus(tmp_path: Path) -> None:
    from hdl_bag.gen.constraints import FIFO_SCOPE, HANDSHAKE

    seen = set()
    for src in CONFIGS.values():
        spec = load(src)
        names = set(_expected_paths(spec))
        seen |= {a.template for a in spec.adapters if a.name in names}
        seen |= {b.template for b in spec.bridges if b.name in names}
    assert seen == set(FIFO_SCOPE) | set(HANDSHAKE)


@pytest.mark.parametrize("module", ["bag_sync", "bag_rst_sync"])
def test_the_synchronizer_flops_carry_async_reg_and_dont_touch(module: str) -> None:
    lines = (files("hdl_bag") / "rtl" / "inhouse" / f"{module}.sv").read_text().splitlines()
    (i,) = [n for n, line in enumerate(lines) if re.match(r"\s*logic \[Stages-1:0\] q;", line)]
    assert lines[i - 1].strip() == '(* ASYNC_REG = "TRUE", DONT_TOUCH = "TRUE" *)'
