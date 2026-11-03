# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Simulation flow: build with Verilator and run cocotb tests. Needs the ``sim`` extra."""

import os
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from hdl_bag.flows.base import Finding, Report
from hdl_bag.flows.compile import BUILD_FLAGS
from hdl_bag.gen import Outputs, include_dirs, sources

# Fixed so a failure reproduces; cocotb derives each test's seed from it and the name.
SEED = int(os.environ.get("BAG_COCOTB_SEED", "20261031"))
_TEST = re.compile(r"@cocotb\.test\([^)]*\)\s*\nasync def (\w+)")


class SimUnavailable(Exception):
    """cocotb is not installed."""


def cocotb_tests(module: Path, test_filter: str | None = None) -> list[str]:
    """The @cocotb.test names in MODULE that TEST_FILTER (cocotb's regex) selects."""
    text = module.read_text(encoding="utf-8")
    names = _TEST.findall(text)
    assert len(names) == text.count("@cocotb.test("), module
    return [n for n in names if not test_filter or re.search(test_filter, f"{module.stem}.{n}")]


def simulate(
    files: Sequence[Path],
    top: str,
    module: Path,
    build_dir: Path,
    *,
    params: dict[str, int] | None = None,
    env: dict[str, str] | None = None,
    incdirs: Sequence[Path] = (),
    test_filter: str | None = None,
    seed: int = SEED,
) -> Report:
    """Build FILES with TOP, run every @cocotb.test in MODULE that TEST_FILTER selects. Every
    test that fails, and any difference from the tests expected, is a finding."""
    expected = cocotb_tests(module, test_filter)
    if not expected:
        raise ValueError(f"{test_filter!r} selects no test in {module.name}")
    try:
        from cocotb_tools.runner import get_runner
    except ImportError as exc:
        raise SimUnavailable("simulation needs cocotb: install hdl-bag[sim]") from exc

    if shutil.which("ccache") is None:
        os.environ["OBJCACHE"] = ""  # Verilator's makefiles default to ccache
    runner = get_runner("verilator")
    runner.build(
        sources=list(files),
        includes=list(incdirs),
        hdl_toplevel=top,
        parameters=params or {},
        build_dir=build_dir,
        build_args=["-Wno-fatal", *BUILD_FLAGS],
        always=True,
    )
    path = os.pathsep.join([str(module.parent), os.environ.get("PYTHONPATH", "")])
    results = build_dir / "results.xml"
    results.unlink(missing_ok=True)
    try:
        runner.test(
            hdl_toplevel=top,
            test_module=module.stem,
            test_dir=module.parent,
            build_dir=build_dir,
            results_xml=str(results),
            test_filter=test_filter or None,
            extra_env={**(env or {}), "COCOTB_RANDOM_SEED": str(seed), "PYTHONPATH": path},
        )
    except SystemExit as exc:  # how the runner says a test failed; the results say which
        if not results.is_file():
            raise RuntimeError(f"{module.name}: the simulation wrote no results") from exc
    cases = list(ET.parse(results).iter("testcase"))
    findings = []
    if len(cases) != len(expected):
        message = f"ran {len(cases)} tests, expected {len(expected)} (seed {seed})"
        findings.append(Finding("cocotb", "", 0, "TEST-COUNT", "error", message))
    for case in cases:
        if case.find("failure") is not None or case.find("error") is not None:
            message = f"{case.get('name')} failed (seed {seed})"
            findings.append(Finding("cocotb", "", 0, "TEST-FAILED", "error", message))
    return Report("sim", tuple(findings), ("verilator", "cocotb"))


def run_tests(
    out: Outputs,
    *,
    test_filter: str | None = None,
    env: dict[str, str] | None = None,
    seed: int = SEED,
) -> Report:
    """Run the tests `bag generate` wrote into OUT. Raises ValueError if it wrote none."""
    if out.tests is None:
        raise ValueError(f"{out.root}: no generated tests")
    root = out.root.resolve()
    return simulate(
        [root / f for f in sources(out)], out.top, root / out.tests, root / "sim",
        env=env, incdirs=[root / d for d in include_dirs(out)],
        test_filter=test_filter, seed=seed,
    )  # fmt: skip
