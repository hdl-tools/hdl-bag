# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Phase 5 PR 3 (D69, D70): every Phase 4 package mutant also fails a formal property.

The mutants (mutations.py) each edit one table of one fabric in the generated parameter
package, or one in-house RTL file. Here a formal job of the config runs on the mutant and
must fail one of its assertions, whose reference comes from the IR, not from the package:
- the decode job (formal/<project>_decode.sv) for the decode tables and the decoder;
- the route jobs (formal/<project>_route_<unit>.sv, one per fabric or bridge) for the fixed
  priorities (D70) and the bridges' address offsets (D69 part 3): the mutated units' jobs.
The control is the same job, or every route job, on the unmutated config, which must pass.
"""

from pathlib import Path

import pytest
from mutations import MUTATIONS, RtlMutation
from test_mutations import CONFIGS, IDS, RTL_IDS, RTL_TARGETS, TARGETS

from hdl_bag.flows.formal import run_sby
from hdl_bag.formal_gen.units import units
from hdl_bag.gen import generate
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.gen.render import camel
from hdl_bag.ir import Spec, load
from hdl_bag.templates import builtin_registry

# Which job catches what: the decode job every table bug but the priorities; the route job
# the priorities and the bridges' offsets.
DECODE_TARGETS = [t for t in TARGETS if t[0] != "priority_inversion"]
DECODE_IDS = [i for t, i in zip(TARGETS, IDS, strict=True) if t[0] != "priority_inversion"]
ROUTE_TARGETS = [t for t in TARGETS if t[0] == "priority_inversion"]
ROUTE_IDS = [i for t, i in zip(TARGETS, IDS, strict=True) if t[0] == "priority_inversion"]
DECODE_RTL = [t for t in RTL_TARGETS if t[1].file == "bag_addr_decode.sv"]
DECODE_RTL_IDS = [i for t, i in zip(RTL_TARGETS, RTL_IDS, strict=True) if t in DECODE_RTL]
ROUTE_RTL = [t for t in RTL_TARGETS if t[0].endswith("_offset")]
ROUTE_RTL_IDS = [i for t, i in zip(RTL_TARGETS, RTL_IDS, strict=True) if t in ROUTE_RTL]


def _spec(config: str) -> Spec:
    return load(CONFIGS / f"{config}.ir.yaml")


def _failed(root: Path, spec: Spec, job: str) -> list[str]:
    """JOB's (decode, or route_<unit>) failures on the output at ROOT, as "<file>: <message>"."""
    name = spec.project.name
    report = run_sby(root / "formal" / f"{name}_{job}.sby", root, timeout=1200)
    return [f"{f.file}: {f.message}" for f in report.findings]


def _route_failed(root: Path, spec: Spec, unit_names: list[str]) -> list[str]:
    """The failures of the route jobs of UNIT_NAMES (each its own job), in order."""
    assert unit_names, "no route job to run"
    return [f for u in unit_names for f in _failed(root, spec, f"route_{u}")]


def _route_units(spec: Spec, *, source: str = "", prefix: str = "") -> list[str]:
    """SPEC's route units (fabrics that decode, and bridges): all, or those whose template
    reads rtl/inhouse/SOURCE, or the fabric whose package prefix (as the mutants name it) is
    PREFIX."""
    registry = builtin_registry()
    found = units(build_netlist(spec))
    if source:
        manifests = {i.template: registry.get(i.template) for i in found}
        found = [i for i in found if (m := manifests[i.template]) is not None
                 and f"rtl/inhouse/{source}" in m.sources]  # fmt: skip
    if prefix:
        found = [i for i in found if camel(i.name) == prefix]
    return [i.name for i in found]


def _caught(failed: list[str], spec: Spec, job: str, label: str = "") -> None:
    """FAILED includes an assertion of a harness of JOB (decode, or route: any unit's) whose
    name contains LABEL."""
    harness = f"formal/{spec.project.name}_{job}"
    assert any(f.startswith(harness) and "assertion" in f and label in f for f in failed), failed


def test_every_package_mutation_fails_a_formal_property() -> None:
    """The pin (D70): each package mutation of the Phase 4 gate has a formal target here, and
    each bridge offset mutation one in the route job."""
    assert {m for m, _, _, _ in TARGETS} == set(MUTATIONS)
    assert {m for m, _, _, _ in DECODE_TARGETS + ROUTE_TARGETS} == set(MUTATIONS)
    assert {n for n, _, _ in ROUTE_RTL} == {"axi2apb_offset", "axi2axil_offset",
                                            "axil2apb_offset"}  # fmt: skip
    # Each route mutant has a unit job to fail: its fabric, or a bridge reading its file.
    for _, config, fabric, _ in ROUTE_TARGETS:
        assert _route_units(_spec(config), prefix=fabric), (config, fabric)
    for name, mut, config in ROUTE_RTL:
        assert _route_units(_spec(config), source=mut.file), (name, config)


@pytest.mark.eda
@pytest.mark.parametrize("config", sorted({t[1] for t in TARGETS} | {c for _, _, c in DECODE_RTL}))
def test_the_decode_job_holds_unmutated(tmp_path: Path, config: str) -> None:
    """The control: on each config the mutants use, the unmutated decode job passes."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    assert _failed(tmp_path, spec, "decode") == []


@pytest.mark.eda
@pytest.mark.parametrize("config", sorted({t[1] for t in ROUTE_TARGETS}
                                          | {c for _, _, c in ROUTE_RTL}))  # fmt: skip
def test_the_route_job_holds_unmutated(tmp_path: Path, config: str) -> None:
    """The control: on each config the route mutants use, every unmutated route job passes,
    covers included."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    assert _route_failed(tmp_path, spec, _route_units(spec)) == []


def _mutate_package(root: Path, spec: Spec, mutation: str, fabric: str, last: bool) -> None:
    path = root / "rtl" / "pkg" / f"{spec.wrapper.package}.sv"
    path.write_text(MUTATIONS[mutation].apply(path.read_text(encoding="utf-8"), fabric, last),
                    encoding="utf-8", newline="\n")  # fmt: skip


def _mutate_rtl(root: Path, mut: RtlMutation) -> None:
    path = root / "rtl" / "inhouse" / mut.file
    path.write_text(mut.apply(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config", "fabric", "last"), DECODE_TARGETS, ids=DECODE_IDS)
def test_the_decode_job_catches_the_mutation(tmp_path: Path, mutation: str, config: str,
                                             fabric: str, last: bool) -> None:  # fmt: skip
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_package(tmp_path, spec, mutation, fabric, last)
    failed = _failed(tmp_path, spec, "decode")
    assert failed, f"{mutation} on {config} survived the decode job"
    harness = f"formal/{spec.project.name}_decode.sv: "
    assert all(f.startswith(harness) and "assertion" in f for f in failed), failed


@pytest.mark.eda
@pytest.mark.parametrize(("name", "mut", "config"), DECODE_RTL, ids=DECODE_RTL_IDS)
def test_the_decode_job_catches_the_rtl_mutation(tmp_path: Path, name: str, mut: RtlMutation,
                                                 config: str) -> None:  # fmt: skip
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_rtl(tmp_path, mut)
    failed = _failed(tmp_path, spec, "decode")
    assert failed, f"{name} ({mut.bug}) on {config} survived the decode job"
    harness = f"formal/{spec.project.name}_decode.sv: "
    assert all(f.startswith(harness) and "assertion" in f for f in failed), failed


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config", "fabric", "last"), ROUTE_TARGETS, ids=ROUTE_IDS)
def test_the_route_job_catches_the_priority_inversion(tmp_path: Path, mutation: str, config: str,
                                                      fabric: str, last: bool) -> None:  # fmt: skip
    """D70: with the fixed priorities reversed, a lower-priority master goes first."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_package(tmp_path, spec, mutation, fabric, last)
    _caught(_route_failed(tmp_path, spec, _route_units(spec, prefix=fabric)), spec, "route",
            "_before_")  # fmt: skip


@pytest.mark.eda
@pytest.mark.parametrize(("name", "mut", "config"), ROUTE_RTL, ids=ROUTE_RTL_IDS)
def test_the_route_job_catches_the_bridge_offset(tmp_path: Path, name: str, mut: RtlMutation,
                                                 config: str) -> None:  # fmt: skip
    """D69 part 3: a pass-through N bridge that forwards the address without its offset
    sends requests outside the window the IR gives."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_rtl(tmp_path, mut)
    bridges = _route_units(spec, source=mut.file)
    _caught(_route_failed(tmp_path, spec, bridges), spec, "route", "_in_window")
