# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The per-PR smoke subset of the EDA tests (Phase 4 gate: under 15 minutes per PR).

conftest.py marks every EDA test whose node id matches one of SMOKE with ``smoke``; in a
pattern ``*`` matches anything and every other character is literal (pytest's parameter ids
are in brackets, which fnmatch would read as character classes).

Pull requests run ``-m "eda and smoke"``. The full ``-m eda`` list runs on every push to main,
nightly, on demand, and on a pull request labelled ``full-eda`` (.github/workflows/pr.yml).
test_smoke.py checks that every pattern still matches a test.

What it keeps:
- every small EDA test (in-house benches, negative elaboration, flows, coverage, wrapper);
- the golden demo_ss whole: lint and compile in both port styles, and every part of its route
  simulation, which crosses the AXI crossbar, adapters, a CDC, all four bridge kinds, AHB-Lite
  and APB;
- lint and compile, and route simulation, of one config per fabric kind besides;
- one mutant of each gate bug, and the secure-check polarity in the RTL;
- one READY-shim run.
"""

import re
from typing import Final

_ROUTE = "tests/gen/test_corpus.py::test_route_simulation"
_LINT = "tests/gen/test_corpus.py::test_gate_lint_and_compile"
_MUT = "tests/mutation/test_mutations.py::test_generated_tests_detect_the_mutation"
_RTL_MUT = "tests/mutation/test_mutations.py::test_generated_tests_detect_the_rtl_mutation"
# One config per fabric kind besides demo_ss: AXI4 and AXI4-Lite crossbars, an AXI4 shared bus
# with fixed priority, AHB-Lite with fixed priority, APB, and several fabrics with CDC.
_CONFIGS = ("p3/axi_xbar_rules", "p3/axil_xbar_rules", "p3/axi_shared_fixed", "p2/ahb_fixed",
            "p2/apb4_rules", "p2/multi")  # fmt: skip

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
    *(f"{_LINT}[{c}-flat]" for c in _CONFIGS),
    *(f"{_ROUTE}[{c}]" for c in _CONFIGS),
    "tests/gen/test_corpus.py::test_ready_driven_from_valid_settles_and_carries_traffic[p3/axi_shared_rr]",
    # One mutant per gate bug, on configs whose suites are quick.
    f"{_MUT}[decode_short-ahb_fixed]",
    f"{_MUT}[swapped_port_index-apb4_rules]",
    f"{_MUT}[dropped_access_ro-ahb_rr]",
    f"{_MUT}[wrong_miss_response-ahb_fixed]",
    f"{_MUT}[secure_dropped-apb4_rules]",
    f"{_MUT}[broken_alias-ahb_fixed]",
    f"{_MUT}[priority_inversion-ahb_fixed]",
    f"{_RTL_MUT}[secure_polarity-ahb_fixed]",
)


def is_smoke(nodeid: str) -> bool:
    """Whether NODEID matches a SMOKE pattern."""
    return any(matches(nodeid, p) for p in SMOKE)


def matches(nodeid: str, pattern: str) -> bool:
    return (
        re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), nodeid) is not None
    )
