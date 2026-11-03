# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Build and run one cocotb module on Verilator; shared by the eda simulation tests."""

from pathlib import Path

from hdl_bag.flows import simulate

REPO = Path(__file__).parents[2]


def run_cocotb(
    sources: list[Path],
    top: str,
    module: Path,
    build: str,
    params: dict[str, int] | None = None,
    env: dict[str, str] | None = None,
    incdirs: list[Path] | None = None,
) -> None:
    """Build SOURCES with TOP, run every @cocotb.test in MODULE and require all to pass."""
    report = simulate(sources, top, module, REPO / "out" / "sim" / build,
                      params=params, env=env, incdirs=incdirs or [])  # fmt: skip
    assert report.ok, report.format()
