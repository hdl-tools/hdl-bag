# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Build and run one cocotb module on Verilator; shared by the eda simulation tests."""

import os
import re
import shutil
from pathlib import Path

from hdl_bag.flows.compile import BUILD_FLAGS

REPO = Path(__file__).parents[2]
RTL_TESTS = Path(__file__).parent

# Fixed so a failure reproduces; cocotb derives each test's seed from it and the name.
SEED = int(os.environ.get("BAG_COCOTB_SEED", "20261031"))
_TEST = re.compile(r"@cocotb\.test\([^)]*\)\s*\nasync def (\w+)")


def cocotb_tests(module: Path, test_filter: str | None = None) -> list[str]:
    """The @cocotb.test names in MODULE that TEST_FILTER (cocotb's regex) selects."""
    names = _TEST.findall(module.read_text())
    assert len(names) == module.read_text().count("@cocotb.test("), module
    return [n for n in names if test_filter is None or re.search(test_filter, f"{module.stem}.{n}")]


def run_cocotb(
    sources: list[Path],
    top: str,
    module: Path,
    build: str,
    params: dict[str, int] | None = None,
    env: dict[str, str] | None = None,
    incdirs: list[Path] | None = None,
    test_filter: str | None = None,
) -> None:
    """Build SOURCES with TOP, run every @cocotb.test in MODULE that TEST_FILTER selects and
    require all to pass."""
    expected = len(cocotb_tests(module, test_filter))
    assert expected > 0, f"{test_filter!r} selects no test in {module.name}"
    from cocotb_tools.check_results import get_results
    from cocotb_tools.runner import get_runner

    build_dir = REPO / "out" / "sim" / build
    if shutil.which("ccache") is None:
        os.environ["OBJCACHE"] = ""  # Verilator's makefiles default to ccache
    runner = get_runner("verilator")
    runner.build(
        sources=sources,
        includes=incdirs or [],
        hdl_toplevel=top,
        parameters=params or {},
        build_dir=build_dir,
        build_args=["-Wno-fatal", *BUILD_FLAGS],
        always=True,
    )
    path = os.pathsep.join([str(module.parent), str(RTL_TESTS), os.environ.get("PYTHONPATH", "")])
    results = runner.test(
        hdl_toplevel=top,
        test_module=module.stem,
        test_dir=module.parent,
        build_dir=build_dir,
        results_xml=str(build_dir / "results.xml"),
        test_filter=test_filter,
        extra_env={**(env or {}), "COCOTB_RANDOM_SEED": str(SEED), "PYTHONPATH": path},
    )
    total, failed = get_results(results)
    assert (total, failed) == (expected, 0), (
        f"{failed} of {total} cocotb tests failed (expected {expected} tests); seed {SEED}"
    )
