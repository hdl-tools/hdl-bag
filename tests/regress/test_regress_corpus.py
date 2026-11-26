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
is "absent" and the test must not be generated for the config. Where the fix was that formal_gen
no longer emits a cover the config can never reach, the step is "no-cover" and the entry names
the cover.
"""

from pathlib import Path

import pytest

from hdl_bag import ir
from hdl_bag.flows import run_tests
from hdl_bag.flows.sim import cocotb_tests
from hdl_bag.gen import generate
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.shrink.evaluate import build, lint_step
from hdl_bag.tests_gen import TESTS, applicable_tests
from hdl_bag.tests_gen.route_model import plain_word, private_words

REGRESS = Path(__file__).parents[1] / "configs" / "regress"

# seed: (step, generated test or cover, fixing PR, the bug). The step is "lint" or "sim" (EDA:
# the config must pass it), "absent" (host: the test must no longer be generated for it) or
# "no-cover" (host: formal_gen must no longer emit the cover). The shrink run is in the PR that
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
                   "the reset test's predicate uses the body's helper (plain_word): a minimal "
                   "config with traffic but no plain word"),
    202611240048: ("absent", "reset_during_traffic_leaves_nothing_behind", "#203",
                   "the reset test's predicate uses the body's helper (plain_word)"),
    202611180004: ("no-cover", "proto_wait_m0", "#217",
                   "an APB master that reaches no slave gets no wait cover: the decoder-mux "
                   "answers every transfer itself, with PREADY high (a minimal config: no "
                   "region, where the seed's Access_Rules denied m0)"),
    202611260015: ("sim", "every_master_reaches_what_the_model_says", "#222",
                   "an AXI4-Lite fabric drops a master's tied AxCACHE, so a pc onto an AXI4 "
                   "slave drives 0b0011"),
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
    """Host, no EDA. The fix was that the test does not apply to such a config: it has nothing
    to check. Pinned to why (review of #221): the reset test resets around each slave plain_word
    reaches, and these configs have traffic (private_words, what the old predicate asked) but no
    plain word, the case its predicate wrongly claimed."""
    _, test, fix, bug = SEEDS[seed]
    spec = ir.load(REGRESS / f"{seed}.ir.yaml")
    assert test in {t.name for t in TESTS}, f"{test} is gone, so this entry guards nothing"
    assert not any(plain_word(spec, m, s) for m in spec.masters for s in spec.slaves)
    assert any(private_words(spec, m, i, 8) for i, m in enumerate(spec.masters))
    assert test not in applicable_tests(spec), f"{seed} gets {test} again ({fix}: {bug})"


@pytest.mark.parametrize("seed", sorted(s for s, e in SEEDS.items() if e[0] == "no-cover"))
def test_a_shrunk_seed_no_longer_gets_the_cover_it_failed(tmp_path: Path, seed: int) -> None:
    """Host, no EDA. The fix was that formal_gen does not emit a cover this config can never
    reach. Pinned to why: the master's read and write covers are still there, so the protocol
    job still runs for it."""
    _, cover, fix, bug = SEEDS[seed]
    generate(ir.load(REGRESS / f"{seed}.ir.yaml"), tmp_path / "out", REGRESS)
    jobs = sorted((tmp_path / "out" / "formal").glob("*_protocol.sv"))
    text = "".join(p.read_text(encoding="utf-8") for p in jobs)
    master = cover.removeprefix("proto_wait_")
    assert f"proto_write_{master}: cover" in text and f"proto_read_{master}: cover" in text
    assert f"{cover}: cover" not in text, f"{seed} gets {cover} again ({fix}: {bug})"


@pytest.mark.eda
@pytest.mark.parametrize(
    "seed", sorted(s for s, e in SEEDS.items() if e[0] not in ("absent", "no-cover"))
)
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
