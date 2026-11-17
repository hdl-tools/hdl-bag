# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""bag regress end to end on two small seeds (PR 5): the real lint, compile, generated tests
and formal. The nightly runs many more (PR 6); these pin that the path works."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main

# What each seed draws, pinned so a generator change that moves them shows here.
SEEDS = {
    0: {"topologies": ["AXI4-Lite crossbar"], "bridges": [], "port_style": "flat"},
    4: {"topologies": ["AHB-Lite single-layer", "APB4 decoder-mux"],
        "bridges": ["bag_ahb2apb"], "port_style": "sv-interface"},
}  # fmt: skip


@pytest.mark.eda
@pytest.mark.parametrize("seed", sorted(SEEDS))
def test_a_seed_runs_the_whole_flow(tmp_path: Path, seed: int) -> None:
    got = CliRunner().invoke(main, ["regress", "--seed", str(seed), "-o", str(tmp_path),
                                    "--formal-budget", "600"])  # fmt: skip
    result = json.loads((tmp_path / str(seed) / "result.json").read_text(encoding="utf-8"))
    assert {k: result["dimensions"][k] for k in SEEDS[seed]} == SEEDS[seed]
    assert result["findings"] == [], "\n".join(result["findings"])
    assert result["status"] in ("pass", "timeout"), got.output
    assert got.exit_code == 0, got.output
    steps = result["steps"]
    assert [s for s in steps if not s.startswith("formal ")] == [
        "draw", "check", "resolve", "generate", "lint", "compile", "sim"]  # fmt: skip
    assert any(s.endswith("_decode:prove") and v == "pass" for s, v in steps.items())
