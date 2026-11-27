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

from hdl_bag import formal_gen, ir
from hdl_bag.flows import run_tests
from hdl_bag.flows.sim import cocotb_tests
from hdl_bag.gen import generate
from hdl_bag.ir import Spec
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.shrink.evaluate import build, lint_step
from hdl_bag.tests_gen import TESTS, applicable_tests
from hdl_bag.tests_gen.route_model import plain_word, private_words, stores_single_bytes

REGRESS = Path(__file__).parents[1] / "configs" / "regress"

# seed: (step, generated test or cover, fixing PR, the bug). The step is "lint" or "sim" (EDA:
# the config must pass it), "absent" (host: the test must no longer be generated for it) or
# "no-cover" (host: formal_gen must no longer emit the cover). The shrink run is in the PR that
# added the config.
SEEDS = {
    202611250040: ("sim", "every_master_reaches_what_the_model_says", "#209",
                   "a downsizer after an 8-bit split and an AXI4-Lite re-widening starts each "
                   "transfer at its own byte"),
    202611200006: ("sim", "every_master_reaches_what_the_model_says", "#226",
                   "a split access whose later transfer misses the slave's region is an error "
                   "(shrunk at 13a322e for #214's route test, it drifted to this: a 4-byte "
                   "region)"),
    202611220001: ("absent", "apb_byte_writes_carry_pstrb", "#200",
                   "the PSTRB test runs only on a path that keeps the strobes "
                   "(stores_single_bytes): here a bag_ahb2apb with PSTRB tied stores whole words"),
    202611220012: ("sim", "axi_shared_bus_midpoint_buffers_reads", "#200",
                   "with Max mst trans 1 the guard's axi_demux_simple admits one read in all "
                   "(D80), not the mid-point's depth (midpoint_reads)"),
    202611220021: ("sim", "axi_arbitration_rounds_starvation_and_lock", "#200",
                   "a deep adapter path absorbs more writes than 16 slots per master left room "
                   "for: up to 64, as the region allows (the shrink cut s1's region to 512 "
                   "bytes, under even that; it was set back to 4 KB by hand)"),
    202611220028: ("sim", "every_master_reaches_what_the_model_says", "#200",
                   "a read split by a narrow bus (bag_axi2apb's bag_axil2apb onto 16-bit APB) "
                   "is several transfers: a later AXI4 downsizer no longer trims the word below "
                   "the address"),
    202611220042: ("sim", "axi_ports_take_every_handshake_ordering", "#200",
                   "a default slave whose own region is denied (D32) is reached only by misses: "
                   "the ordering test falls back to the probe addresses"),
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
                   "slave drives 0b11 (the shrink cut every region; one 4 KB region for s0 was "
                   "restored by hand, else the route test's error self-check has no error word)"),
    327: ("sim", "every_master_reaches_what_the_model_says", "#212",
          "a split read re-widened by an AXI4-Lite bus before a downsizer reads a word once "
          "per byte at or below it"),
}  # fmt: skip


def test_every_config_in_the_regress_corpus_has_an_entry() -> None:
    """A shrunk config without its step and bug would be run by nothing."""
    assert sorted(int(p.name.removesuffix(".ir.yaml")) for p in REGRESS.glob("*.ir.yaml")) == (
        sorted(SEEDS)
    )


def _reset_case(spec: Spec) -> bool:
    """The reset test resets around each slave plain_word reaches: traffic (private_words, what
    its old predicate asked) but no plain word is the case that predicate wrongly claimed."""
    return not any(plain_word(spec, m, s) for m in spec.masters for s in spec.slaves) and any(
        private_words(spec, m, i, 8) for i, m in enumerate(spec.masters)
    )


def _pstrb_case(spec: Spec) -> bool:
    """The PSTRB test's old predicate took any plain word of an AXI master on a 32-bit APB4
    slave; the case it wrongly claimed is one whose path does not keep the strobes."""
    found = [plain_word(spec, m, s) for m in spec.masters if m.protocol in ("AXI4", "AXI4-Lite")
             for s in spec.slaves if s.protocol == "APB4" and s.data_width == 32]  # fmt: skip
    hits = [f for f in found if f]
    return bool(hits) and not any(stores_single_bytes(spec, f[1]) for f in hits)


# Per test an "absent" entry may name: the case its old predicate wrongly claimed.
ABSENT_CASES = {
    "reset_during_traffic_leaves_nothing_behind": _reset_case,
    "apb_byte_writes_carry_pstrb": _pstrb_case,
}


@pytest.mark.parametrize("seed", sorted(s for s, e in SEEDS.items() if e[0] == "absent"))
def test_a_shrunk_seed_no_longer_gets_the_test_it_failed(seed: int) -> None:
    """Host, no EDA. The fix was that the test does not apply to such a config: it has nothing
    to check. Pinned to why (review of #221): the config is still the case the test's old
    predicate wrongly claimed (ABSENT_CASES)."""
    _, test, fix, bug = SEEDS[seed]
    spec = ir.load(REGRESS / f"{seed}.ir.yaml")
    assert test in {t.name for t in TESTS}, f"{test} is gone, so this entry guards nothing"
    assert test in ABSENT_CASES, f"{seed}: no ABSENT_CASES entry says why {test} is absent"
    assert ABSENT_CASES[test](spec), f"{seed} is no longer the case {test}'s old predicate took"
    assert test not in applicable_tests(spec), f"{seed} gets {test} again ({fix}: {bug})"


@pytest.mark.parametrize("seed", sorted(s for s, e in SEEDS.items() if e[0] == "no-cover"))
def test_a_shrunk_seed_no_longer_gets_the_cover_it_failed(tmp_path: Path, seed: int) -> None:
    """Host, no EDA. The fix was that formal_gen does not emit a cover this config can never
    reach. Pinned to why: the master's read and write covers are still there, so the protocol
    job still runs for it."""
    _, cover, fix, bug = SEEDS[seed]
    assert cover.startswith("proto_wait_"), cover  # the only kind of cover this guards so far
    # The label is still one formal_gen emits, so its absence below means something.
    assert '"proto_wait_' in Path(formal_gen.__file__).read_text(encoding="utf-8")
    generate(ir.load(REGRESS / f"{seed}.ir.yaml"), tmp_path / "out", REGRESS)
    jobs = sorted((tmp_path / "out" / "formal").glob("*_protocol.sv"))
    assert jobs, f"{seed}: no protocol job"
    text = "".join(p.read_text(encoding="utf-8") for p in jobs)
    master = cover.removeprefix("proto_wait_")
    assert f"proto_write_{master}: cover" in text and f"proto_read_{master}: cover" in text
    assert f"{cover}: cover" not in text, f"{seed} gets {cover} again ({fix}: {bug})"


@pytest.mark.eda
@pytest.mark.parametrize("seed", sorted(s for s, e in SEEDS.items() if e[0] in ("lint", "sim")))
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
