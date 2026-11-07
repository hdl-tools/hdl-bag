# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Tool-abstraction layer: findings, reports and the backend protocol.

Callers only see ``Finding``, ``Report`` and the flow functions. A commercial backend
is one more class with the same ``name`` / ``command`` / ``parse`` shape.
"""

import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol

Severity = Literal["error", "warning"]


@dataclass(frozen=True, order=True)
class Finding:
    tool: str
    file: str  # posix path relative to the run directory, or "" when the tool gives none
    line: int
    rule: str
    severity: Severity
    message: str
    waived_by: str | None = field(default=None, compare=False)

    def waive(self, reason: str) -> "Finding":
        return replace(self, waived_by=reason)

    def format(self) -> str:
        where = f"{self.file}:{self.line}: " if self.file else ""
        tag = " (waived)" if self.waived_by else ""
        return f"{self.tool}: {where}{self.severity}: {self.message} [{self.rule}]{tag}"


def is_third_party(path: str) -> bool:
    """True for a file in a vendored third-party tree (any ``rtl/vendor/`` directory)."""
    return "/rtl/vendor/" in f"/{path}"


@dataclass(frozen=True)
class Report:
    flow: str
    findings: tuple[Finding, ...]
    tools: tuple[str, ...]
    # Warnings located in vendored third-party RTL: reported, never judged (P3-2).
    third_party: tuple[Finding, ...] = ()
    # What the reader should know that is not a finding (a tool that is not the pinned one).
    notes: tuple[str, ...] = ()

    @property
    def unwaived(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.waived_by is None)

    @property
    def ok(self) -> bool:
        return not self.unwaived

    def format(self) -> str:
        lines = [f.format() for f in self.findings]
        waived = len(self.findings) - len(self.unwaived)
        lines.append(
            f"{self.flow} ({', '.join(self.tools)}): {len(self.unwaived)} unwaived finding(s), "
            f"{waived} waived"
        )
        if self.third_party:
            lines.append(
                f"{len(self.third_party)} third-party warning(s) in rtl/vendor/ not judged"
            )
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines) + "\n"


class Versioned(Protocol):
    """A tool with a pinned version (tools.lock) that it reports with ``--version``."""

    name: str
    executable: str
    pinned: str  # the version the built-in waivers or flows were written for (tools.lock)

    def parse_version(self, output: str) -> str | None: ...

    def is_pinned(self, version: str | None) -> bool: ...


class Backend(Versioned, Protocol):
    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]: ...

    def parse(self, output: str, root: Path) -> list[Finding]: ...


def relative(path: str, root: Path) -> str:
    """A tool-reported path as posix text relative to the run directory."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(root.resolve())
        except ValueError:
            return p.as_posix()
    return p.as_posix()


def run_backend(
    backend: Backend, files: list[Path], top: str, root: Path, incdirs: list[Path] | None = None
) -> list[Finding]:
    """Run one tool in ROOT and return its findings. A missing tool is an error finding."""
    exe = shutil.which(backend.executable)
    if exe is None:
        return [
            Finding(backend.name, "", 0, "TOOL-MISSING", "error",
                    f"'{backend.executable}' not found on PATH")
        ]  # fmt: skip
    cmd = [exe, *backend.command(files, top, incdirs or [])[1:]]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
    output = proc.stdout + proc.stderr
    findings = backend.parse(output, root)
    if proc.returncode != 0 and not findings:
        tail = output.strip().splitlines()[-1:] or ["no output"]
        findings.append(
            Finding(backend.name, "", 0, "TOOL-FAILED", "error",
                    f"exit status {proc.returncode}: {tail[0]}")
        )  # fmt: skip
    return findings


def tool_version(backend: Versioned) -> str | None:
    """The version BACKEND's tool reports, or None if it is missing or does not say."""
    exe = shutil.which(backend.executable)
    if exe is None:
        return None
    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False)
    return backend.parse_version(proc.stdout + proc.stderr)
