# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Compile flow: elaborate the design and build it with Verilator (the v1 simulator).

Lint findings are the lint flow's job, so warnings are off here; only errors count.
``build=False`` stops after elaboration and C++ generation: it catches every elaboration
error in seconds, while the C++ build (about 95% of the time) only matters for a design
that is then simulated.
"""

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from hdl_bag.flows.backends import Verilator
from hdl_bag.flows.base import Finding, Report, run_backend

# Verilator 5.052's DFG optimiser runs out of memory (over 14 GB) on PULP's axi_dw_downsizer,
# whose variable-granularity combinational loops it cannot break; without it the same design
# elaborates in seconds. Simulation behaviour is unchanged.
BUILD_FLAGS = ("-fno-dfg",)


class VerilatorBuild(Verilator):
    name = "verilator-build"

    def __init__(self, obj_dir: str, build: bool = True) -> None:
        self.obj_dir = obj_dir
        self.build = build
        if not build:
            self.name = "verilator-elaborate"

    def command(self, files: list[Path], top: str, incdirs: list[Path]) -> list[str]:
        return [
            self.executable, "--cc", *(["--build"] if self.build else []),
            "-Wno-fatal", "-Wno-lint", "-Wno-style", *BUILD_FLAGS,
            "--top-module", top, "-Mdir", self.obj_dir,
            *(f"-I{d.as_posix()}" for d in incdirs), *(f.as_posix() for f in files),
        ]  # fmt: skip

    def parse(self, output: str, root: Path) -> list[Finding]:
        return [f for f in super().parse(output, root) if f.severity == "error"]


def compile_design(
    files: Sequence[Path],
    top: str,
    root: Path,
    *,
    incdirs: Sequence[Path] = (),
    build: bool = True,
) -> Report:
    """Elaborate FILES with TOP and, with BUILD, compile the C++ model; the object
    directory is ROOT/obj/<top>."""
    obj = root / "obj" / top
    shutil.rmtree(obj, ignore_errors=True)
    obj.mkdir(parents=True)  # Verilator creates only the last level of -Mdir
    if shutil.which("ccache") is None:
        os.environ.setdefault("OBJCACHE", "")  # Verilator's makefiles default to ccache
    backend = VerilatorBuild((Path("obj") / top).as_posix(), build)
    findings = run_backend(backend, list(files), top, root, list(incdirs))
    return Report("compile", tuple(sorted(findings)), (backend.name,))
