# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Open-source lint backends: slang, Verilator and Verible.

Each backend builds its command line and parses the tool's text output into findings.
Parsing is pure, so it is tested on captured output without the tools installed.
"""

import re
from dataclasses import replace
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from hdl_bag.flows.base import Finding, Severity, is_third_party, relative

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
# continuation of an UNOPTFLAT warning:   path:line:col:      Example path: <node>
_VERILATOR_PATH: Final = re.compile(
    r"^\s+(?P<file>(?:[A-Za-z]:)?[^:\s][^:]*):\d+:\d+:\s+Example path:"
)
# path:line:col[-col]: message [Style: section] [rule-name]
_VERIBLE: Final = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\s][^:]*):(?P<line>\d+):\d+(?:-\d+)?: (?P<msg>.*?)"
    r"(?: \[(?P<rule>[\w-]+)\])?$"
)


def _version(pattern: str, output: str) -> str | None:
    m = re.search(pattern, output, re.M)
    return m[1] if m else None


def _same_version(pinned: str, version: str | None) -> bool:
    """VERSION is the PINNED one (tools.lock), ignoring a leading v and any finer detail the
    pin leaves out (slang v12.0 is 12.0.0)."""
    if version is None:
        return False
    pin, have = pinned.removeprefix("v"), version.removeprefix("v")
    return have == pin or have.startswith(f"{pin}.")


class Slang:
    name = "slang"
    executable = "slang"
    pinned = "v12.0"  # tools.lock SLANG_VERSION

    def parse_version(self, output: str) -> str | None:
        return _version(r"^slang version (\d+(?:\.\d+)*)", output)

    def is_pinned(self, version: str | None) -> bool:
        return _same_version(self.pinned, version)

    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]:
        return [
            self.executable, "--quiet", "-Weverything", *slang_flags(), "--top", top,
            *(f"-I{d.as_posix()}" for d in incdirs), *(f.as_posix() for f in files),
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
    pinned = "v5.052"  # tools.lock VERILATOR_VERSION

    def parse_version(self, output: str) -> str | None:
        return _version(r"^Verilator (\d+\.\d+)", output)

    def is_pinned(self, version: str | None) -> bool:
        return _same_version(self.pinned, version)

    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]:
        return [
            self.executable, "--lint-only", "-Wall", "--top-module", top,
            *(f"-I{d.as_posix()}" for d in incdirs), *(f.as_posix() for f in files),
        ]  # fmt: skip

    def parse(self, output: str, root: Path) -> list[Finding]:
        out: list[Finding] = []
        loop_files: list[list[str]] = []  # per finding: files on its UNOPTFLAT example path
        for line in output.splitlines():
            path = _VERILATOR_PATH.match(line)
            if path and out:
                loop_files[-1].append(relative(path["file"], root))
                continue
            m = _VERILATOR.match(line)
            if not m or m["msg"].startswith("Exiting due to"):
                continue
            # An elaboration-time $error / $fatal in the RTL is the design's own error, though
            # Verilator reports it as a warning: judge it as one, in every flow.
            user = m["rule"] in ("USERERROR", "USERFATAL")
            sev: Severity = "warning" if m["sev"] == "Warning" and not user else "error"
            file = relative(m["file"], root) if m["file"] else ""
            line_no = int(m["line"]) if m["line"] else 0
            out.append(Finding(self.name, file, line_no, m["rule"] or "error", sev, m["msg"]))
            loop_files.append([])
        return [_loop_owner(f, files) for f, files in zip(out, loop_files, strict=True)]


class Verible:
    name = "verible"
    executable = "verible-verilog-lint"
    pinned = "v0.0-4296-g0f262651"  # tools.lock VERIBLE_VERSION

    def parse_version(self, output: str) -> str | None:
        return _version(r"^Version\s+(\S+)", output)

    def is_pinned(self, version: str | None) -> bool:
        return _same_version(self.pinned, version)

    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]:
        # Verible lints files one at a time and needs no include directories.
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


def _loop_owner(f: Finding, path_files: list[str]) -> Finding:
    """An UNOPTFLAT (circular logic) warning is reported at one node of the loop, which can
    be a generated net even when the loop runs through vendored RTL (variable-granularity
    loops in PULP modules). Attribute such a warning to the first vendored file on its
    example path, so the lint scope (P3-2) treats it as third-party. A loop is attributed
    only when its other nodes are in vendored RTL or in the hand-written in-house wrappers
    (rtl/inhouse/: fixed, reviewed struct forwarding between flat ports and PULP structs,
    which Verilator sees as whole-struct nodes). A loop with any node in generated code,
    other than where it is reported, keeps its location and is judged: that is where the
    generator could wire a ready back into a valid."""
    if f.rule != "UNOPTFLAT" or is_third_party(f.file):
        return f
    vendor = next((p for p in path_files if is_third_party(p)), None)
    fixed = [p for p in path_files if is_third_party(p) or _in_house(p) or p == f.file]
    if vendor is None or len(fixed) != len(path_files):
        return f
    return replace(f, file=vendor, message=f"{f.message} (loop through {vendor}, seen at {f.file})")


def _in_house(path: str) -> bool:
    return "/rtl/inhouse/" in f"/{path}"


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
