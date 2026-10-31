# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Lint flow: every listed tool must pass with zero unwaived findings."""

from collections.abc import Sequence
from importlib.resources import as_file
from pathlib import Path

from hdl_bag.flows.backends import BACKENDS, files_root
from hdl_bag.flows.base import Report, relative, run_backend
from hdl_bag.flows.waivers import Waiver, apply_waivers, load_waivers

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
) -> Report:
    """Lint FILES (in compile order, relative to ROOT or absolute) with TOP as the top.

    Waivers are the built-in ones plus EXTRA_WAIVERS files named ``<tool>.yaml``.
    """
    unknown = sorted(set(tools) - BACKENDS.keys())
    if unknown:
        raise ValueError(f"unknown lint tool(s): {', '.join(unknown)}")
    linted = [relative(str(f), root) for f in files]
    findings = []
    for name in tools:
        waivers = builtin_waivers(name)
        for path in extra_waivers:
            if path.stem == name:
                waivers += load_waivers(path)
        raw = run_backend(BACKENDS[name], list(files), top, root)
        findings += apply_waivers(name, raw, waivers, linted)
    return Report("lint", tuple(sorted(findings)), tuple(tools))
