# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""In-house RTL: lint-clean with every tool, and directed cocotb smoke tests (eda)."""

from importlib.resources import files
from pathlib import Path

import pytest
from simrun import run_cocotb

from hdl_bag.flows import lint

HERE = Path(__file__).parent
REPO = HERE.parents[1]
INHOUSE = Path(str(files("hdl_bag") / "rtl" / "inhouse"))
LIBRARY = sorted(INHOUSE.glob("*.sv"))
TB = HERE / "tb"

pytestmark = pytest.mark.eda


def _sources(*extra: Path) -> list[Path]:
    return [p.relative_to(REPO) for p in (*LIBRARY, *extra)]


LINT_TOPS = [
    *(p.stem for p in LIBRARY),
    "tb_apb_mux",
    "tb_ahb_lite_bus",
    "tb_ahb2apb",
]


@pytest.mark.parametrize("top", LINT_TOPS)
def test_lint_clean(top: str) -> None:
    tb = [TB / f"{top}.sv"] if top.startswith("tb_") else []
    report = lint(_sources(*tb), top, REPO)
    assert report.ok, report.format()


def _run(top: str, module: str, build: str, params: dict[str, int], env: dict[str, str]) -> None:
    run_cocotb(_sources(TB / f"{top}.sv"), top, HERE / f"{module}.py", build, params, env)


@pytest.mark.parametrize("miss_to_default", [0, 1])
def test_apb_mux(miss_to_default: int) -> None:
    _run("tb_apb_mux", "cocotb_apb_mux", f"apb_mux_{miss_to_default}",
         {"MissToDefault": miss_to_default}, {"MISS_TO_DEFAULT": str(miss_to_default)})  # fmt: skip


@pytest.mark.parametrize("fixed", [0, 1])
def test_ahb_lite_bus(fixed: int) -> None:
    _run("tb_ahb_lite_bus", "cocotb_ahb_lite_bus", f"ahb_bus_{fixed}",
         {"FixedPriority": fixed}, {"FIXED_PRIORITY": str(fixed)})  # fmt: skip


@pytest.mark.parametrize(
    ("hdw", "pdw", "offset", "hclk", "pclk"),
    [
        (32, 32, 0, 10, 10),  # same clock, same width, pass-through Y
        (64, 32, 0x4000_0000, 10, 10),  # 64 -> 32 split, pass-through N
        (32, 8, 0, 10, 30),  # CDC 1:3, 32 -> 8
        (64, 32, 0x3FFF_F000, 30, 10),  # CDC 3:1, an offset that needs a borrow
        (64, 8, 0x2000_0100, 10, 7),  # CDC 10:7, 64 -> 8 split
    ],
)
def test_ahb2apb(hdw: int, pdw: int, offset: int, hclk: int, pclk: int) -> None:
    cdc = int(hclk != pclk)
    _run("tb_ahb2apb", "cocotb_ahb2apb", f"ahb2apb_{hdw}_{pdw}_{offset:x}_{hclk}_{pclk}",
         {"HDataWidth": hdw, "PDataWidth": pdw, "AddrOffset": offset, "AsyncCdc": cdc},
         {"HDW": str(hdw), "PDW": str(pdw), "OFFSET": hex(offset), "HCLK_NS": str(hclk),
          "PCLK_NS": str(pclk)})  # fmt: skip
