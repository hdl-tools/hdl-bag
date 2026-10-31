# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Compile flow: elaborate the design and build it with Verilator (the v1 simulator).

Lint findings are the lint flow's job, so warnings are off here; only errors count.
"""

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from hdl_bag.flows.backends import Verilator
from hdl_bag.flows.base import Finding, Report, run_backend


class VerilatorBuild(Verilator):
    name = "verilator-build"

    def __init__(self, obj_dir: str) -> None:
        self.obj_dir = obj_dir

    def command(self, files: list[Path], top: str) -> list[str]:
        return [
            self.executable, "--cc", "--build", "-Wno-fatal", "-Wno-lint", "-Wno-style",
            "--top-module", top, "-Mdir", self.obj_dir, *(f.as_posix() for f in files),
        ]  # fmt: skip

    def parse(self, output: str, root: Path) -> list[Finding]:
        return [f for f in super().parse(output, root) if f.severity == "error"]


def compile_design(files: Sequence[Path], top: str, root: Path) -> Report:
    """Elaborate and build FILES with TOP; the object directory is ROOT/obj/<top>."""
    obj = root / "obj" / top
    shutil.rmtree(obj, ignore_errors=True)
    obj.mkdir(parents=True)  # Verilator creates only the last level of -Mdir
    if shutil.which("ccache") is None:
        os.environ.setdefault("OBJCACHE", "")  # Verilator's makefiles default to ccache
    backend = VerilatorBuild((Path("obj") / top).as_posix())
    findings = run_backend(backend, list(files), top, root)
    return Report("compile", tuple(sorted(findings)), (backend.name,))
