# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The shrunk random seeds (owner, 2026-11-25: every seed a nightly failed on, once fixed, is
shrunk and joins the corpus). Each config in tests/configs/regress/ is what shrink.yml left of
a seed at a commit it failed on, with the shrinker of #213 and #219, so it still fails there
the way the seed did: a step, and for sim the one generated test. It must pass that now. The
step is judged as bag shrink judges it (hdl_bag.shrink.evaluate), at the default cocotb seed
it shrank with. Other generated tests are not this config's business: a shrunk config is cut
to its failure, so it may leave them nothing to check.
"""

from pathlib import Path

import pytest

from hdl_bag import ir
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.shrink.evaluate import build, lint_step, sim_step

REGRESS = Path(__file__).parents[1] / "configs" / "regress"

# seed: (step, generated test or None, fixing PR, the bug). The shrink run is in the PR that
# added the config.
SEEDS = {
    202611250040: ("sim", "every_master_reaches_what_the_model_says", "#209",
                   "a downsizer after an 8-bit split and an AXI4-Lite re-widening starts each "
                   "transfer at its own byte"),
    327: ("sim", "every_master_reaches_what_the_model_says", "#212",
          "a split read re-widened by an AXI4-Lite bus before a downsizer reads a word once "
          "per byte at or below it"),
}  # fmt: skip


def test_every_config_in_the_regress_corpus_has_an_entry() -> None:
    """A shrunk config without its step and bug would be run by nothing."""
    assert sorted(int(p.name.removesuffix(".ir.yaml")) for p in REGRESS.glob("*.ir.yaml")) == (
        sorted(SEEDS)
    )


@pytest.mark.eda
@pytest.mark.parametrize("seed", sorted(SEEDS))
def test_a_shrunk_seed_passes_the_step_it_failed(tmp_path: Path, seed: int) -> None:
    step, test, fix, bug = SEEDS[seed]
    built = build(to_spec_data(ir.load(REGRESS / f"{seed}.ir.yaml")), tmp_path, REGRESS)
    assert not isinstance(built, str), f"{seed} stops at {built}"
    spec, out = built
    run = lint_step() if step == "lint" else sim_step(test)
    assert run(spec, out) is None, f"{seed} fails {step} {test or ''} again ({fix}: {bug})"
