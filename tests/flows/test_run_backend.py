# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""run_backend's verdict on a tool's exit status (#187)."""

import sys
from pathlib import Path

import pytest

from hdl_bag.flows import lint
from hdl_bag.flows.backends import BACKENDS
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
    """A finished run of the pinned tools exits 0 or 1 (measured with slang, Verilator and
    Verible on clean code, warnings, errors and a syntax error). Any other status means the
    tool did not finish, so its findings are not a verdict, even when it printed some."""
    findings = run_backend(_Tool(code), [], "t", tmp_path)  # type: ignore[arg-type]
    assert [f.rule for f in findings if f.rule == "TOOL-FAILED"] == (
        ["TOOL-FAILED"] if failed else []
    )
    assert any(f.rule == "W" for f in findings)


class _VendorTool(_Tool):
    """A slang stand-in that prints one warning in vendored RTL, which lint does not judge."""

    name = "slang"

    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]:
        script = f"print('rtl/vendor/pulp/x.sv:1: warning: w'); raise SystemExit({self.code})"
        return [self.executable, "-c", script]

    def parse(self, output: str, root: Path) -> list[Finding]:
        return [Finding(self.name, "rtl/vendor/pulp/x.sv", 1, "W", "warning", line)
                for line in output.splitlines() if "warning" in line]  # fmt: skip


def test_a_lint_tool_that_dies_after_a_vendored_warning_fails_the_lint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#187's own case: the only finding is one lint does not judge, and the tool died."""
    monkeypatch.setitem(BACKENDS, "slang", _VendorTool(139))
    report = lint([], "t", tmp_path, tools=["slang"])
    assert [f.rule for f in report.unwaived] == ["TOOL-FAILED"]
