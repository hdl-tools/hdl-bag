# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""`bag shrink` against the real formal tools (issue #144), on a failure injected by the
mutation harness.

No legal spec fails a formal job, so every candidate the search generates is mutated after
`generate` with the `decode_short` package mutant, which the decode job is known to catch
(tests/mutation/test_decode_mutations.py). The failure is deterministic and the search is
real: sby accepts or rejects each reduction. A candidate whose package the mutation cannot
edit is left alone and passes."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from mutations import MUTATIONS, MutationError

from hdl_bag import ir
from hdl_bag.cli import main
from hdl_bag.flows.formal import run_sby
from hdl_bag.gen import Outputs, generate
from hdl_bag.shrink import evaluate

P2 = Path(__file__).parents[1] / "configs" / "p2"
FABRIC = "Ahb"  # the package prefix the mutant names for the `ahb` fabric


def mutate(spec: ir.Spec, out: Outputs) -> bool:
    """Apply decode_short to the generated package of SPEC; False if it has nothing to edit."""
    path = out.root / "rtl" / "pkg" / f"{spec.wrapper.package}.sv"
    try:
        mutant = MUTATIONS["decode_short"].apply(path.read_text(encoding="utf-8"), FABRIC, False)
    except MutationError:
        return False
    path.write_text(mutant, encoding="utf-8", newline="\n")
    return True


@pytest.mark.eda
def test_a_decode_mutant_shrinks_to_the_one_route_that_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every candidate carries the decode mutant, so the search keeps exactly the edits after
    which sby still fails the decode job with FORMAL-ASSERT. The `reproduce:` line is not
    asserted: the bug is injected, so `bag flow` on the shrunk IR does not fail."""
    shutil.copy(P2 / "ahb_fixed.ir.yaml", tmp_path / "ahb_fixed.ir.yaml")
    shutil.copy(P2 / "header.txt", tmp_path / "header.txt")
    monkeypatch.chdir(tmp_path)

    def seeded(spec: ir.Spec, out: Path, spec_dir: Path) -> Outputs:
        outputs = generate(spec, out, spec_dir)
        mutate(spec, outputs)
        return outputs

    monkeypatch.setattr(evaluate, "generate", seeded)  # the name build() calls
    result = CliRunner().invoke(
        main, ["shrink", "ahb_fixed.ir.yaml", "--step", "formal", "--timeout", "120",
               "--budget", "80"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "baseline: formal ahb_fixed_decode:prove fails with FORMAL-ASSERT"
    assert "stopped at the budget" not in result.output
    kept = [ln for ln in lines if ln.startswith("kept: ")]
    assert len(kept) >= 4, result.output
    # The tool rejected this edit: without the fabric no decode job fails.
    assert "kept: drop fabric ahb and its ports" not in kept, result.output

    shrunk = ir.load(tmp_path / "ahb_fixed.shrunk.ir.yaml")
    # The offline model's prediction. CI shows what the tools really keep: pin it from the
    # run (cite its id here) and read why before changing any of these.
    assert [m.name for m in shrunk.masters] == ["m2"]
    assert [s.name for s in shrunk.slaves] == ["s2"]
    assert [f.name for f in shrunk.fabrics] == ["ahb"]

    # Independent of the search: the minimal design passes unmutated and fails only as seeded.
    final = generate(shrunk, tmp_path / "final", tmp_path)
    job = final.root / "formal" / f"{shrunk.project.name}_decode.sby"
    assert run_sby(job, final.root, timeout=120).ok
    assert mutate(shrunk, final), "the mutant found nothing to edit in the shrunk package"
    report = run_sby(job, final.root, timeout=120)
    assert {f.rule for f in report.unwaived} == {"FORMAL-ASSERT"}, report.findings
