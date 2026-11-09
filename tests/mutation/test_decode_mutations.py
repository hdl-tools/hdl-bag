# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Phase 5 PR 3 (D69, D70): every Phase 4 package mutant also fails a formal property.

The mutants (mutations.py) each edit one table of one fabric in the generated parameter
package, or one in-house RTL file. Here a formal job of the config runs on the mutant and
must fail one of its assertions, whose reference comes from the IR, not from the package:
- the decode job (formal/<project>_decode.sv) for the decode tables and the decoder;
- the route jobs (formal/<project>_route_<unit>.sv, one per fabric or bridge) for the fixed
  priorities (D70), the bridges' address offsets (D69 part 3), and a sample of the decode
  table bugs as they show at a fabric's slave ports (D69 part 2): the mutated units' jobs.
A catch counts only with a counterexample to an assertion of the expected kind in the job's
harness, and nothing else but unreached covers, so a timeout, a tool error or a cover alone
never passes for a catch. The control is the same job, or every route job, on the unmutated
config, which must pass, covers included.
"""

import re
from pathlib import Path

import pytest
from mutations import MUTATIONS, RtlMutation
from test_mutations import AF, AP, AXR, CONFIGS, IDS, RTL_IDS, RTL_TARGETS, SF, TARGETS, XR

import hdl_bag
from hdl_bag.flows.base import Finding
from hdl_bag.flows.formal import run_sby
from hdl_bag.formal_gen.units import ROUTE_DEPTH, render_unit, route_depth, units
from hdl_bag.gen import generate
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.gen.render import camel
from hdl_bag.ir import Spec, load
from hdl_bag.tests_gen.route_model import window

RTL = Path(hdl_bag.__file__).parent / "rtl" / "inhouse"
# Which job catches what: the decode job every table bug but the priorities; the route job
# the priorities, the bridges' offsets, and (MISROUTE) a decode bug of each fabric kind.
DECODE_TARGETS = [t for t in TARGETS if t[0] != "priority_inversion"]
DECODE_IDS = [i for t, i in zip(TARGETS, IDS, strict=True) if t[0] != "priority_inversion"]
ROUTE_TARGETS = [t for t in TARGETS if t[0] == "priority_inversion"]
ROUTE_IDS = [i for t, i in zip(TARGETS, IDS, strict=True) if t[0] == "priority_inversion"]
F = False
MISROUTE = [("swapped_port_index", XR, "Xbar", F), ("swapped_port_index", AXR, "Xbar", F),
            ("swapped_port_index", SF, "Bus", F), ("swapped_port_index", AP, "Apb", F),
            ("decode_long", AF, "Ahb", F)]  # fmt: skip
# In MISROUTE's own order, so each case carries its own ID.
MISROUTE_IDS = [dict(zip(TARGETS, IDS, strict=True))[t] for t in MISROUTE]
DECODE_RTL = [t for t in RTL_TARGETS if t[1].file == "bag_addr_decode.sv"]
DECODE_RTL_IDS = [i for t, i in zip(RTL_TARGETS, RTL_IDS, strict=True) if t in DECODE_RTL]
# The bridge offset mutants, each on a config with a pass-through N bridge of its file. Phase 4
# put axi2apb_offset on bridges_pulp, whose br_per crosses clock domains: on the mutant its
# free-clock job reached step 18 of 24 in 20 minutes. mixed_narrow_apb's br_a8 and br_a16 are
# the same bridge with an offset, in one domain.
ROUTE_CONFIG = {"axi2apb_offset": "p3/mixed_narrow_apb"}
ROUTE_RTL = [(n, mut, ROUTE_CONFIG.get(n, c)) for n, mut, c in RTL_TARGETS if n.endswith("_offset")]
ROUTE_RTL_IDS = [f"{n}-{c.split('/')[1]}" for n, _, c in ROUTE_RTL]
# Route controls beyond the configs the mutants use: bridges_pulp has AXI4 and CDC bridges and
# an ID-less AXI4 master on its crossbar.
ROUTE_CONTROLS = ["p3/bridges_pulp"]
# RTL mutants no address or arbitration property can see: what a bridge drives besides the
# address (protection, strobes, responses). The Phase 4 simulation gate catches them.
OUT_OF_SCOPE = {"axi2apb_pprot", "axi2apb_pstrb", "axi2apb_pslverr", "axil2apb_pslverr",
                "axi2axil_bresp", "axi2axil_rresp"}  # fmt: skip
# D71: the wiring between a fabric's masters and its decoder, which only the master-side
# scoreboard sees: a region-level check takes any master and the address as it arrives. Each
# is caught by the scoreboard (_routed) or the delivery check (_delivered) of the unit.
SCOREBOARD_RTL = [
    ("ahb_alias_off", RtlMutation("bag_ahb_lite_bus.sv", (
        (".alias_en_i(alias_en_i),", ".alias_en_i('0),"),), "alias enables cut"), AF, "ahb"),
    ("ahb_secure_forced", RtlMutation("bag_ahb_lite_bus.sv", (
        ("assign m_hnonsec_o[s] = bus_hnonsec;", "assign m_hnonsec_o[s] = 1'b0;"),),
        "HNONSEC forced to secure towards the slaves"), AF, "ahb"),
    ("axi_master_index", RtlMutation("bag_axi_xbar.sv", (
        (".MstIdx    (m),", ".MstIdx    ((m + 1) % NoMasters),"),),
        "each master's guard decodes as the next master"), AXR, "xbar"),
]  # fmt: skip
SCOREBOARD_IDS = [f"{n}-{c.split('/')[1]}" for n, _, c, _ in SCOREBOARD_RTL]

# The decode assertions (formal_gen.decode) each table bug must fail: an AXI4 or AXI4-Lite
# master with no default slave is answered from MissResp, which only the miss-response
# assertion reads; an alias moved to the next block changes the forwarded address.
DECODES = ("_selects", "_errors")


def _decode_labels(mutation: str, config: str) -> tuple[str, ...]:
    if mutation == "wrong_miss_response" and config != AF:
        return ("_miss_response_",)
    if mutation == "broken_alias":
        return ("_aliases",)
    return DECODES


def _spec(config: str) -> Spec:
    return load(CONFIGS / f"{config}.ir.yaml")


def _failed(root: Path, spec: Spec, job: str, timeout: float = 1200) -> list[Finding]:
    """JOB's (decode, or route_<unit>) findings on the output at ROOT."""
    name = spec.project.name
    return list(run_sby(root / "formal" / f"{name}_{job}.sby", root, timeout=timeout).findings)


def _route_failed(root: Path, spec: Spec, unit_names: list[str]) -> list[Finding]:
    """The findings of the route jobs of UNIT_NAMES (each its own job), in order."""
    assert unit_names, "no route job to run"
    return [f for u in unit_names for f in _failed(root, spec, f"route_{u}")]


def _route_units(spec: Spec, *, source: str = "", prefix: str = "") -> list[str]:
    """SPEC's route units (fabrics that decode, and bridges): all, or the one fabric whose
    package prefix (as the mutants name it) is PREFIX, or the bridges that instantiate the
    module of rtl/inhouse/SOURCE with an offset to get wrong (their fabrics' windows
    differ). Of those, the ones in one clock domain when there are any: their jobs run on
    one clock, while on a mutant a free-clock job through a CDC can outlast the time limit."""
    found = units(build_netlist(spec))
    if prefix:
        found = [i for i in found if camel(i.name) == prefix]
        assert len(found) <= 1, (prefix, [i.name for i in found])
    if source:
        fabrics = {f.name: f for f in spec.fabrics}
        module = source.removesuffix(".sv")
        found = [i for i in found if i.bridge is not None
                 and re.search(rf"^\s*{module} #\(", render_unit(spec, i).text, re.M)
                 and window(spec, fabrics[i.bridge.upstream])
                 != window(spec, fabrics[i.bridge.downstream])]  # fmt: skip
        sync = [i for i in found if i.up is not None and i.down is not None
                and i.up.domain == i.down.domain]  # fmt: skip
        found = sync or found
    return [i.name for i in found]


_ASSERTION = re.compile(r": assertion (\S+) fails ")


def _caught(failed: list[Finding], spec: Spec, job: str, labels: tuple[str, ...]) -> None:
    """FAILED holds a counterexample to an assertion of a harness of JOB (decode, or route:
    any unit's) named for one of LABELS, and nothing but such counterexamples and unreached
    covers (a mutant may starve a cover too: reversed priorities never let the pair's race
    end the other way). A timeout or a tool error never passes for a catch, nor a cover
    alone."""
    harness = f"formal/{spec.project.name}_{job}"
    asserts = [f for f in failed if f.rule == "FORMAL-ASSERT"]
    assert all(f.file.startswith(harness) for f in asserts), failed
    assert all(f.rule in ("FORMAL-ASSERT", "FORMAL-COVER") for f in failed), failed
    names = [m[1] for f in asserts if (m := _ASSERTION.search(f.message))]
    assert any(label in n for n in names for label in labels), (labels, failed)


def test_every_package_mutation_fails_a_formal_property() -> None:
    """The pin (D70): each package mutation of the Phase 4 gate has a formal target here,
    each RTL mutation one or a reason it has none, and each route target a unit job."""
    assert {m for m, _, _, _ in DECODE_TARGETS} == set(MUTATIONS) - {"priority_inversion"}
    assert {m for m, _, _, _ in ROUTE_TARGETS} == {"priority_inversion"}
    assert set(MISROUTE) <= set(DECODE_TARGETS)
    for (mutation, config, _, last), case in zip(MISROUTE, MISROUTE_IDS, strict=True):
        assert case == f"{mutation}-{config.split('/')[1]}{'-last' if last else ''}"
    decode, route = {n for n, _, _ in DECODE_RTL}, {n for n, _, _ in ROUTE_RTL}
    assert decode == {"secure_polarity"}
    assert route == {"axi2apb_offset", "axi2axil_offset", "axil2apb_offset"}
    assert {n for n, _, _ in RTL_TARGETS} == decode | route | OUT_OF_SCOPE
    assert not (decode | route) & OUT_OF_SCOPE
    for _, config, fabric, _ in ROUTE_TARGETS + MISROUTE:
        assert _route_units(_spec(config), prefix=fabric), (config, fabric)
    for name, mut, config in ROUTE_RTL:
        assert _route_units(_spec(config), source=mut.file), (name, config)
    for name, mut, config, unit in SCOREBOARD_RTL:
        assert unit in _route_units(_spec(config)), (name, config)
        assert config in ROUTE_CONTROL_CONFIGS, name  # the unmutated job runs as the control
        mut.apply((RTL / mut.file).read_text(encoding="utf-8"))  # each edit applies once


@pytest.mark.eda
@pytest.mark.parametrize("config", sorted({t[1] for t in TARGETS} | {c for _, _, c in DECODE_RTL}))
def test_the_decode_job_holds_unmutated(tmp_path: Path, config: str) -> None:
    """The control: on each config the mutants use, the unmutated decode job passes."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    assert _failed(tmp_path, spec, "decode") == []


ROUTE_CONTROL_CONFIGS = sorted({t[1] for t in ROUTE_TARGETS + MISROUTE}
                               | {c for _, _, c in ROUTE_RTL} | set(ROUTE_CONTROLS))  # fmt: skip
# One case per route job, so the jobs spread over the workers and shards, and a failure names
# its unit (mixed_narrow_apb alone has eleven).
ROUTE_CONTROL_CASES = [(c, u) for c in ROUTE_CONTROL_CONFIGS for u in _route_units(_spec(c))]


def _deep(config: str, unit: str) -> bool:
    """A route job deeper than ROUTE_DEPTH (a bridge across domains that narrows the data more
    than once): the nightly runs it, with a longer time limit (D74)."""
    spec = _spec(config)
    inst = next(i for i in units(build_netlist(spec)) if i.name == unit)
    return route_depth(render_unit(spec, inst))[1] > ROUTE_DEPTH


DEEP_CONTROLS = {(c, u) for c, u in ROUTE_CONTROL_CASES if _deep(c, u)}
DEEP_TIMEOUT_S = 3600


def test_only_the_deepest_route_jobs_wait_for_the_nightly() -> None:
    """D74: mixed_narrow_apb's br_c8 (AXI4-Lite 64 to APB 8 bits, across domains) needs 36
    steps; its bmc reached step 27 in CI's 1200 s. Only such jobs carry the deep mark, which
    the PR and main full runs skip and the nightly runs."""
    assert {("p3/mixed_narrow_apb", "br_c8")} == DEEP_CONTROLS


@pytest.mark.eda
@pytest.mark.parametrize(("config", "unit"), [
    pytest.param(c, u, id=f"{c}-{u}", marks=[pytest.mark.deep] if (c, u) in DEEP_CONTROLS else [])
    for c, u in ROUTE_CONTROL_CASES])  # fmt: skip
def test_the_route_job_holds_unmutated(tmp_path: Path, config: str, unit: str) -> None:
    """The control: on each config the route mutants use, every unmutated route job passes,
    covers included. With the misroute targets this runs both crossbars' route jobs."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    timeout = DEEP_TIMEOUT_S if (config, unit) in DEEP_CONTROLS else 1200
    assert _failed(tmp_path, spec, f"route_{unit}", timeout) == []


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
    _caught(_failed(tmp_path, spec, "decode"), spec, "decode", _decode_labels(mutation, config))


@pytest.mark.eda
@pytest.mark.parametrize(("name", "mut", "config"), DECODE_RTL, ids=DECODE_RTL_IDS)
def test_the_decode_job_catches_the_rtl_mutation(tmp_path: Path, name: str, mut: RtlMutation,
                                                 config: str) -> None:  # fmt: skip
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_rtl(tmp_path, mut)
    _caught(_failed(tmp_path, spec, "decode"), spec, "decode", DECODES)


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config", "fabric", "last"), ROUTE_TARGETS, ids=ROUTE_IDS)
def test_the_route_job_catches_the_priority_inversion(tmp_path: Path, mutation: str, config: str,
                                                      fabric: str, last: bool) -> None:  # fmt: skip
    """D70: with the fixed priorities reversed, a lower-priority master goes first."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_package(tmp_path, spec, mutation, fabric, last)
    _caught(_route_failed(tmp_path, spec, _route_units(spec, prefix=fabric)), spec, "route",
            ("_before_",))  # fmt: skip


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config", "fabric", "last"), MISROUTE, ids=MISROUTE_IDS)
def test_the_route_job_catches_the_misroute(tmp_path: Path, mutation: str, config: str,
                                            fabric: str, last: bool) -> None:  # fmt: skip
    """D69 part 2: a decode table bug sends a request to a slave port the IR does not, which
    the fabric's slave-port assertions see (crossbar, shared bus with ID-prefixed masters,
    APB mux, AHB-Lite bus)."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_package(tmp_path, spec, mutation, fabric, last)
    _caught(_route_failed(tmp_path, spec, _route_units(spec, prefix=fabric)), spec, "route",
            ("_routed",))  # fmt: skip


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
    _caught(_route_failed(tmp_path, spec, bridges), spec, "route", ("_in_window",))


@pytest.mark.eda
@pytest.mark.parametrize(("name", "mut", "config", "unit"), SCOREBOARD_RTL, ids=SCOREBOARD_IDS)
def test_the_route_job_catches_the_master_wiring(tmp_path: Path, name: str, mut: RtlMutation,
                                                 config: str, unit: str) -> None:  # fmt: skip
    """D71: a fabric that decodes a request with the wrong master's tables, without the
    master's aliases, or passes it on with the wrong secure bit sends the slave a request
    its master never made, or answers one it should have passed on."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    _mutate_rtl(tmp_path, mut)
    _caught(_failed(tmp_path, spec, f"route_{unit}"), spec, "route", ("_routed", "_delivered"))
