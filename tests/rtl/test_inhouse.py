# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""In-house RTL: lint-clean with every tool, and directed cocotb tests (eda)."""

from importlib.resources import files
from pathlib import Path

import pytest
from simrun import run_cocotb

from hdl_bag.flows import lint
from hdl_bag.templates.registry import builtin_registry
from hdl_bag.templates.sources import resolve_sources

HERE = Path(__file__).parent
REPO = HERE.parents[1]
INHOUSE = Path(str(files("hdl_bag") / "rtl" / "inhouse"))
# Stand-alone in-house blocks. The AXI blocks (bag_axi*, bag_prio_arb) build on the
# vendored PULP RTL and are linted and simulated inside generated designs instead
# (tests/gen/test_corpus.py).
LIBRARY = sorted(p for p in INHOUSE.glob("*.sv") if not p.stem.startswith(("bag_axi", "bag_prio")))
TB = HERE / "tb"

pytestmark = pytest.mark.eda


def _sources(*extra: Path) -> list[Path]:
    return [p.relative_to(REPO) for p in (*LIBRARY, *extra)]


LINT_TOPS = [
    *(p.stem for p in LIBRARY),
    "tb_apb_mux",
    "tb_ahb_lite_bus",
    "tb_ahb2apb",
    "tb_apb2ahb",
    "tb_apb2axi",
    "tb_ahb2axi",
]
PKG = REPO / "src" / "hdl_bag"


def _template_sources(template: str, tb: str) -> tuple[list[Path], list[Path]]:
    """A template's sources (vendored PULP closure + in-house) and a testbench; incdirs."""
    manifest = builtin_registry().get(template)
    assert manifest is not None
    src = resolve_sources(list(manifest.sources))
    files = [(PKG / f).relative_to(REPO) for f in src.files] + [(TB / f"{tb}.sv").relative_to(REPO)]
    return files, [(PKG / d).relative_to(REPO) for d in src.incdirs]


@pytest.mark.parametrize("top", LINT_TOPS)
def test_lint_clean(top: str) -> None:
    tb = [TB / f"{top}.sv"] if top.startswith("tb_") else []
    report = lint(_sources(*tb), top, REPO)
    assert report.ok, report.format()


def test_lint_clean_axi2ahb() -> None:
    """bag_axi2ahb builds on the vendored PULP RTL (its findings are third-party, P3-2)."""
    files, incdirs = _template_sources("bag_axi2ahb", "tb_axi2ahb")
    report = lint(files, "tb_axi2ahb", REPO, incdirs=incdirs)
    assert report.ok, report.format()


def _run(top: str, module: str, build: str, params: dict[str, int], env: dict[str, str]) -> None:
    run_cocotb(_sources(TB / f"{top}.sv"), top, HERE / f"{module}.py", build, params, env)


def _env(up: str, down: str, up_ns: int, down_ns: int, cdc: int) -> dict[str, str]:
    return {f"{up}CLK_NS": str(up_ns), f"{down}CLK_NS": str(down_ns), "CDC": str(cdc)}


# Clock rows (up period, down period, CDC): 1:1 without and with the CDC logic (the second
# clock 3 ns out of phase), 1:3, 3:1 and 10:7.
@pytest.mark.parametrize(
    ("pdw", "hdw", "offset", "pclk", "hclk", "cdc"),
    [
        (32, 32, 0, 10, 10, 0),  # same clock, same width, pass-through Y
        (8, 64, 0x4000_0000, 10, 10, 0),  # 8 -> 64, pass-through N
        (32, 32, 0, 10, 10, 1),  # CDC 1:1
        (16, 32, 0, 10, 30, 1),  # CDC 1:3
        (32, 64, 0x3FFF_F000, 30, 10, 1),  # CDC 3:1, an offset that needs a borrow
        (8, 32, 0x2000_0100, 10, 7, 1),  # CDC 10:7
    ],
)
def test_apb2ahb(pdw: int, hdw: int, offset: int, pclk: int, hclk: int, cdc: int) -> None:
    _run("tb_apb2ahb", "cocotb_apb2ahb", f"apb2ahb_{pdw}_{hdw}_{offset:x}_{pclk}_{hclk}_{cdc}",
         {"PDataWidth": pdw, "HDataWidth": hdw, "AddrOffset": offset, "AsyncCdc": cdc},
         {"PDW": str(pdw), "HDW": str(hdw), "OFFSET": hex(offset),
          **_env("P", "H", pclk, hclk, cdc)})  # fmt: skip


@pytest.mark.parametrize(
    ("pdw", "adw", "lite", "offset", "pclk", "aclk", "cdc"),
    [
        (32, 32, 0, 0, 10, 10, 0),
        (8, 64, 0, 0x4000_0000, 10, 10, 0),  # narrow APB on a wide AXI4 bus, pass-through N
        (32, 64, 1, 0, 10, 10, 1),  # AXI4-Lite, CDC 1:1
        (16, 32, 1, 0, 10, 30, 1),  # AXI4-Lite, CDC 1:3
        (32, 64, 1, 0x3FFF_F000, 30, 10, 1),  # AXI4-Lite, CDC 3:1, borrow
        (32, 128, 0, 0, 10, 7, 1),  # CDC 10:7
    ],
)
def test_apb2axi(pdw: int, adw: int, lite: int, offset: int, pclk: int, aclk: int,
                 cdc: int) -> None:  # fmt: skip
    _run("tb_apb2axi", "cocotb_apb2axi",
         f"apb2axi_{pdw}_{adw}_{lite}_{offset:x}_{pclk}_{aclk}_{cdc}",
         {"PDataWidth": pdw, "ADataWidth": adw, "AddrOffset": offset, "AsyncCdc": cdc},
         {"PDW": str(pdw), "ADW": str(adw), "LITE": str(lite), "OFFSET": hex(offset),
          **_env("P", "A", pclk, aclk, cdc)})  # fmt: skip


@pytest.mark.parametrize(
    ("hdw", "adw", "lite", "offset", "hclk", "aclk", "cdc"),
    [
        (32, 32, 0, 0, 10, 10, 0),
        (64, 32, 0, 0x4000_0000, 10, 10, 0),  # a 64-bit beat splits in two, pass-through N
        (64, 64, 0, 0, 10, 10, 1),  # a full 64-bit beat as one transfer, CDC 1:1
        (32, 64, 1, 0, 10, 30, 1),  # AXI4-Lite, CDC 1:3
        (64, 32, 1, 0x3FFF_F000, 30, 10, 1),  # AXI4-Lite split, CDC 3:1, borrow
        (64, 128, 0, 0, 10, 10, 0),  # 64 on a 128-bit bus
        (32, 256, 0, 0, 10, 7, 1),  # narrow AHB on a wide AXI4 bus, CDC 10:7
    ],
)
def test_ahb2axi(hdw: int, adw: int, lite: int, offset: int, hclk: int, aclk: int,
                 cdc: int) -> None:  # fmt: skip
    _run("tb_ahb2axi", "cocotb_ahb2axi",
         f"ahb2axi_{hdw}_{adw}_{lite}_{offset:x}_{hclk}_{aclk}_{cdc}",
         {"HDataWidth": hdw, "ADataWidth": adw, "AddrOffset": offset, "AsyncCdc": cdc},
         {"HDW": str(hdw), "ADW": str(adw), "LITE": str(lite), "OFFSET": hex(offset),
          **_env("H", "A", hclk, aclk, cdc)})  # fmt: skip


@pytest.mark.parametrize(
    ("adw", "hdw", "lite", "idw", "fifo", "offset", "aclk", "hclk", "cdc"),
    [
        (32, 32, 0, 4, 0, 0, 10, 10, 0),  # same width and clock: exact burst mapping
        (64, 64, 0, 1, 2, 0x4000_0000, 10, 10, 0),  # ID width 1, FIFO, pass-through N
        (32, 32, 0, 3, 0, 0, 10, 10, 1),  # CDC 1:1
        (32, 32, 0, 4, 0, 0, 30, 10, 1),  # equal widths across CDC 3:1 (WRAP, FIXED)
        (64, 32, 0, 4, 0, 0, 10, 30, 1),  # downsizer, CDC 1:3
        (32, 64, 0, 2, 2, 0x3FFF_F000, 30, 10, 1),  # upsizer, FIFO, CDC 3:1, borrow
        (128, 32, 1, 4, 0, 0, 10, 7, 1),  # AXI4-Lite upstream, downsizer, CDC 10:7
        (32, 32, 1, 4, 0, 0x1000_0000, 10, 10, 0),  # AXI4-Lite upstream, same width
    ],
)
def test_axi2ahb(adw: int, hdw: int, lite: int, idw: int, fifo: int, offset: int, aclk: int,
                 hclk: int, cdc: int) -> None:  # fmt: skip
    files, incdirs = _template_sources("bag_axi2ahb", "tb_axi2ahb")
    run_cocotb(files, "tb_axi2ahb", HERE / "cocotb_axi2ahb.py",
               f"axi2ahb_{adw}_{hdw}_{lite}_{idw}_{fifo}_{offset:x}_{aclk}_{hclk}_{cdc}",
               {"ADataWidth": adw, "HDataWidth": hdw, "Lite": lite, "IdWidth": idw,
                "FifoDepth": fifo, "AddrOffset": offset, "AsyncCdc": cdc},
               {"ADW": str(adw), "HDW": str(hdw), "LITE": str(lite), "IDW": str(idw),
                "OFFSET": hex(offset), **_env("A", "H", aclk, hclk, cdc)}, incdirs)  # fmt: skip
