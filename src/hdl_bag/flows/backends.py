# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Open-source lint backends: slang, Verilator and Verible.

Each backend builds its command line and parses the tool's text output into findings.
Parsing is pure, so it is tested on captured output without the tools installed.
"""

import re
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from hdl_bag.flows.base import Finding, Severity, relative

# path:line:col: severity: message [-Wname]
_SLANG: Final = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\s][^:]*):(?P<line>\d+):\d+: (?P<sev>fatal error|error|warning): "
    r"(?P<msg>.*?)(?: \[-W(?P<rule>[\w-]+)\])?$"
)
# %Warning-CODE: path:line:col: message   /   %Error: path:line:col: message
_VERILATOR: Final = re.compile(
    r"^%(?P<sev>Warning|Error)(?:-(?P<rule>[A-Z0-9_]+))?: "
    r"(?:(?P<file>(?:[A-Za-z]:)?[^:\s][^:]*):(?P<line>\d+):(?:\d+:)? )?(?P<msg>.*)$"
)
# path:line:col[-col]: message [Style: section] [rule-name]
_VERIBLE: Final = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\s][^:]*):(?P<line>\d+):\d+(?:-\d+)?: (?P<msg>.*?)"
    r"(?: \[(?P<rule>[\w-]+)\])?$"
)


class Slang:
    name = "slang"
    executable = "slang"

    def command(self, files: list[Path], top: str) -> list[str]:
        return [
            self.executable, "--quiet", "-Weverything", *slang_flags(), "--top", top,
            *(f.as_posix() for f in files),
        ]  # fmt: skip

    def parse(self, output: str, root: Path) -> list[Finding]:
        out = []
        for line in output.splitlines():
            m = _SLANG.match(line)
            if m:
                sev: Severity = "warning" if m["sev"] == "warning" else "error"
                out.append(
                    Finding(self.name, relative(m["file"], root), int(m["line"]),
                            m["rule"] or "error", sev, m["msg"])
                )  # fmt: skip
        return out


class Verilator:
    name = "verilator"
    executable = "verilator"

    def command(self, files: list[Path], top: str) -> list[str]:
        return [
            self.executable, "--lint-only", "-Wall", "--top-module", top,
            *(f.as_posix() for f in files),
        ]  # fmt: skip

    def parse(self, output: str, root: Path) -> list[Finding]:
        out = []
        for line in output.splitlines():
            m = _VERILATOR.match(line)
            if not m or m["msg"].startswith("Exiting due to"):
                continue
            sev: Severity = "warning" if m["sev"] == "Warning" else "error"
            file = relative(m["file"], root) if m["file"] else ""
            line_no = int(m["line"]) if m["line"] else 0
            out.append(Finding(self.name, file, line_no, m["rule"] or "error", sev, m["msg"]))
        return out


class Verible:
    name = "verible"
    executable = "verible-verilog-lint"

    def command(self, files: list[Path], top: str) -> list[str]:
        with as_file(files_root() / "verible.rules") as rules:
            config = rules.as_posix()
        return [
            self.executable, f"--rules_config={config}", "--lint_fatal=false",
            "--parse_fatal=false", *(f.as_posix() for f in files),
        ]  # fmt: skip

    def parse(self, output: str, root: Path) -> list[Finding]:
        out = []
        for line in output.splitlines():
            m = _VERIBLE.match(line)
            if m:
                rule = m["rule"] or "syntax"
                sev: Severity = "error" if rule == "syntax" else "warning"
                out.append(
                    Finding(self.name, relative(m["file"], root), int(m["line"]), rule, sev,
                            m["msg"])
                )  # fmt: skip
        return out


def slang_flags() -> list[str]:
    """Options from the packaged config/slang.flags."""
    return parse_flags((files_root() / "slang.flags").read_text(encoding="utf-8"))


def parse_flags(text: str) -> list[str]:
    """One option per line, each with a ``# reason``; blank and comment lines are skipped."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        option, sep, reason = raw.partition("#")
        if not sep or not reason.strip():
            raise ValueError(f"slang.flags line {n}: '{option.strip()}' has no reason")
        out.append(option.strip())
    return out


def files_root() -> Traversable:
    """The packaged lint configuration directory (rule configs and waivers)."""
    return files("hdl_bag.flows") / "config"


BACKENDS: Final = {b.name: b for b in (Slang(), Verilator(), Verible())}
