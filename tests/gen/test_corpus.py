# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The phase gate corpora: tests/configs/p2 (APB/AHB-only), tests/configs/p3 (AXI and
mixed protocols) and the golden demo_ss spec.

Gate: every config lints (slang, Verilator, Verible) and compiles in both port styles;
the Phase 2 corpus has 10+ configs, one with nested sub-wrappers. Beyond the gates, every
config also passes the structural connectivity oracle and an IR-driven route simulation
through its generated top.
"""

import hashlib
import importlib.util
import json
import shutil
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from hier_oracle import check
from simrun import REPO

from hdl_bag.checks import run_checks
from hdl_bag.flows import compile_design, lint
from hdl_bag.flows.base import Finding
from hdl_bag.flows.sim import cocotb_tests, run_tests, simulate
from hdl_bag.gen import generate, include_dirs, sources
from hdl_bag.ir import Spec, dumps, load, resolve
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


STYLES = ["flat", "sv-interface"]
WAIVED = json.loads((CONFIG_ROOT / "waived_counts.json").read_text(encoding="utf-8"))


def _waived_snapshot(findings: Iterable[Finding]) -> dict[str, int | str]:
    """How many findings are waived, and which: a count alone would miss one waived finding
    replacing another. Lines are left out, so an unrelated edit does not move it."""
    rows = sorted(f"{f.tool} {f.file} {f.rule}" for f in findings if f.waived_by is not None)
    return {"count": len(rows), "which": hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]}


def test_run_cocotb_refuses_a_filter_that_selects_nothing() -> None:
    with pytest.raises(ValueError, match="selects no test"):
        simulate([], "top", HERE / "cocotb_external.py", REPO / "out", test_filter="no_such_test")


def test_waived_snapshot_sees_one_finding_replacing_another() -> None:
    a = Finding("slang", "rtl/a.sv", 1, "unused", "warning", "m", waived_by="r")
    b = Finding("slang", "rtl/b.sv", 1, "unused", "warning", "m", waived_by="r")
    assert _waived_snapshot([a])["count"] == _waived_snapshot([b])["count"]
    assert _waived_snapshot([a]) != _waived_snapshot([b])
    assert _waived_snapshot([a]) == _waived_snapshot(
        [replace(a, line=9), replace(b, waived_by=None)]
    )


def test_waived_snapshot_lists_every_gate_run() -> None:
    assert sorted(WAIVED) == sorted(f"{c}-{s}" for c in ALL for s in STYLES)


@pytest.mark.eda
@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("config", ALL)
def test_gate_lint_and_compile(config: str, style: str) -> None:
    configs, name = _split(config)
    root = REPO / "out" / "corpus" / f"{name}_{style}"
    out = generate(load(configs / f"{name}.ir.yaml"), root, configs, style)
    files, inc = sources(out), include_dirs(out)
    key = f"{config}-{style}"
    seen = REPO / "out" / "waived" / f"{key.replace('/', '__')}.json"
    seen.unlink(missing_ok=True)  # so a run that fails leaves nothing to refresh from
    lint_report = lint(files, out.top, root, incdirs=inc)
    for report in (
        lint_report,
        # Elaboration is the gate; test_route_simulation builds and runs the C++ model.
        compile_design(files, out.top, root, incdirs=inc, build=False),
    ):
        assert report.ok, report.format()
    # A waiver must be a decision: a change fails until the snapshot is refreshed
    # (scripts/waived_counts.py, from the files this writes).
    waived = _waived_snapshot(lint_report.findings)
    seen.parent.mkdir(parents=True, exist_ok=True)
    seen.write_text(json.dumps({"run": key, **waived}), encoding="utf-8")
    assert waived == WAIVED[key], lint_report.format()


# demo_ss alone took most of a CI shard, and a test cannot be split across shards. Nearly all
# its time is two cocotb tests, so it runs as parts: (label, cocotb filter, master slice).
_REACH = "every_master_reaches_what_the_model_says"
_ORDER = "axi_ports_take_every_handshake_ordering"
_REST = f"^(?!.*({_REACH}|{_ORDER}))"
ROUTE_PARTS: dict[str, list[tuple[str, str, str]]] = {
    "golden/demo_ss": [
        ("reach-0", _REACH, "0/2"),
        ("reach-1", _REACH, "1/2"),
        ("order", _ORDER, ""),
        ("rest", _REST, ""),
    ],
}


def _route_items() -> list[object]:
    items: list[object] = []
    for config in ROUTED:
        parts = ROUTE_PARTS.get(config)
        if parts is None:
            items.append(pytest.param(config, "", None, "", id=config))
        else:
            items += [pytest.param(config, *p, id=f"{config}-{p[0]}") for p in parts]
    return items


def _spec(config: str) -> Spec:
    configs, name = _split(config)
    return load(configs / f"{name}.ir.yaml")


def test_route_parts_cover_every_cocotb_test_and_master(tmp_path: Path) -> None:
    """Each part list runs every cocotb test, and those split by master run every master."""
    for config, parts in ROUTE_PARTS.items():
        configs, _ = _split(config)
        out = generate(_spec(config), tmp_path / config, configs)
        assert out.tests is not None
        module = tmp_path / config / out.tests
        ran = [n for _, f, _ in parts for n in dict.fromkeys(cocotb_tests(module, f))]
        assert sorted(set(ran)) == sorted(cocotb_tests(module))
        for test_filter in {f for _, f, _ in parts}:
            shards = [s for _, f, s in parts if f == test_filter]
            if shards != [""]:
                count = len(shards)
                assert count <= len(_spec(config).masters)
                assert sorted(shards) == sorted(f"{i}/{count}" for i in range(count))
                assert len(cocotb_tests(module, test_filter)) == 1
            else:
                assert len(shards) == 1


@pytest.mark.eda
@pytest.mark.parametrize(("config", "part", "test_filter", "masters"), _route_items())
def test_route_simulation(config: str, part: str, test_filter: str | None, masters: str) -> None:
    configs, name = _split(config)
    tag = f"{name}_{part}" if part else name
    root = REPO / "out" / "corpus" / f"{tag}_route"
    out = generate(_spec(config), root, configs)  # the config's own port style
    report = run_tests(out, test_filter=test_filter, env={"BAG_ROUTE_MASTERS": masters})
    assert report.ok, report.format()


# Each needs its own C++ build, so a representative set: every AXI fabric topology, the in-house
# and PULP bridges, adapters, and the largest config.
READY_SHIM = ["p3/axi_shared_rr", "p3/axil_xbar_rules", "p3/axi_adapters", "p3/bridges_inhouse",
              "p3/bridges_pulp", "golden/demo_ss"]  # fmt: skip


@pytest.mark.eda
@pytest.mark.parametrize("config", READY_SHIM)
def test_ready_driven_from_valid_settles_and_carries_traffic(config: str) -> None:
    """D38. A VALID that waits for READY would be a combinational loop through the shim."""
    configs, name = _split(config)
    out = generate(_spec(config), REPO / "out" / "corpus" / f"{name}_shim", configs)
    report = run_tests(out, ready_shim=True)
    assert report.ok, report.format()


@pytest.mark.parametrize("config", ALL)
def test_probes_exercise_every_protection_rule(config: str) -> None:
    """Each Access_Rule (of a master port) and each RO / WO / secure-only region is hit by a
    route-simulation probe that the reference model denies, so no configured protection
    passes silently because no probe reaches it."""
    from hdl_bag.tests_gen.route_model import probes, route

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
        widths = {f.name: (f.protocol, f.data_width) for f in s.fabrics}
        for b in s.bridges:
            if b.template == "bag_axi2apb" and widths[b.downstream][1] == 8:
                have.add(f"bag_axi2apb:{widths[b.upstream][0]}:{widths[b.upstream][1]}->8")
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
        "bag_axi2apb:AXI4:32->8", "bag_axi2apb:AXI4-Lite:32->8", "bag_axi2apb:AXI4-Lite:64->8",
    }  # fmt: skip
    assert required - have == set()
