# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Phase 5 PR 3 (D69, D70): every Phase 4 package mutant also fails a formal property.

The mutants (mutations.py) each edit one table of one fabric in the generated parameter
package, or one in-house RTL file. Here the config's formal decode job runs on the mutant
and must fail an assertion of the decode harness (formal/<project>_decode.sv), whose
reference comes from the IR, not from the package. The control is the same job on the
unmutated config, which must pass.
"""

from pathlib import Path

import pytest
from mutations import MUTATIONS, RtlMutation
from test_mutations import CONFIGS, IDS, RTL_IDS, RTL_TARGETS, TARGETS

from hdl_bag.flows.formal import run_sby
from hdl_bag.gen import generate
from hdl_bag.ir import Spec, load

# The RTL mutants the decode job sees: those in the decoder it instantiates.
DECODE_RTL = [t for t in RTL_TARGETS if t[1].file == "bag_addr_decode.sv"]
DECODE_RTL_IDS = [i for t, i in zip(RTL_TARGETS, RTL_IDS, strict=True) if t in DECODE_RTL]


def _spec(config: str) -> Spec:
    return load(CONFIGS / f"{config}.ir.yaml")


def _failed(root: Path, spec: Spec) -> list[str]:
    """The decode job's failed assertions on the output at ROOT, as "<file>: <message>"."""
    name = spec.project.name
    report = run_sby(root / "formal" / f"{name}_decode.sby", root, timeout=600)
    return [f"{f.file}: {f.message}" for f in report.findings]


def test_every_package_mutation_fails_a_formal_property() -> None:
    """The pin (D70): each package mutation of the Phase 4 gate has a decode target here."""
    assert {m for m, _, _, _ in TARGETS} == set(MUTATIONS)
    assert {m for m, _, _, _ in DECODE_TARGETS} == set(MUTATIONS)


# Decode bugs: every Phase 4 target but the arbitration one, which D70 covers.
DECODE_TARGETS = [t for t in TARGETS if t[0] != "priority_inversion"]
DECODE_IDS = [i for t, i in zip(TARGETS, IDS, strict=True) if t[0] != "priority_inversion"]


@pytest.mark.eda
@pytest.mark.parametrize("config", sorted({t[1] for t in TARGETS} | {c for _, _, c in DECODE_RTL}))
def test_the_decode_job_holds_unmutated(tmp_path: Path, config: str) -> None:
    """The control: on each config the mutants use, the unmutated decode job passes."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    assert _failed(tmp_path, spec) == []


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config", "fabric", "last"), DECODE_TARGETS, ids=DECODE_IDS)
def test_the_decode_job_catches_the_mutation(tmp_path: Path, mutation: str, config: str,
                                             fabric: str, last: bool) -> None:  # fmt: skip
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    path = tmp_path / "rtl" / "pkg" / f"{spec.wrapper.package}.sv"
    path.write_text(MUTATIONS[mutation].apply(path.read_text(encoding="utf-8"), fabric, last),
                    encoding="utf-8", newline="\n")  # fmt: skip
    failed = _failed(tmp_path, spec)
    harness = f"formal/{spec.project.name}_decode.sv"
    assert failed, f"{mutation} on {config} survived the decode job"
    assert all(f.startswith(f"{harness}: ") and "assertion" in f for f in failed), failed


@pytest.mark.eda
@pytest.mark.parametrize(("name", "mut", "config"), DECODE_RTL, ids=DECODE_RTL_IDS)
def test_the_decode_job_catches_the_rtl_mutation(tmp_path: Path, name: str, mut: RtlMutation,
                                                 config: str) -> None:  # fmt: skip
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    path = tmp_path / "rtl" / "inhouse" / mut.file
    path.write_text(mut.apply(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    failed = _failed(tmp_path, spec)
    harness = f"formal/{spec.project.name}_decode.sv"
    assert failed, f"{name} ({mut.bug}) on {config} survived the decode job"
    assert all(f.startswith(f"{harness}: ") and "assertion" in f for f in failed), failed
