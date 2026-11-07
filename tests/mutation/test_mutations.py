# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Phase 4 gate: every mutation in mutations.py is detected by the generated tests.

Each target generates a config, applies one mutation to one fabric's tables in the generated
parameter package, and runs the config's whole generated test suite (as `bag sim` does). The
suite must fail on a generated test, not on the build: a mutant that builds and passes is a
hole in the tests. Each gate bug runs on configs of more than one fabric kind where the
feature exists in more than one.

The unmutated configs pass the same suites in tests/gen/test_corpus.py (route simulation),
so a failure here is the mutation's doing.
"""

import re
from pathlib import Path

import pytest
from mutations import BRIDGE_MUTATIONS, MUTATIONS, MutationError, get
from simrun import REPO

from hdl_bag.flows.sim import run_tests
from hdl_bag.gen import generate
from hdl_bag.ir import Spec, load

CONFIGS = REPO / "tests" / "configs"

# (mutation, config, fabric prefix in the package)
TARGETS = [
    ("decode_short", "p3/axi_xbar_rules", "Xbar"),
    ("decode_short", "p2/ahb_fixed", "Ahb"),
    # APB masters address whole words: the byte variant is equivalent there (no legal access
    # has bit 0 set), so the word variant.
    ("decode_short_word", "p2/apb4_rules", "Apb"),
    ("decode_long", "p3/axi_xbar_rules", "Xbar"),
    ("swapped_port_index", "p3/axi_xbar_rules", "Xbar"),
    ("swapped_port_index", "p3/axi_shared_fixed", "Bus"),
    ("swapped_port_index", "p2/apb4_rules", "Apb"),
    ("dropped_access_rule", "p3/axi_xbar_rules", "Xbar"),
    ("dropped_access_rule", "p2/ahb_rr", "Ahb"),
    ("wrong_miss_response", "p3/axi_xbar_rules", "Xbar"),
    ("wrong_miss_response", "p2/ahb_fixed", "Ahb"),
    ("inverted_secure_check", "p3/axi_xbar_rules", "Xbar"),
    ("inverted_secure_check", "p2/ahb_fixed", "Ahb"),
    ("inverted_secure_check", "p2/apb4_rules", "Apb"),
    ("broken_alias", "p3/axi_xbar_rules", "Xbar"),
    ("broken_alias", "p2/ahb_fixed", "Ahb"),
    ("priority_inversion", "p3/axi_shared_fixed", "Bus"),
    ("priority_inversion", "p2/ahb_fixed", "Ahb"),
]
IDS = [f"{m}-{c.split('/')[1]}" for m, c, _ in TARGETS]
GATE = {"decode off-by-one", "swapped port index", "dropped access rule", "wrong miss response",
        "inverted secure check", "broken alias", "priority inversion"}  # fmt: skip


def _spec(config: str) -> Spec:
    return load(CONFIGS / f"{config}.ir.yaml")


def _package(root: Path, spec: Spec) -> Path:
    return root / "rtl" / "pkg" / f"{spec.wrapper.package}.sv"


def _params(text: str) -> dict[str, str]:
    """Every localparam of a package, name -> value text."""
    return dict(re.findall(r"localparam [^=]*?\b(\w+) = (.*?);", text, re.S))


def test_every_gate_bug_has_a_mutation_and_a_target() -> None:
    assert {m.bug for m in MUTATIONS.values()} == GATE
    assert {m for m, _, _ in TARGETS} == set(MUTATIONS)


@pytest.mark.parametrize(("mutation", "config", "fabric"), TARGETS, ids=IDS)
def test_mutation_edits_exactly_its_table(
    tmp_path: Path, mutation: str, config: str, fabric: str
) -> None:
    """Without EDA tools: the mutation applies to this config's package and changes one
    table of the named fabric, nothing else."""
    spec = _spec(config)
    generate(spec, tmp_path, (CONFIGS / config).parent)
    pkg = _package(tmp_path, spec).read_text(encoding="utf-8")
    mutant = MUTATIONS[mutation].apply(pkg, fabric)
    before, after = _params(pkg), _params(mutant)
    assert before.keys() == after.keys()
    changed = [k for k in before if before[k] != after[k]]
    assert len(changed) == 1 and changed[0].startswith(fabric), changed


def test_a_mutation_that_changes_nothing_is_refused() -> None:
    pkg = "localparam logic [2:0][31:0] XPriority = '{32'h0000_0000, 32'h0, 32'h0};\n"
    with pytest.raises(MutationError, match="no fixed priorities"):
        MUTATIONS["priority_inversion"].apply(pkg, "X")
    assert [v.value for v in get(pkg, "XPriority")] == [0, 0, 0]


def test_literals_round_trip() -> None:
    pkg = (
        "localparam logic [4:0] ARuleSecure = 5'h10;\n"
        "localparam logic [1:0][1:0] AMissResp = '{2'b11, 2'b10};\n"
    )
    assert MUTATIONS["inverted_secure_check"].apply(pkg, "A").splitlines()[0].endswith("= 5'h0F;")
    assert "'{2'b11, 2'b11}" in MUTATIONS["wrong_miss_response"].apply(pkg, "A")


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config", "fabric"), TARGETS, ids=IDS)
def test_generated_tests_detect_the_mutation(mutation: str, config: str, fabric: str) -> None:
    spec = _spec(config)
    root = REPO / "out" / "mutation" / f"{mutation}-{config.split('/')[1]}"
    out = generate(spec, root, (CONFIGS / config).parent)
    path = _package(root, spec)
    path.write_text(MUTATIONS[mutation].apply(path.read_text(encoding="utf-8"), fabric),
                    encoding="utf-8", newline="\n")  # fmt: skip
    report = run_tests(out)
    failed = [f for f in report.findings if f.rule == "TEST-FAILED"]
    assert failed, f"{mutation} survived on {config}:\n{report.format()}"
    assert {f.rule for f in report.findings} == {"TEST-FAILED"}, report.format()
    print(f"{mutation} on {config}: caught by {len(failed)} test(s): "
          + ", ".join(f.message.split()[0] for f in failed))  # fmt: skip


# ------------------------------------------------------------------- PULP-backed bridges

# (mutation, config): bridges_pulp has bag_axi2apb (pass-through N: br_per) and bag_axi2axil;
# mixed_narrow_apb has bag_axil2apb behind AXI4-Lite fabrics (pass-through N).
BRIDGE_TARGETS = [
    ("axi2apb_offset", "p3/bridges_pulp"),
    ("axi2apb_pslverr", "p3/bridges_pulp"),
    ("axil2apb_offset", "p3/mixed_narrow_apb"),
    ("axil2apb_pslverr", "p3/mixed_narrow_apb"),
    ("axi2axil_bresp", "p3/bridges_pulp"),
    ("axi2axil_rresp", "p3/bridges_pulp"),
]
BRIDGE_IDS = [f"{m}-{c.split('/')[1]}" for m, c in BRIDGE_TARGETS]


def test_every_bridge_mutation_has_a_target() -> None:
    assert {m for m, _ in BRIDGE_TARGETS} == set(BRIDGE_MUTATIONS)


@pytest.mark.parametrize(("mutation", "config"), BRIDGE_TARGETS, ids=BRIDGE_IDS)
def test_bridge_mutation_applies(tmp_path: Path, mutation: str, config: str) -> None:
    """Without EDA tools: the config uses the bridge, and each edit is found exactly once."""
    mut = BRIDGE_MUTATIONS[mutation]
    generate(_spec(config), tmp_path, (CONFIGS / config).parent)
    path = tmp_path / "rtl" / "inhouse" / mut.file
    assert mut.apply(path.read_text(encoding="utf-8")) != path.read_text(encoding="utf-8")


@pytest.mark.eda
@pytest.mark.parametrize(("mutation", "config"), BRIDGE_TARGETS, ids=BRIDGE_IDS)
def test_generated_tests_cover_the_pulp_bridges(mutation: str, config: str) -> None:
    mut = BRIDGE_MUTATIONS[mutation]
    root = REPO / "out" / "mutation" / f"{mutation}-{config.split('/')[1]}"
    out = generate(_spec(config), root, (CONFIGS / config).parent)
    path = root / "rtl" / "inhouse" / mut.file
    path.write_text(mut.apply(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    report = run_tests(out)
    failed = [f for f in report.findings if f.rule == "TEST-FAILED"]
    assert failed, f"{mutation} ({mut.bug}) survived on {config}:\n{report.format()}"
    assert {f.rule for f in report.findings} == {"TEST-FAILED"}, report.format()
    print(f"{mutation} on {config}: caught by {len(failed)} test(s): "
          + ", ".join(f.message.split()[0] for f in failed))  # fmt: skip
