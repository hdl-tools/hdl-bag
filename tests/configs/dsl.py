# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The gate corpora's side of the workbook DSL, which lives in the package
(hdl_bag.regress.dsl) so that ``bag regress`` can use it: the p2 File header, and building a
corpus's configs into committed IR."""

from collections.abc import Callable
from pathlib import Path

from hdl_bag.ir.model import Spec
from hdl_bag.regress.dsl import *  # noqa: F403
from hdl_bag.regress.dsl import Cfg, unexpected

HEADER = Path(__file__).parent / "p2" / "header.txt"


def resolve_cfg(cfg: Cfg, tmp: Path) -> tuple[Spec | None, list[str]]:
    """CFG through workbook -> bag check -> resolve in TMP: (IR, []), or (None, findings) on
    any finding but the WRAP refusals CFG declares."""
    import shutil

    from hdl_bag.checks import run_checks
    from hdl_bag.ir import resolve
    from hdl_bag.reader import read_workbook
    from hdl_bag.template.builder import build_workbook

    path = tmp / f"{cfg.name}.xlsx"
    build_workbook(data=cfg.data).save(path)
    shutil.copy(HEADER, tmp / "header.txt")
    result = run_checks(read_workbook(path), tmp)
    wrong = unexpected((f"{f.severity}: {f.message}" for f in result.findings), cfg.wrap)
    if wrong:
        return None, wrong
    return resolve(result), []


def write_all(built: dict[str, Callable[[], Cfg]], here: Path) -> None:
    """Build every config through workbook -> bag check -> resolve; write its IR to HERE."""
    import tempfile

    from hdl_bag.ir import dump

    with tempfile.TemporaryDirectory() as tmp:
        for name, make in built.items():
            spec, findings = resolve_cfg(make(), Path(tmp))
            if spec is None:
                print(f"{name}:")
                for f in findings:
                    print(f"  {f}")
                continue
            dump(spec, here / f"{name}.ir.yaml")
            print(f"{name}: ok")
