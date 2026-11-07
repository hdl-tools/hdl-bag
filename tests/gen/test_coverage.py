# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Functional coverage report (D52): the bins, recording, and what `bag sim` writes."""

from collections import Counter
from pathlib import Path

import pytest
from simrun import REPO

from hdl_bag.gen import generate
from hdl_bag.ir import load
from hdl_bag.tests_gen import coverage, render_tests

XBAR_RULES = REPO / "tests" / "configs" / "p3" / "axi_xbar_rules.ir.yaml"


def test_space_of_a_config() -> None:
    """axi_xbar_rules: every master reaches what its decode allows, secure-only mem.sec is
    allowed secure and denied non-secure, and each AXI4 master with a writable word has all
    three burst types."""
    spec = load(XBAR_RULES)
    bins = coverage.space(spec)
    paths = {(m, s) for m, s, _ in bins["path"]}
    assert ("cpu", "rom") in paths and ("dma", "rom") not in paths  # Access_Rules dma -> rom none
    assert ("cpu", "regs", "write") not in bins["path"]  # Access_Rules cpu -> regs RO
    assert {
        ("cpu", "mem.sec", "secure", "allowed"),
        ("cpu", "mem.sec", "non-secure", "refused"),
    } <= (bins["secure"])
    # dbg reaches only the read-only rom: no word to burst-write and read back.
    assert {(m, b) for m, _, b, _ in bins["burst"]} == {
        (m, b) for m in ("cpu", "dma") for b in ("INCR", "FIXED", "WRAP")
    }
    assert {k for _, _, k in bins["response"]} >= {"OKAY", "slave error", "miss"}


def test_save_merges_and_report_marks_what_is_missing(tmp_path: Path) -> None:
    spec = load(XBAR_RULES)
    path = tmp_path / "coverage.json"
    coverage.hit("path", ("cpu", "rom", "read"))
    coverage.save(path)
    coverage.hit("path", ("cpu", "rom", "read"))
    coverage.hit("burst", ("cpu", "wrap", "WRAP", "passed"))
    coverage.save(path)
    hits = coverage.load(path)
    assert hits[("path", ("cpu", "rom", "read"))] == 2
    text, summary = coverage.report(spec, hits)
    bins = coverage.space(spec)
    assert f"coverage: path 1/{len(bins['path'])}" in summary
    assert f"coverage: burst 1/{len(bins['burst'])}" in summary
    assert "| cpu | rom | read | 2 |" in text
    assert "| cpu | mem.mem | write | **none** |" not in text  # bins are slave names, not regions
    assert "**none**" in text


def test_every_generated_test_saves_its_coverage_even_when_it_fails() -> None:
    text = render_tests(load(XBAR_RULES), None)
    calls = text.count("        await route_tests.")
    assert calls > 0 and text.count("    finally:\n        route_tests.save_coverage()") == calls


@pytest.mark.eda
def test_a_full_run_covers_every_bin_and_a_filtered_run_does_not(tmp_path: Path) -> None:
    """Every bin is something a generated test checks, so a full passing run covers them all;
    running one test leaves the burst bins uncovered."""
    from hdl_bag.flows.sim import run_tests

    spec = load(XBAR_RULES)
    out = generate(spec, tmp_path / "out", XBAR_RULES.parent)
    report = run_tests(out)
    assert report.ok, report.format()
    bins = coverage.space(spec)
    assert [n for n in report.notes if n.startswith("coverage: ")] == [
        f"coverage: {k} {len(bins[k])}/{len(bins[k])}" for k in coverage.KINDS if bins[k]
    ]
    assert "**none**" not in (out.root / "sim" / "coverage.md").read_text(encoding="utf-8")
    report = run_tests(out, test_filter="every_master_reaches_what_the_model_says")
    assert f"coverage: burst 0/{len(bins['burst'])}" in report.notes
    hits: Counter[tuple[str, coverage.Bin]] = coverage.load(out.root / "sim" / "coverage.json")
    assert all(hits[("path", b)] for b in bins["path"])


def _outputs_with_tests(tmp_path: Path) -> Path:
    out = generate(load(XBAR_RULES), tmp_path / "out", XBAR_RULES.parent)
    assert out.tests is not None
    return out.root / out.tests


def test_a_broken_hits_file_is_a_note_not_a_crash(tmp_path: Path) -> None:
    """The report never costs `bag sim` its result: a truncated file (a killed run) is a note."""
    from hdl_bag.flows.sim import _coverage

    module = _outputs_with_tests(tmp_path)
    hits = tmp_path / "coverage.json"
    hits.write_text('{"path": [["cpu", "rom"', encoding="utf-8")
    (note,) = _coverage(module, hits)
    assert note.startswith("coverage report skipped:")
    hits.write_text("{}", encoding="utf-8")
    assert _coverage(module, hits) == ["coverage: the tests run recorded none"]


def test_a_shard_is_measured_against_its_own_masters(tmp_path: Path) -> None:
    from hdl_bag.flows.sim import _coverage

    module = _outputs_with_tests(tmp_path)
    hits = tmp_path / "coverage.json"
    coverage.hit("path", ("cpu", "rom", "read"))
    coverage.save(hits)
    spec = load(XBAR_RULES)
    shard = {m.name for m in spec.masters[0::2]}
    assert shard == {"cpu", "dma"}  # masters cpu, dbg, dma: every second from the first
    whole = len(coverage.space(spec)["path"])
    part = len(coverage.space(spec, shard)["path"])
    assert part < whole
    assert f"coverage: path 1/{part}" in _coverage(module, hits, "0/2")
