# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The per-PR smoke subset of the EDA tests (Phase 4 gate: under 15 minutes per PR).

conftest.py marks every EDA test whose node id matches one of SMOKE with ``smoke``; in a
pattern ``*`` matches anything and every other character is literal (pytest's parameter ids
are in brackets, which fnmatch would read as character classes).

Pull requests run ``-m "eda and smoke"``. The full ``-m eda`` list runs on every push to main,
nightly, on demand, and on a pull request labelled ``full-eda`` (.github/workflows/).
test_smoke.py checks that every pattern still matches a test, that every mutant has its
unmutated control in the subset, and that the subset stays within its time budget.

What it keeps:
- every small EDA test (in-house benches, negative elaboration, flows, coverage, wrapper);
- the golden demo_ss whole: lint and compile in both port styles, and every part of its route
  simulation (the AXI crossbar, a CDC and width adapters, and four of the bridge templates,
  all in pass-through mode);
- lint and compile in both port styles, and route simulation, of one config per fabric kind;
- lint and compile of every Phase 2 config;
- route simulation of the bridge configs, which between them use every bridge template, the
  ID-width adapter, bridge offsets, a narrow APB and a CDC chain;
- one mutant of each gate bug on AHB-Lite or APB, four on AXI4 and AXI4-Lite, two in the PULP
  bridges' wrappers, and the secure-check polarity in the RTL;
- one READY-shim run.

What only the full list runs: the remaining Phase 3 configs and their sv-interface lint, the
other mutants (most of the AXI ones and every ``-last`` variant), and the remaining READY-shim
runs.
"""

import re
from typing import Final

_ROUTE = "tests/gen/test_corpus.py::test_route_simulation"
_LINT = "tests/gen/test_corpus.py::test_gate_lint_and_compile"
_MUT = "tests/mutation/test_mutations.py::test_generated_tests_detect_the_mutation"
_RTL_MUT = "tests/mutation/test_mutations.py::test_generated_tests_detect_the_rtl_mutation"
# One config per fabric kind besides demo_ss: AXI4 and AXI4-Lite crossbars, AXI4 and AXI4-Lite
# shared buses with fixed priority, AHB-Lite with fixed and round-robin priority, APB, and
# several fabrics with CDC. Each is also the control for the mutants below on it.
_CONFIGS = ("p3/axi_xbar_rules", "p3/axil_xbar_rules", "p3/axi_shared_fixed",
            "p3/axil_shared_fixed", "p2/ahb_fixed", "p2/ahb_rr", "p2/apb4_rules",
            "p2/multi")  # fmt: skip
# Route simulation only: the bridges beyond demo_ss's pass-through ones.
_BRIDGE_CONFIGS = ("p3/bridges_inhouse", "p3/bridges_pulp", "p3/mixed_adapters_bridges",
                   "p3/mixed_narrow_apb", "p3/mixed_cdc_chain")  # fmt: skip

SMOKE: Final[tuple[str, ...]] = (
    "tests/flows/*",
    "tests/rtl/*",
    "tests/gen/test_coverage.py::*",
    "tests/gen/test_collateral.py::*",
    "tests/gen/test_external.py::*",
    "tests/gen/test_tests_gen.py::*",
    "tests/gen/test_wrapper_sim.py::*",
    f"{_LINT}[golden/demo_ss-*]",
    f"{_ROUTE}[golden/demo_ss*]",
    f"{_LINT}[p2/*]",
    *(f"{_LINT}[{c}-*]" for c in _CONFIGS),
    *(f"{_ROUTE}[{c}]" for c in _CONFIGS + _BRIDGE_CONFIGS),
    "tests/gen/test_corpus.py::test_ready_driven_from_valid_settles_and_carries_traffic[p3/axi_shared_rr]",
    # One mutant per gate bug, on configs whose suites are quick.
    f"{_MUT}[decode_short-ahb_fixed]",
    f"{_MUT}[swapped_port_index-apb4_rules]",
    f"{_MUT}[dropped_access_ro-ahb_rr]",
    f"{_MUT}[wrong_miss_response-ahb_fixed]",
    f"{_MUT}[secure_dropped-apb4_rules]",
    f"{_MUT}[broken_alias-ahb_fixed]",
    f"{_MUT}[priority_inversion-ahb_fixed]",
    # The AXI4 and AXI4-Lite tables, which the AHB-Lite and APB mutants don't reach.
    f"{_MUT}[priority_inversion-axil_shared_fixed]",
    f"{_MUT}[decode_short_word-axil_xbar_rules]",
    f"{_MUT}[swapped_port_index-axil_xbar_rules]",
    f"{_MUT}[decode_long-axi_xbar_rules]",
    # RTL: the secure-check polarity, and offsets in the PULP bridges' wrappers.
    f"{_RTL_MUT}[secure_polarity-ahb_fixed]",
    f"{_RTL_MUT}[axi2axil_offset-mixed_cdc_chain]",
    f"{_RTL_MUT}[axil2apb_offset-mixed_narrow_apb]",
)


def is_smoke(nodeid: str) -> bool:
    """Whether NODEID matches a SMOKE pattern."""
    return any(matches(nodeid, p) for p in SMOKE)


def matches(nodeid: str, pattern: str) -> bool:
    return (
        re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), nodeid) is not None
    )
