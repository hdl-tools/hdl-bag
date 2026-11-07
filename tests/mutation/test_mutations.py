# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Phase 4 gate: every mutation in mutations.py is detected by the generated tests.

Each target generates a config, applies one mutation (to one fabric's tables in the generated
parameter package, or to one RTL file copied into the output), and runs the config's whole
generated test suite, as `bag sim` does. Detection (``_detected``) means:
- at least one generated test fails;
- every failing test fails on an AssertionError (cocotb names the exception class in
  results.xml): not a timeout (a hang is not detection), not another exception such as an
  IndexError in a helper, and not raised through _bring_up (a mutant that breaks bring-up
  proves nothing). The assertion may come from the scoreboard or from a protocol check in a
  bus model;
- nothing else goes wrong (a build failure, a missing test, a simulator exit).

Each gate bug runs on several fabric kinds (AXI4, AXI4-Lite, AHB-Lite, APB) and on the first
and the last entry of a table where the index could matter. The control is
tests/gen/test_corpus.py's route simulation, which runs the same unmutated suites and must
pass; test_every_config_has_an_unmutated_control checks that each config here is there.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from mutations import BRIDGE_MUTATIONS, MUTATIONS, SECURE_POLARITY, MutationError, RtlMutation, get
from simrun import REPO

from hdl_bag.flows.sim import run_tests
from hdl_bag.gen import Outputs, generate
from hdl_bag.ir import Spec, load

CONFIGS = REPO / "tests" / "configs"
XR, AXR, AF, AR, AP = ("p3/axi_xbar_rules", "p3/axil_xbar_rules", "p2/ahb_fixed", "p2/ahb_rr",
                       "p2/apb4_rules")  # fmt: skip
SF, ASF = "p3/axi_shared_fixed", "p3/axil_shared_fixed"
F, L = False, True  # the first entry the mutation can edit, or the last

# (mutation, config, fabric prefix in the package, last)
TARGETS = [
    ("decode_short", XR, "Xbar", F), ("decode_short", XR, "Xbar", L),
    ("decode_short", AF, "Ahb", F), ("decode_short", AF, "Ahb", L),
    # APB and AXI4-Lite masters address whole words: the byte variant cannot show there.
    ("decode_short_word", AP, "Apb", F), ("decode_short_word", AP, "Apb", L),
    ("decode_short_word", AXR, "Xbar", F),
    ("decode_long", XR, "Xbar", F), ("decode_long", XR, "Xbar", L), ("decode_long", AF, "Ahb", F),
    ("decode_long", AP, "Apb", F), ("decode_long", AXR, "Xbar", F),
    ("swapped_port_index", XR, "Xbar", F), ("swapped_port_index", XR, "Xbar", L),
    ("swapped_port_index", SF, "Bus", F), ("swapped_port_index", AP, "Apb", F),
    ("swapped_port_index", AXR, "Xbar", F),
    ("dropped_access_none", XR, "Xbar", F), ("dropped_access_none", AR, "Ahb", F),
    ("dropped_access_none", AXR, "Xbar", F),
    ("dropped_access_ro", XR, "Xbar", F), ("dropped_access_ro", AR, "Ahb", F),
    ("dropped_region_ro", XR, "Xbar", F), ("dropped_region_ro", AP, "Apb", F),
    ("dropped_region_ro", AP, "Apb", L), ("dropped_region_ro", SF, "Bus", F),
    ("wrong_miss_response", XR, "Xbar", F), ("wrong_miss_response", XR, "Xbar", L),
    ("wrong_miss_response", AF, "Ahb", F), ("wrong_miss_response", AXR, "Xbar", F),
    ("secure_dropped", XR, "Xbar", F), ("secure_dropped", AF, "Ahb", F),
    ("secure_dropped", AP, "Apb", F),
    ("secure_spurious", XR, "Xbar", F), ("secure_spurious", XR, "Xbar", L),
    ("secure_spurious", AF, "Ahb", F), ("secure_spurious", AP, "Apb", F),
    ("broken_alias", XR, "Xbar", F), ("broken_alias", AF, "Ahb", F), ("broken_alias", AF, "Ahb", L),
    ("priority_inversion", SF, "Bus", F), ("priority_inversion", AF, "Ahb", F),
    ("priority_inversion", ASF, "Bus", F),
]  # fmt: skip
IDS = [f"{m}-{c.split('/')[1]}{'-last' if last else ''}" for m, c, _, last in TARGETS]
# Where the catching test is known, it must be among the failures.
CATCHER = {"decode_short": "single_bytes_reach_both_ends_of_every_region"}
GATE = {"decode off-by-one", "swapped port index", "dropped access rule", "wrong miss response",
        "inverted secure check", "broken alias", "priority inversion"}  # fmt: skip

# (RTL mutation, config). bridges_pulp has bag_axi2apb (pass-through N: br_per) and bag_axi2axil
# (pass-through Y); mixed_cdc_chain a pass-through N bag_axi2axil with a 32-bit downstream
# (br_lite; bridges_pulp_cdc's br_aon would be equivalent: its window base lies above its 24-bit
# downstream address, so subtracting it changes nothing); mixed_narrow_apb bag_axil2apb behind
# AXI4-Lite fabrics (pass-through N).
RTL_TARGETS: list[tuple[str, RtlMutation, str]] = [
    *((name, mut, "p3/bridges_pulp") for name, mut in BRIDGE_MUTATIONS.items()
      if name.startswith(("axi2apb", "axi2axil_bresp", "axi2axil_rresp"))),
    ("axi2axil_offset", BRIDGE_MUTATIONS["axi2axil_offset"], "p3/mixed_cdc_chain"),
    ("axil2apb_offset", BRIDGE_MUTATIONS["axil2apb_offset"], "p3/mixed_narrow_apb"),
    ("axil2apb_pslverr", BRIDGE_MUTATIONS["axil2apb_pslverr"], "p3/mixed_narrow_apb"),
    # The inverted secure check as the RTL bug it names, on every fabric kind with secure regions.
    ("secure_polarity", SECURE_POLARITY, XR),
    ("secure_polarity", SECURE_POLARITY, AF),
    ("secure_polarity", SECURE_POLARITY, AP),
]  # fmt: skip
RTL_IDS = [f"{n}-{c.split('/')[1]}" for n, _, c in RTL_TARGETS]


def _spec(config: str) -> Spec:
    return load(CONFIGS / f"{config}.ir.yaml")


def _package(root: Path, spec: Spec) -> Path:
    return root / "rtl" / "pkg" / f"{spec.wrapper.package}.sv"


def _params(text: str) -> dict[str, str]:
    """Every localparam of a package, name -> value text."""
    return dict(re.findall(r"localparam [^=]*?\b(\w+) = (.*?);", text, re.S))


def test_every_gate_bug_has_a_mutation_and_a_target() -> None:
    assert {m.bug for m in MUTATIONS.values()} == GATE
    assert {m for m, _, _, _ in TARGETS} == set(MUTATIONS)
    assert {n for n, _, _ in RTL_TARGETS} >= set(BRIDGE_MUTATIONS)


def test_every_config_has_an_unmutated_control() -> None:
    """The unmutated suites run in test_corpus.py's route simulation (the control)."""
    import sys

    sys.path.insert(0, str(REPO / "tests" / "gen"))
    from test_corpus import ROUTED

    configs = {c for _, c, _, _ in TARGETS} | {c for _, _, c in RTL_TARGETS}
    assert configs <= set(ROUTED)


@pytest.mark.parametrize(("mutation", "config", "fabric", "last"), TARGETS, ids=IDS)
def test_mutation_edits_exactly_its_table(
    tmp_path: Path, mutation: str, config: str, fabric: str, last: bool
) -> None:
    """Without EDA tools: the mutation applies to this config's package and changes one table
    of the named fabric, nothing else; the first and last variants edit different entries."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    pkg = _package(tmp_path, spec).read_text(encoding="utf-8")
    mutant = MUTATIONS[mutation].apply(pkg, fabric, last)
    before, after = _params(pkg), _params(mutant)
    assert before.keys() == after.keys()
    changed = [k for k in before if before[k] != after[k]]
    assert len(changed) == 1 and changed[0].startswith(fabric), changed
    if last and mutation != "priority_inversion":
        assert mutant != MUTATIONS[mutation].apply(pkg, fabric, False), "first and last agree"


@pytest.mark.parametrize(("name", "mut", "config"), RTL_TARGETS, ids=RTL_IDS)
def test_rtl_mutation_applies(tmp_path: Path, name: str, mut: RtlMutation, config: str) -> None:
    """Without EDA tools: the config uses the file, and each edit is found exactly once."""
    generate(_spec(config), tmp_path, (CONFIGS / config).parent)
    path = tmp_path / "rtl" / "inhouse" / mut.file
    assert mut.apply(path.read_text(encoding="utf-8")) != path.read_text(encoding="utf-8")


def test_a_mutation_that_changes_nothing_is_refused() -> None:
    pkg = "localparam logic [2:0][31:0] XPriority = '{32'h0000_0000, 32'h0, 32'h0};\n"
    with pytest.raises(MutationError, match="no fixed priorities"):
        MUTATIONS["priority_inversion"].apply(pkg, "X")
    assert [v.value for v in get(pkg, "XPriority")] == [0, 0, 0]


def test_literals_round_trip() -> None:
    pkg = (
        "localparam logic [4:0] ARuleSecure = 5'h10;\n"
        "localparam logic [1:0][1:0] AMissResp = '{2'b11, 2'b10};\n"
        "localparam logic [1:0] AMissToDefault = 2'b00;\n"
    )
    assert "ARuleSecure = 5'h00;" in MUTATIONS["secure_dropped"].apply(pkg, "A")
    assert "ARuleSecure = 5'h11;" in MUTATIONS["secure_spurious"].apply(pkg, "A")
    assert "ARuleSecure = 5'h18;" in MUTATIONS["secure_spurious"].apply(pkg, "A", True)
    assert "'{2'b11, 2'b11}" in MUTATIONS["wrong_miss_response"].apply(pkg, "A")
    assert "'{2'b10, 2'b10}" in MUTATIONS["wrong_miss_response"].apply(pkg, "A", True)


def _results(*failures: tuple[str, str, str]) -> ET.Element:
    """A results.xml as cocotb 2.1 writes it: one testcase per (name, exception type, traceback)."""
    suite = ET.Element("testsuite")
    for name, kind, text in failures:
        case = ET.SubElement(suite, "testcase", name=name)
        ET.SubElement(case, "failure", message="m", type=kind).text = text
    return suite


def test_only_an_assertion_outside_bring_up_counts_as_detection() -> None:
    """cocotb writes <failure> for every exception, so the exception type has to be read: a
    mutant that only crashes a helper (IndexError, KeyError) proves nothing about the tests."""
    ok = ("t", "AssertionError", 'File "route_tests.py", line 9, in body\nAssertionError')
    assert _not_detection(_results(ok)) == []
    for bad in [
        ("t", "KeyError", 'File "route_model.py", line 3, in expect\nKeyError: 4'),
        ("t", "IndexError", "IndexError: list index out of range"),
        ("t", "SimTimeoutError", "cocotb.triggers.SimTimeoutError: Timeout"),
        ("t", "AssertionError", 'File "route_tests.py", line 5, in _bring_up\nAssertionError'),
    ]:
        assert _not_detection(_results(ok, bad)) == [f"t: {bad[1]}"], bad


def _not_detection(results: ET.Element) -> list[str]:
    """The failures in a cocotb results.xml that don't count as detection, as "test: type".
    cocotb writes <failure> for any exception and names its class in ``type``, so only an
    AssertionError counts, and not one raised through _bring_up or carrying a timeout."""
    wrong = []
    for c in results.iter("testcase"):
        for e in [*c.iter("failure"), *c.iter("error")]:
            text = f"{e.get('message') or ''}\n{e.text or ''}"
            kind = e.get("type") or e.tag
            if kind != "AssertionError" or "Timeout" in text or " in _bring_up" in text:
                wrong.append(f"{c.get('name')}: {kind}")
    return wrong


def _detected(out: Outputs, what: str, catcher: str | None = None) -> list[str]:
    """Run OUT's generated suite; assert detection as the module docstring defines it. Returns
    the failing tests."""
    report = run_tests(out)
    failed = [f.message.split()[0] for f in report.findings if f.rule == "TEST-FAILED"]
    assert failed, f"{what} survived:\n{report.format()}"
    assert {f.rule for f in report.findings} == {"TEST-FAILED"}, report.format()
    wrong = _not_detection(ET.parse(out.root / "sim" / "results.xml").getroot())
    assert wrong == [], f"{what}: not an assertion in a test body: {wrong}\n{report.format()}"
    assert catcher is None or catcher in failed, f"{what}: {catcher} did not fail: {failed}"
    return failed


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config", "fabric", "last"), TARGETS, ids=IDS)
def test_generated_tests_detect_the_mutation(mutation: str, config: str, fabric: str,
                                             last: bool) -> None:  # fmt: skip
    spec = _spec(config)
    root = REPO / "out" / "mutation" / f"{mutation}-{config.split('/')[1]}{'-last' * last}"
    out = generate(spec, root, (CONFIGS / config).parent)
    path = _package(root, spec)
    path.write_text(MUTATIONS[mutation].apply(path.read_text(encoding="utf-8"), fabric, last),
                    encoding="utf-8", newline="\n")  # fmt: skip
    failed = _detected(out, f"{mutation} on {config}", CATCHER.get(mutation))
    print(f"{mutation} on {config}: caught by {len(failed)} test(s): {', '.join(failed)}")


@pytest.mark.eda
@pytest.mark.parametrize(("name", "mut", "config"), RTL_TARGETS, ids=RTL_IDS)
def test_generated_tests_detect_the_rtl_mutation(name: str, mut: RtlMutation, config: str) -> None:
    root = REPO / "out" / "mutation" / f"{name}-{config.split('/')[1]}"
    out = generate(_spec(config), root, (CONFIGS / config).parent)
    path = root / "rtl" / "inhouse" / mut.file
    path.write_text(mut.apply(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    failed = _detected(out, f"{name} ({mut.bug}) on {config}")
    print(f"{name} on {config}: caught by {len(failed)} test(s): {', '.join(failed)}")
