# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Phase 5 PR 3 (D69, D70): every Phase 4 package mutant also fails a formal property.

The mutants (mutations.py) each edit one table of one fabric in the generated parameter
package, or one in-house RTL file. Here a formal job of the config runs on the mutant and
must fail one of its assertions, whose reference comes from the IR, not from the package:
- the decode job (formal/<project>_decode.sv) for the decode tables and the decoder;
- the route job (formal/<project>_route.sv) for the fixed priorities (D70) and the bridges'
  address offsets (D69 part 3).
The control is the same job on the unmutated config, which must pass.
"""

from pathlib import Path

import pytest
from mutations import MUTATIONS, RtlMutation
from test_mutations import CONFIGS, IDS, RTL_IDS, RTL_TARGETS, TARGETS

from hdl_bag.flows.formal import run_sby
from hdl_bag.gen import generate
from hdl_bag.ir import Spec, load

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
    """JOB's (decode or route) failures on the output at ROOT, as "<file>: <message>"."""
    name = spec.project.name
    report = run_sby(root / "formal" / f"{name}_{job}.sby", root, timeout=1200)
    return [f"{f.file}: {f.message}" for f in report.findings]


def _caught(failed: list[str], spec: Spec, job: str, label: str = "") -> None:
    """FAILED includes an assertion of JOB's harness whose name contains LABEL."""
    harness = f"formal/{spec.project.name}_{job}.sv: "
    assert any(f.startswith(harness) and "assertion" in f and label in f for f in failed), failed


def test_every_package_mutation_fails_a_formal_property() -> None:
    """The pin (D70): each package mutation of the Phase 4 gate has a formal target here, and
    each bridge offset mutation one in the route job."""
    assert {m for m, _, _, _ in TARGETS} == set(MUTATIONS)
    assert {m for m, _, _, _ in DECODE_TARGETS + ROUTE_TARGETS} == set(MUTATIONS)
    assert {n for n, _, _ in ROUTE_RTL} == {"axi2apb_offset", "axi2axil_offset",
                                            "axil2apb_offset"}  # fmt: skip


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
    """The control: on each config the route mutants use, the unmutated route job passes,
    covers included."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    assert _failed(tmp_path, spec, "route") == []


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
    _caught(_failed(tmp_path, spec, "route"), spec, "route", "_before_")


@pytest.mark.eda
@pytest.mark.parametrize(("name", "mut", "config"), ROUTE_RTL, ids=ROUTE_RTL_IDS)
def test_the_route_job_catches_the_bridge_offset(tmp_path: Path, name: str, mut: RtlMutation,
                                                 config: str) -> None:  # fmt: skip
    """D69 part 3: a pass-through N bridge that forwards the address without its offset
    sends requests outside the window the IR gives."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_rtl(tmp_path, mut)
    _caught(_failed(tmp_path, spec, "route"), spec, "route", "_in_window")
