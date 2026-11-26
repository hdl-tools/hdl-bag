# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The shrunk random seeds (owner, 2026-11-25: every seed a nightly failed on, once fixed, is
shrunk and joins the corpus). Each config in tests/configs/regress/ is what shrink.yml left of
a seed at a commit it failed on, with the shrinker of #213 and #219, so it still fails there
the way the seed did: a step, and for sim the one generated test. It must pass that now, at
the default cocotb seed it shrank with. Unlike bag shrink's judge, a test that is no longer
generated for the config, or a simulator that is missing, is a failure here, not a pass (review
of #220). Other generated tests are not this config's business: a shrunk config is cut to its
failure, so it may leave them nothing to check. Each config was checked once to fail its test
at the commit before its fix (the PR that added it says how). Where the fix was that the test
no longer applies (its predicate claimed a config its body could not check), the entry's step
is "absent" and the test must not be generated for the config.
"""

from pathlib import Path

import pytest

from hdl_bag import ir
from hdl_bag.flows import run_tests
from hdl_bag.flows.sim import cocotb_tests
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.shrink.evaluate import build, lint_step
from hdl_bag.tests_gen import applicable_tests

REGRESS = Path(__file__).parents[1] / "configs" / "regress"

# seed: (step, generated test or None, fixing PR, the bug). The shrink run is in the PR that
# added the config.
SEEDS = {
    202611250040: ("sim", "every_master_reaches_what_the_model_says", "#209",
                   "a downsizer after an 8-bit split and an AXI4-Lite re-widening starts each "
                   "transfer at its own byte"),
    202611240002: ("sim", "narrow_apb_behind_axi2apb_keeps_lanes_strobes_and_errors", "#202",
                   "the narrow-APB byte write takes only a path that keeps the strobes "
                   "(a bridge with PSTRB tied stores the whole word)"),
    202611240024: ("sim", "axi4_bursts_reach_slaves_and_error_paths", "#203",
                   "a burst refused in front of the decode answers SLVERR on a miss (D104)"),
    202611240036: ("absent", "reset_during_traffic_leaves_nothing_behind", "#203",
                   "the reset test's predicate uses the body's helper (plain_word)"),
    202611240048: ("absent", "reset_during_traffic_leaves_nothing_behind", "#203",
                   "the reset test's predicate uses the body's helper (plain_word)"),
    327: ("sim", "every_master_reaches_what_the_model_says", "#212",
          "a split read re-widened by an AXI4-Lite bus before a downsizer reads a word once "
          "per byte at or below it"),
}  # fmt: skip


def test_every_config_in_the_regress_corpus_has_an_entry() -> None:
    """A shrunk config without its step and bug would be run by nothing."""
    assert sorted(int(p.name.removesuffix(".ir.yaml")) for p in REGRESS.glob("*.ir.yaml")) == (
        sorted(SEEDS)
    )


@pytest.mark.parametrize("seed", sorted(s for s, e in SEEDS.items() if e[0] == "absent"))
def test_a_shrunk_seed_no_longer_gets_the_test_it_failed(seed: int) -> None:
    """The fix was that the test does not apply to such a config: it has nothing to check."""
    _, test, fix, bug = SEEDS[seed]
    assert test not in applicable_tests(ir.load(REGRESS / f"{seed}.ir.yaml")), (
        f"{seed} gets {test} again ({fix}: {bug})"
    )


@pytest.mark.eda
@pytest.mark.parametrize("seed", sorted(s for s, e in SEEDS.items() if e[0] != "absent"))
def test_a_shrunk_seed_passes_the_step_it_failed(tmp_path: Path, seed: int) -> None:
    step, test, fix, bug = SEEDS[seed]
    built = build(to_spec_data(ir.load(REGRESS / f"{seed}.ir.yaml")), tmp_path, REGRESS)
    assert not isinstance(built, str), f"{seed} stops at {built}"
    spec, out = built
    again = f"{seed} fails {step} {test or ''} again ({fix}: {bug})"
    if step == "lint":
        assert lint_step()(spec, out) is None, again
        return
    assert out.tests is not None and test in cocotb_tests(out.root.resolve() / out.tests), (
        f"{seed}: {test} is no longer generated for it, so it checks nothing"
    )
    report = run_tests(out, test_filter=rf"\.{test}$")
    assert report.ok, f"{again}: {[f.format() for f in report.unwaived]}"
