# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""run_backend's verdict on a tool's exit status (#187)."""

import sys
from pathlib import Path

import pytest

from hdl_bag.flows.base import Finding, run_backend


class _Tool:
    """A tool that prints one warning and exits with CODE."""

    name = "faketool"
    executable = sys.executable

    def __init__(self, code: int) -> None:
        self.code = code

    def parse_version(self, output: str) -> str | None:
        return None

    def is_pinned(self, version: str | None) -> bool:
        return True

    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]:
        script = f"print('a.sv:1: warning: w'); raise SystemExit({self.code})"
        return [self.executable, "-c", script]

    def parse(self, output: str, root: Path) -> list[Finding]:
        return [Finding(self.name, "a.sv", 1, "W", "warning", line)
                for line in output.splitlines() if "warning" in line]  # fmt: skip


@pytest.mark.parametrize(("code", "failed"), [(0, False), (1, False), (2, True), (139, True)])
def test_a_tool_that_dies_after_printing_a_finding_has_failed(
    tmp_path: Path, code: int, failed: bool
) -> None:
    """The pinned lint tools exit 0 when clean and 1 when they report findings (measured on
    slang, Verilator and Verible). Any other status means the tool did not finish, so its
    findings are not a verdict, even when it printed some before dying."""
    findings = run_backend(_Tool(code), [], "t", tmp_path)  # type: ignore[arg-type]
    assert [f.rule for f in findings if f.rule == "TOOL-FAILED"] == (
        ["TOOL-FAILED"] if failed else []
    )
    assert any(f.rule == "W" for f in findings)
