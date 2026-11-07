# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Simulation flow: build with Verilator and run cocotb tests. Needs the ``sim`` extra."""

import os
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from hdl_bag.flows.base import Finding, Report
from hdl_bag.flows.compile import BUILD_FLAGS
from hdl_bag.gen import Outputs, include_dirs, sources
from hdl_bag.tests_gen import SHIM_TEST, ready_shim_module

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


def _findings(cases: list[ET.Element], expected: list[str], seed: int) -> list[Finding]:
    def finding(rule: str, message: str) -> Finding:
        return Finding("cocotb", "", 0, rule, "error", f"{message} (seed {seed})")

    names = [str(case.get("name")) for case in cases]
    out = []
    if sorted(names) != sorted(expected):
        out.append(finding("TEST-SET", f"ran {sorted(names)}, expected {sorted(expected)}"))
    for name, case in zip(names, cases, strict=True):
        if case.find("failure") is not None or case.find("error") is not None:
            out.append(finding("TEST-FAILED", f"{name} failed"))
        elif case.find("skipped") is not None:
            out.append(finding("TEST-SKIPPED", f"{name} was skipped"))
    return out


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
    build_dir = build_dir.resolve()
    try:
        expected = cocotb_tests(module, test_filter)
    except re.error as exc:
        raise ValueError(f"{test_filter!r} is not a valid regex: {exc}") from exc
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
    exit_code: int | str | None = 0
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
    except SystemExit as exc:  # a failed test, or the simulator itself: the results say which
        exit_code = exc.code
    if not results.is_file():
        raise RuntimeError(f"{module.name}: the simulation wrote no results (exit {exit_code})")
    findings = _findings(list(ET.parse(results).iter("testcase")), expected, seed)
    if exit_code and not findings:
        # The results are written before the simulator stops, so they cannot show a fault
        # in its teardown.
        message = f"the simulator exited {exit_code} after every test passed (seed {seed})"
        findings.append(Finding("cocotb", "", 0, "SIM-EXIT", "error", message))
    return Report("sim", tuple(findings), ("verilator", "cocotb"))


def run_tests(
    out: Outputs,
    *,
    test_filter: str | None = None,
    env: dict[str, str] | None = None,
    seed: int = SEED,
    ready_shim: bool = False,
) -> Report:
    """Run the tests `bag generate` wrote into OUT. Raises ValueError if it wrote none. With
    READY_SHIM, run the shim's test through the top that drives AXI READY from VALID (D38)."""
    if out.tests is None:
        raise ValueError(
            f"{out.root}: no generated tests (Generate cocotb unit tests is N, a user-supplied "
            "fabric, or generated by an older hdl-bag)"
        )
    if out.simulator != "verilator":
        raise ValueError(f"Project > Simulator is {out.simulator}; bag sim runs verilator only")
    root = out.root.resolve()
    files, top, build = [root / f for f in sources(out)], out.top, root / "sim"
    if ready_shim:
        if test_filter:
            raise ValueError("--ready-shim runs its own test, so it does not take a filter")
        top = ready_shim_module(out.top)
        shim = root / "tests" / f"{top}.sv"
        if not shim.is_file():
            raise ValueError(f"{out.root}: no READY shim (no AXI port, or no traffic to run)")
        files, build, test_filter = [*files, shim], root / "sim_ready_shim", SHIM_TEST
    hits = build / "coverage.json"
    hits.parent.mkdir(parents=True, exist_ok=True)
    hits.unlink(missing_ok=True)
    hits.with_suffix(".md").unlink(missing_ok=True)  # never leave an older run's report
    report = simulate(
        files, top, root / out.tests, build,
        env={**(env or {}), "BAG_COVERAGE_FILE": str(hits)},
        incdirs=[root / d for d in include_dirs(out)], test_filter=test_filter, seed=seed,
    )  # fmt: skip
    shard = (env or {}).get("BAG_ROUTE_MASTERS")
    return replace(report, notes=(*report.notes, *_coverage(root / out.tests, hits, shard)))


def _coverage(module: Path, hits: Path, shard: str | None = None) -> list[str]:
    """Write the coverage report next to HITS (D52) and return its summary lines; nothing if
    the run recorded none. SHARD ("i/k", BAG_ROUTE_MASTERS) limits it to those masters. The
    report never costs the run its result: a problem with it is a note."""
    from hdl_bag.ir import load
    from hdl_bag.tests_gen import coverage

    try:
        counts = coverage.load(hits) if hits.is_file() else None
        if not counts:
            return ["coverage: the tests run recorded none"]
        # tests/<project>_tests.py reads tests/<project>.ir.yaml
        spec = load(module.with_name(module.name.removesuffix("_tests.py") + ".ir.yaml"))
        masters = None
        if shard:
            i, k = (int(x) for x in shard.split("/"))
            masters = {m.name for m in spec.masters[i::k]}
        text, summary = coverage.report(spec, counts, masters)
        path = hits.with_suffix(".md")
        path.write_text(text, encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError) as exc:
        return [f"coverage report skipped: {exc}"]
    return [*summary, f"coverage report (report only, gates nothing): {path}"]
