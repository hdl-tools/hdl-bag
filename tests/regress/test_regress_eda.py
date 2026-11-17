# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""bag regress end to end on two small seeds (PR 5): the real lint, compile, generated tests
and formal. The nightly runs many more (PR 6); these pin that the path works."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from hdl_bag.cli import main

# 3: one APB4 decoder-mux; 17: an AXI4-Lite crossbar across two clock domains (CDC adapters).
SEEDS = (3, 17)


@pytest.mark.eda
@pytest.mark.parametrize("seed", SEEDS)
def test_a_seed_runs_the_whole_flow(tmp_path: Path, seed: int) -> None:
    got = CliRunner().invoke(main, ["regress", "--seed", str(seed), "-o", str(tmp_path),
                                    "--formal-budget", "600"])  # fmt: skip
    result = json.loads((tmp_path / str(seed) / "result.json").read_text(encoding="utf-8"))
    assert result["findings"] == [], got.output
    assert result["status"] in ("pass", "timeout"), got.output
    assert got.exit_code == 0, got.output
    steps = result["steps"]
    assert [s for s in steps if not s.startswith("formal ")] == [
        "check", "resolve", "generate", "lint", "compile", "sim"]  # fmt: skip
    assert any(s.endswith("_decode") and v == "pass" for s, v in steps.items())
