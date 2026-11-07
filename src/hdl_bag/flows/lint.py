# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Lint flow: every listed tool must pass with zero unwaived findings.

Vendored third-party RTL (``rtl/vendor/``) is compiled so that its instantiations are
checked, but its warnings are not judged: they go to ``Report.third_party`` (P3-2).
Errors anywhere still fail. Verible, which only checks style, skips vendored files.
"""

from collections.abc import Sequence
from importlib.resources import as_file
from pathlib import Path

from hdl_bag.flows.backends import BACKENDS, files_root
from hdl_bag.flows.base import (
    Finding,
    Report,
    is_third_party,
    relative,
    run_backend,
    tool_version,
)
from hdl_bag.flows.waivers import Waiver, load_waivers, unused, waive

DEFAULT_TOOLS = ("slang", "verilator", "verible")


def builtin_waivers(tool: str) -> list[Waiver]:
    """Waivers shipped with hdl-bag for its own in-house RTL and generated code."""
    entry = files_root() / "waivers" / f"{tool}.yaml"
    if not entry.is_file():
        return []
    with as_file(entry) as path:
        return load_waivers(path)


def lint(
    files: Sequence[Path],
    top: str,
    root: Path,
    *,
    tools: Sequence[str] = DEFAULT_TOOLS,
    extra_waivers: Sequence[Path] = (),
    incdirs: Sequence[Path] = (),
) -> Report:
    """Lint FILES (in compile order, relative to ROOT or absolute) with TOP as the top.

    Waivers are the built-in ones plus EXTRA_WAIVERS files named ``<tool>.yaml``. A waiver
    that waives nothing is a WAIVER-UNUSED error only when its tool ran to the end (no error
    stopped it) and is the pinned version the waivers were written for; with another version
    it is a note.
    """
    unknown = sorted(set(tools) - BACKENDS.keys())
    if unknown:
        raise ValueError(f"unknown lint tool(s): {', '.join(unknown)}")
    own = [f for f in files if not is_third_party(relative(str(f), root))]
    linted = [relative(str(f), root) for f in own]
    findings: list[Finding] = []
    third_party: list[Finding] = []
    notes: list[str] = []
    for name in tools:
        waivers = builtin_waivers(name)
        for path in extra_waivers:
            if path.stem == name:
                waivers += load_waivers(path)
        tool_files = own if name == "verible" else list(files)
        raw = run_backend(BACKENDS[name], tool_files, top, root, list(incdirs))
        ours, theirs = split_third_party(raw)
        third_party += theirs
        marked, idle = waive(ours, waivers)
        findings += marked
        stale = unused(name, idle, linted)
        if not stale or any(f.severity == "error" for f in raw):
            continue  # a tool that did not run, or stopped early, proves no waiver stale
        backend = BACKENDS[name]
        version = tool_version(backend)
        if backend.is_pinned(version):
            findings += stale
            continue
        which = ", ".join(f.message.removeprefix("waiver for ").removesuffix(" matched nothing")
                          for f in stale)  # fmt: skip
        notes.append(
            f"{name} {version or '(version unknown)'} is not the pinned {backend.pinned}: "
            f"{len(stale)} waiver(s) written for {backend.pinned} matched nothing ({which})"
        )
    return Report("lint", tuple(sorted(findings)), tuple(tools), tuple(sorted(third_party)),
                  tuple(notes))  # fmt: skip


def split_third_party(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """(findings to judge, warnings located in vendored RTL). Errors are always judged."""
    theirs = [f for f in findings if f.severity == "warning" and is_third_party(f.file)]
    return [f for f in findings if f not in theirs], theirs
