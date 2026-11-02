# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The phase gate corpora: tests/configs/p2 (APB/AHB-only), tests/configs/p3 (AXI and
mixed protocols) and the golden demo_ss spec.

Gate: every config lints (slang, Verilator, Verible) and compiles in both port styles;
the Phase 2 corpus has 10+ configs, one with nested sub-wrappers. Beyond the gates, every
config also passes the structural connectivity oracle and an IR-driven route simulation
through its generated top.
"""

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest
from hier_oracle import check
from simrun import REPO, run_cocotb

from hdl_bag.checks import run_checks
from hdl_bag.flows import compile_design, lint
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.ir import dumps, load, resolve
from hdl_bag.ir.to_workbook import to_workbook
from hdl_bag.reader import read_workbook
from hdl_bag.template.builder import build_workbook
from hdl_bag.templates import builtin_registry

CONFIG_ROOT = REPO / "tests" / "configs"
PHASES = ("p2", "p3")
CONFIGS = CONFIG_ROOT / "p2"  # the Phase 2 gate corpus
NAMES = sorted(p.name.removesuffix(".ir.yaml") for p in CONFIGS.glob("*.ir.yaml"))
# Every config of every phase, as "<phase>/<name>".
ALL = [*sorted(f"{ph}/{p.name.removesuffix('.ir.yaml')}" for ph in PHASES
              for p in (CONFIG_ROOT / ph).glob("*.ir.yaml")), "golden/demo_ss"]  # fmt: skip
HERE = Path(__file__).parent


def _corpus(phase: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"corpus_{phase}",
                                                  CONFIG_ROOT / phase / "corpus.py")  # fmt: skip
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _split(config: str) -> tuple[Path, str]:
    phase, name = config.split("/")
    return (REPO / "tests" / "golden" if phase == "golden" else CONFIG_ROOT / phase), name


def _opaque(config: str) -> bool:
    """A config with a user-supplied fabric (bag_external): BAG generates only a stub of
    it, so the route model cannot be simulated through it (test_external.py covers the
    wiring with test-supplied fabrics)."""
    configs, name = _split(config)
    spec = load(configs / f"{name}.ir.yaml")
    reg = builtin_registry()
    return any((m := reg.get(f.template)) is not None and m.opaque for f in spec.fabrics)


ROUTED = [c for c in ALL if not _opaque(c)]


def test_only_external_configs_skip_the_route_simulation() -> None:
    assert sorted(set(ALL) - set(ROUTED)) == ["p3/external_fabric"]


def _ir_text(text: str) -> str:
    return text.split("provenance:")[0]


def test_corpus_has_ten_configs_with_nesting() -> None:
    assert len(NAMES) >= 10
    nested = [n for n in NAMES if load(CONFIGS / f"{n}.ir.yaml").hierarchy.wrappers]
    assert nested, "the gate needs a config with nested sub-wrappers"


@pytest.mark.parametrize("config", [f"{ph}/{n}" for ph in PHASES for n in _corpus(ph).BUILT])
def test_ir_matches_its_workbook_source(tmp_path: Path, config: str) -> None:
    """The committed IR is what `bag check` + resolve make of the corpus.py workbook."""
    configs, name = _split(config)
    path = tmp_path / f"{name}.xlsx"
    build_workbook(data=_corpus(configs.name).BUILT[name]().data).save(path)
    shutil.copy(configs / "header.txt", tmp_path / "header.txt")
    result = run_checks(read_workbook(path), tmp_path)
    assert [f.message for f in result.findings] == []
    committed = (configs / f"{name}.ir.yaml").read_text()
    assert _ir_text(dumps(resolve(result))) == _ir_text(committed), (
        f"stale: run `uv run python tests/configs/{configs.name}/corpus.py`"
    )


@pytest.mark.parametrize("config", ALL)
def test_round_trip_and_oracle(tmp_path: Path, config: str) -> None:
    configs, name = _split(config)
    spec = load(configs / f"{name}.ir.yaml")
    # IR -> workbook -> check -> IR is the identity (apart from provenance).
    wb = tmp_path / "rt.xlsx"
    to_workbook(spec).save(wb)
    shutil.copy(configs / "header.txt", tmp_path / "header.txt")
    result = run_checks(read_workbook(wb), tmp_path)
    assert [f.message for f in result.findings] == []
    assert _ir_text(dumps(resolve(result))) == _ir_text(dumps(spec))
    # The generated hierarchy matches the IR, port by port and net by net.
    generate(spec, tmp_path / "out", configs, "flat")
    assert check(spec, tmp_path / "out" / "rtl") == []


@pytest.mark.eda
@pytest.mark.parametrize("style", ["flat", "sv-interface"])
@pytest.mark.parametrize("config", ALL)
def test_gate_lint_and_compile(config: str, style: str) -> None:
    configs, name = _split(config)
    root = REPO / "out" / "corpus" / f"{name}_{style}"
    out = generate(load(configs / f"{name}.ir.yaml"), root, configs, style)
    files, inc = sources(out), include_dirs(out)
    for report in (
        lint(files, out.top, root, incdirs=inc),
        # Elaboration is the gate; test_route_simulation builds and runs the C++ model.
        compile_design(files, out.top, root, incdirs=inc, build=False),
    ):
        assert report.ok, report.format()


@pytest.mark.eda
@pytest.mark.parametrize("config", ROUTED)
def test_route_simulation(config: str) -> None:
    configs, name = _split(config)
    src = configs / f"{name}.ir.yaml"
    spec = load(src)
    root = REPO / "out" / "corpus" / f"{name}_route"
    out = generate(spec, root, configs)  # the config's own port style
    run_cocotb([root / f for f in sources(out)], out.top, HERE / "cocotb_route.py",
               f"route_{name}", env={"BAG_IR": str(src)},
               incdirs=[root / d for d in include_dirs(out)])  # fmt: skip


@pytest.mark.parametrize("config", ALL)
def test_probes_exercise_every_protection_rule(config: str) -> None:
    """Each Access_Rule (of a master port) and each RO / WO / secure-only region is hit by a
    route-simulation probe that the reference model denies, so no configured protection
    passes silently because no probe reaches it."""
    from route_model import probes, route

    configs, name = _split(config)
    spec = load(configs / f"{name}.ir.yaml")
    masters = {m.name: m for m in spec.masters}
    addrs = probes(spec)

    def denied(master: str, region: object, write: bool, nonsec: bool = False) -> bool:
        r = region  # a MemoryRegion
        width = masters[master].addr_width
        return any(r.base <= a < r.base + r.size and a < 1 << width  # type: ignore[attr-defined]
                   and route(spec, master, a, write, nonsec, {}).slave != r.slave  # type: ignore[attr-defined]
                   for a in addrs)  # fmt: skip

    missing = []
    for f in spec.fabrics:
        regions = [r for r in spec.memory_map if r.fabric == f.name]
        for rule in f.access_rules:
            if rule.master not in masters:
                continue  # a bridge's rule: reached through its upstream masters
            hit = False
            for r in (r for r in regions if r.slave == rule.slave):
                for write in (True, False):  # the direction the region allows, the rule bars
                    region_ok = r.access == "RW" or (r.access == "RO") != write
                    rule_bars = rule.access == "none" or (rule.access == "RO") == write
                    if region_ok and rule_bars:
                        hit |= denied(rule.master, r, write)
            if not hit:
                missing.append(f"Access_Rules {rule.master} -> {rule.slave} {rule.access}")
        for r in regions:  # reached by any master, possibly across bridges
            if r.access in ("RO", "WO") and not any(
                    denied(m, r, r.access == "RO") for m in masters):  # fmt: skip
                missing.append(f"{r.access} region {r.slave}.{r.region}")
            if r.secure and not any(denied(m, r, False, nonsec=True) for m in masters):
                missing.append(f"secure region {r.slave}.{r.region}")
    assert missing == []


def test_phase_3_gate_coverage() -> None:
    """The Phase 3 gate: 15+ hand-written configs that together cover its feature list. Every
    item is computed from the IR, so a config edit cannot silently drop one."""
    specs = [load(CONFIG_ROOT / "p3" / f"{n}.ir.yaml") for n in _corpus("p3").BUILT]
    mixed = [s.project.name for s in specs
             if len({*(f.protocol for f in s.fabrics), *(m.protocol for m in s.masters),
                     *(x.protocol for x in s.slaves)}) > 1]  # fmt: skip
    assert len(mixed) >= 15, mixed
    have: set[str] = set()
    for s in specs:
        ports = {m.name: m for m in s.masters} | {x.name: x for x in s.slaves}
        have |= {b.template for b in s.bridges} | {f.template for f in s.fabrics}
        have |= {f"{f.template}:{f.arbitration}" for f in s.fabrics}
        for a in s.adapters:
            if a.port not in ports:
                continue
            side = "master" if a.port in {m.name for m in s.masters} else "slave"
            have.add(f"{a.type}:{side}")
            if a.type == "dw":  # the data path's direction, master to slave
                have.add("dw:up" if int(a.params["to"]) > int(a.params["from"]) else "dw:down")
            if a.type == "pc":
                have.add(f"pc:{a.params['from']}->{a.params['to']}")
        have |= {f"slice:{o.slice}" for o in s.port_options}
        axi = {f.name for f in s.fabrics if f.protocol.startswith("AXI")}
        if any(d.aliases for f in s.fabrics if f.name in axi for d in f.decode):
            have.add("alias on AXI")
        if any(r.secure and r.fabric in axi for r in s.memory_map):
            have.add("secure-only on AXI")
        parents = {w.name: w.parent for w in s.hierarchy.wrappers}
        placed = [p.wrapper for p in s.hierarchy.placement if p.kind == "adapter"]
        if any(parents.get(parents.get(w, ""), "") for w in placed):  # two levels down
            have.add("adapters in nested sub-wrappers")
    registry = builtin_registry()
    required = set(registry.names("bridge")) | set(registry.names("fabric"))  # every template
    required |= {
        "bag_axi_shared_bus:fixed-priority", "bag_axi_shared_bus:round-robin",
        "bag_axil_shared_bus:fixed-priority", "bag_axil_shared_bus:round-robin",
        "cdc:master", "cdc:slave", "dw:up", "dw:down", "iw:slave", "fifo:master", "fifo:slave",
        "cut:master", "cut:slave", "pc:AXI4->AXI4-Lite", "pc:AXI4-Lite->AXI4",
        "slice:multicut-2", "alias on AXI", "secure-only on AXI",
        "adapters in nested sub-wrappers",
    }  # fmt: skip
    assert required - have == set()
