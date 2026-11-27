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
the cover. A "formal" entry names the job and task ("reset:cover") it must now pass.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from hdl_bag import formal_gen, ir
from hdl_bag.flows import run_sby, run_tests
from hdl_bag.flows.sim import cocotb_tests
from hdl_bag.gen import generate
from hdl_bag.ir import Spec
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.regress.run import formal_tasks
from hdl_bag.shrink.evaluate import build, lint_step
from hdl_bag.tests_gen import TESTS, applicable_tests
from hdl_bag.tests_gen import route_model as rm
from hdl_bag.tests_gen.route_model import plain_word, private_words, stores_single_bytes

REGRESS = Path(__file__).parents[1] / "configs" / "regress"

# seed: (step, generated test or cover, fixing PR, the bug). The step is "lint" or "sim" (EDA:
# the config must pass it), "formal" (EDA: the named job:task must pass), "absent" (host: the
# test must no longer be generated for it) or "no-cover" (host: formal_gen must no longer emit
# the cover). The shrink run is in the PR that
# added the config.
SEEDS = {
    202611180040: ("lint", None, "#233",
                   "an UNOPTFLAT through whole-struct ports (bag_axi2axil, bag_axi_dw, the PULP "
                   "xbar) is no loop at bit level, so Yosys's check waives it (D111)"),
    202611170026: ("formal", "reset:cover", "#201",
                   "the reset job's covers are reached at step 40, one past a cover depth of "
                   "40: COVER_DEPTH is 80"),
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
    202611220017: ("sim", "every_master_reaches_what_the_model_says", "#198",
                   "APB3 has no PPROT: the bridge after an APB3 bus drives the default PROT "
                   "(D28), whatever the master sent (it also needs #226: a write split past "
                   "its 4-byte region)"),
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
    202611900021: ("sim", "axi_arbitration_rounds_starvation_and_lock", "serial-writes",
                   "a fixed-priority shared bus whose highest master sits behind a width "
                   "converter, which sends one write at a time: the low master is rightly served "
                   "in the gaps (the shrink stopped at its 40-run budget)"),
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
    assert cover is not None, f"{seed}: a no-cover entry names its cover"
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
@pytest.mark.parametrize(
    "seed", sorted(s for s, e in SEEDS.items() if e[0] in ("lint", "sim", "formal"))
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
    if step == "formal":
        assert test is not None, f"{seed}: a formal entry names its job:task"
        job, task = test.split(":")
        stem = f"{spec.project.name}_{job}"
        rel = next((r for r, t in formal_tasks(out) if Path(r).stem == stem and t == task), None)
        assert rel is not None, f"{seed}: {test} is no longer generated, so it checks nothing"
        root = out.root.resolve()
        # The cover #201's depth was about is still there (review of #237: a task with no
        # covers would show nothing).
        job_sv = (root / rel).with_suffix(".sv").read_text(encoding="utf-8")
        assert "reset_mid_s3_axil_arvalid" in job_sv, f"{seed}: the cover #201 needed is gone"
        report = run_sby(root / rel, root, task=task)
        assert report.ok, f"{again}: {[f.format() for f in report.unwaived]}"
        return
    assert out.tests is not None and test in cocotb_tests(out.root.resolve() / out.tests), (
        f"{seed}: {test} is no longer generated for it, so it checks nothing"
    )
    report = run_tests(out, test_filter=rf"\.{test}$")
    assert report.ok, f"{again}: {[f.format() for f in report.unwaived]}"


# ---------------------------------------------------------------- what each sim entry still has
# Review of #227: a sim entry passing shows only that its test runs and passes; each also
# keeps the condition its bug needs, so a later change to the model or the config cannot make
# it pass without checking anything. Each predicate states that condition with the route model.


def _accesses(spec: Spec, write: bool) -> Iterator[tuple[Any, int, rm.Outcome]]:
    for m in spec.masters:
        for a in rm.probes(spec):
            if a < 1 << m.addr_width:
                yield m, a, rm.route(spec, m.name, a, write, False, {})


def _bus(spec: Spec, bridge: str) -> Any:
    b = next(x for x in spec.bridges if x.name == bridge)
    return next(f for f in spec.fabrics if f.name == b.downstream)


def _lite_drops_cache(spec: Spec) -> bool:
    """#222: a master with CACHE tied to 0 reaches an AXI4 slave across AXI4-Lite, which drops
    AxCACHE, so the slave sees 0b11."""
    return any(
        e.slave in {s.name for s in spec.slaves if s.protocol == "AXI4"}
        and rm.cache_after_tie(spec, m.name, 0b11) == 0
        and rm.attributes(spec, m, 0, False, e.bridges, at_slave=True)[3] == 0b11
        for m, _, e in _accesses(spec, True)
    )


def _partial_miss(spec: Spec) -> bool:
    """#226: a split access some of whose transfers miss."""
    return any(e.partial for w in (True, False) for _, _, e in _accesses(spec, w))


def _midpoint_one_read(spec: Spec) -> bool:
    """#200: an AXI4 shared-bus mid-point the test reads through, with Max mst trans 1, a
    master with IDs and room for more than one read: the guard then admits one read in all
    (D80), not min(buffered, trans << id_width)."""
    for f in spec.fabrics:
        found = rm.midpoint_path(spec, f) if f.protocol == "AXI4" else None
        if found and f.max_mst_trans == 1 and (found[0].id_width or 0) >= 1 and found[2] > 1:
            return True
    return False


def _default_only_by_miss(spec: Spec) -> bool:
    """#200: a default slave no master reaches at its own regions, but some access reaches."""
    for s in {d.default_slave for f in spec.fabrics for d in f.decode if d.default_slave}:
        own = [r.base for r in spec.memory_map if r.slave == s]
        if own and not any(rm.route(spec, m.name, b, True, False, {}).slave == s
                   for m in spec.masters for b in own if b < 1 << m.addr_width) and any(
            e.slave == s for _, _, e in _accesses(spec, True)
        ):  # fmt: skip
            return True
    return False


def _apb3_before_a_bridge(spec: Spec) -> bool:
    """#198: an access crosses an APB3 bus (no PPROT) and then another bridge."""
    return any(
        e.slave and any(_bus(spec, b).protocol == "APB3" for b in e.bridges[:-1])
        for _, _, e in _accesses(spec, True)
    )


def _reads_a_word_again(spec: Spec) -> bool:
    """#212: a read that may reach one slave word more times than its access has bytes: the
    split beat, re-widened by an AXI4-Lite bus, is wider than the access (reads_per_word's
    span term; without it the bound is access_bytes)."""
    return any(e.slave and rm.reads_per_word(spec, m, e) > rm.access_bytes(m)
               for m, _, e in _accesses(spec, False))  # fmt: skip


def _fixed_priority_top_sends_one_write_at_a_time(spec: Spec) -> bool:
    """A fixed-priority AXI fabric's contention test where every master but the last reaches it
    through an AXI4 width converter, so the starvation check has no master whose writes stay
    queued (queued_master, as the test body): PULP's converters take an AW only once the last
    write's W has left, so the arbiter sees gaps in each queue."""
    for f in spec.fabrics:
        if f.protocol not in ("AXI4", "AXI4-Lite") or f.arbitration != "fixed-priority":
            continue
        found = rm.contention(spec, f)
        if found:
            by_prio = [p.port for p in sorted(found[1], key=lambda p: p.priority or 0)]
            if rm.queued_master(spec, f, by_prio) is None:
                return True
    return False


def _contention_behind_adapters(spec: Spec) -> bool:
    """#200: an AXI fabric's contention test whose masters sit behind deep adapters (a clock
    crossing, a FIFO or a multi-stage cut), which absorb more writes than 16 slots per master
    left room for. (The region's room, set back to 4 KB by hand for 021, is not pinned.)"""

    def deep(a: Any) -> bool:
        return a.type in ("cdc", "fifo") or (a.type == "cut" and a.params.get("stages", 1) >= 2)

    for f in spec.fabrics:
        found = rm.contention(spec, f) if f.protocol in ("AXI4", "AXI4-Lite") else None
        ports = {p.port for p in found[1]} if found else set()
        if any(a.fabric == f.name and a.port in ports and deep(a) for a in spec.adapters):
            return True
    return False


def _tied_pstrb_before_narrow_apb(spec: Spec) -> bool:
    """#202: a plain word of an APB slave narrower than 32 bits through a bridge with PSTRB
    tied."""
    for m in spec.masters:
        for s in spec.slaves:
            found = plain_word(spec, m, s) if s.protocol in ("APB3", "APB4") else None
            if (
                found
                and 8 < s.data_width < 32
                and any(
                    rm._option(spec, b, "pstrb") in ("tie-0", "tie-default")
                    for b in found[1].bridges
                )
            ):
                return True
    return False


def _refused_miss(spec: Spec) -> bool:
    """#203: the burst test's miss address is refused by a converter in front of the decode."""
    for k, m in enumerate(spec.masters):
        _, miss = rm.burst_path(spec, m, k) if m.protocol == "AXI4" else ([], None)
        if miss is not None and rm.refused_access(
            spec, m, rm.route(spec, m.name, miss, False, False, {}), 0, False, miss, 4, 8
        ):
            return True
    return False


def _whole_struct_loop_path(spec: Spec) -> bool:
    """#233 (D111): a bag_axi2axil off an AXI4 fabric whose ports have both an upsizing and a
    downsizing width converter, the whole-struct nodes Verilator's false loop ran through."""
    for b in spec.bridges:
        if b.template != "bag_axi2axil":
            continue
        dws = [a for a in spec.adapters if a.fabric == b.upstream and a.type == "dw"]
        up = [int(a.params["from"]) < int(a.params["to"]) for a in dws]
        if any(up) and not all(up):
            return True
    return False


def _narrow_then(spec: Spec, protocol: str) -> bool:
    """A read split by a bus narrower than 32 bits, then a PROTOCOL fabric wider than that bus
    and wider than something after it (a downsizer's way down: the next bus or the slave)."""
    for _, _, e in _accesses(spec, False):
        if not e.slave:
            continue
        slave = next(s for s in spec.slaves if s.name == e.slave)
        widths = [_bus(spec, b).data_width for b in e.bridges] + [slave.data_width]
        kinds = [_bus(spec, b).protocol for b in e.bridges] + ["slave"]
        for i, w in enumerate(widths[:-1]):
            for j in range(i + 1, len(widths) - 1):
                if (w < 32 and kinds[j] == protocol and widths[j] > w
                        and min(widths[j + 1 :]) < widths[j]):  # fmt: skip
                    return True
    return False


SIM_CASES: dict[int, Callable[[Spec], bool]] = {
    202611180040: _whole_struct_loop_path,
    202611200006: _partial_miss,
    202611220012: _midpoint_one_read,
    # #198, and #226 for the write split past its 4-byte region.
    202611220017: lambda spec: _apb3_before_a_bridge(spec) and _partial_miss(spec),
    202611220021: _contention_behind_adapters,
    # #200: a later AXI4 downsizer no longer trims below the address after the split.
    202611220028: lambda spec: _narrow_then(spec, "AXI4"),
    202611220042: _default_only_by_miss,
    202611240002: _tied_pstrb_before_narrow_apb,
    202611240024: _refused_miss,
    # #209: the downsizer after an 8-bit split and an AXI4-Lite re-widening.
    202611250040: lambda spec: _narrow_then(spec, "AXI4-Lite"),
    202611260015: _lite_drops_cache,
    202611900021: _fixed_priority_top_sends_one_write_at_a_time,
    327: _reads_a_word_again,
}


@pytest.mark.parametrize("seed", sorted(s for s, e in SEEDS.items() if e[0] in ("lint", "sim")))
def test_a_shrunk_seed_keeps_what_its_bug_needs(seed: int) -> None:
    """Host, no EDA (review of #227): the config still has its bug's condition (SIM_CASES)."""
    assert seed in SIM_CASES, f"{seed}: no SIM_CASES entry says what its bug needs"
    assert set(SIM_CASES) <= {s for s, e in SEEDS.items() if e[0] in ("lint", "sim")}
    _, _, fix, bug = SEEDS[seed]
    assert SIM_CASES[seed](ir.load(REGRESS / f"{seed}.ir.yaml")), f"{seed} lost it ({fix}: {bug})"
