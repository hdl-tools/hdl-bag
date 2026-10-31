# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""C header and documents checked against the IR (parsed back, not compared to the code)."""

import contextlib
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from hdl_bag import __version__
from hdl_bag.gen import MissingRtl, generate
from hdl_bag.gen.collateral import c_header
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir import Spec, load

TESTS = Path(__file__).parents[1]
GOLDEN_IR = TESTS / "golden" / "demo_ss.ir.yaml"
SNAPSHOTS = TESTS / "golden" / "collateral"
CONFIGS = {
    "demo_ss": GOLDEN_IR,
    "ahb2apb_pt": TESTS / "configs" / "p2" / "ahb2apb_pt.ir.yaml",
    "ahb2apb_nested": TESTS / "configs" / "p2" / "ahb2apb_nested.ir.yaml",
}
FILES = ("sw/demo_ss_memmap.h", "doc/memmap.md", "doc/memmap.html", "doc/port_map.md")


def _generate(spec: Spec, src: Path, root: Path) -> Path:
    with contextlib.suppress(MissingRtl):  # the collateral is written before RTL stops
        generate(spec, root, src.parent)
    return root


@pytest.fixture(params=sorted(CONFIGS))
def generated(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Spec, Path]:
    src = CONFIGS[request.param]
    spec = load(src)
    return spec, _generate(spec, src, tmp_path)


def _region_rows(spec: Spec) -> dict[str, set[tuple[str, str, int, int, str, bool]]]:
    """Expected memory-map rows per fabric, straight from the IR."""
    out: dict[str, set[tuple[str, str, int, int, str, bool]]] = {
        f.name: set() for f in spec.fabrics
    }
    for r in spec.memory_map:
        out[r.fabric].add((r.slave, r.region, r.base, r.size, r.access, r.secure))
    return out


# ---------------------------------------------------------------- C header


def test_c_header_matches_memory_map(generated: tuple[Spec, Path]) -> None:
    spec, root = generated
    text = (root / "sw" / f"{spec.project.name}_memmap.h").read_text()
    found: dict[str, int] = {}
    for name, value in re.findall(r"^#define (\w+) 0x([0-9A-F]+)ULL\b", text, re.M):
        assert name not in found, f"{name} defined twice"
        found[name] = int(value, 16)
    expected = {}
    for r in spec.memory_map:
        stem = f"{r.slave}_{r.region}".upper()
        expected[f"{stem}_BASE"], expected[f"{stem}_SIZE"] = r.base, r.size
    assert found == expected
    guard = f"{spec.project.name.upper()}_MEMMAP_H"
    assert f"#ifndef {guard}\n#define {guard}\n" in text and text.endswith(
        f"#endif /* {guard} */\n"
    )


def test_c_macro_collision_is_an_error(tmp_path: Path) -> None:
    spec = load(CONFIGS["ahb2apb_pt"])
    r = next(r for r in spec.memory_map if r.slave == "rtc0")
    clash = r.model_copy(update={"slave": "rtc0_regs", "region": "x", "base": 0x5000_3000})
    clash2 = r.model_copy(update={"slave": "rtc0", "region": "regs_x", "base": 0x5000_4000})
    spec = spec.model_copy(update={"memory_map": (*spec.memory_map, clash, clash2)})
    # generate() stops earlier on the package names (CamelCase drops the underscores, so
    # every C-macro collision is also a package collision); test the header's own guard.
    with pytest.raises(GenerateError, match="package names collide"):
        generate(spec, tmp_path, CONFIGS["ahb2apb_pt"].parent)
    with pytest.raises(GenerateError, match="C macro RTC0_REGS_X_BASE is claimed by"):
        c_header(spec, None)


# ---------------------------------------------------------------- documents


def _md_tables(text: str) -> dict[str, list[list[str]]]:
    """'## fabric' / '### sub' heading -> rows of the first table under it."""
    out: dict[str, list[list[str]]] = {}
    heading = ""
    for block in re.split(r"\n(?=#{2,3} )", text):
        first = block.splitlines()[0]
        if first.startswith("## "):
            heading = first[3:]
        elif first.startswith("### "):
            heading = f"{heading.split('/')[0]}/{first[4:]}"
        rows = [line for line in block.splitlines() if line.startswith("| ")]
        if rows:
            out[heading] = [[c.strip() for c in r.strip("|").split(" | ")] for r in rows[1:]]
    return out


def _parse_rows(rows: list[list[str]]) -> set[tuple[str, str, int, int, str, bool]]:
    out = set()
    for slave, region, base, end, size, access, secure in rows:
        b, e, s = (int(x.replace("_", ""), 16) for x in (base, end, size))
        assert e == b + s - 1, (slave, region)
        out.add((slave.split(" (bridge")[0], region, b, s, access, secure == "Y"))
    return out


def test_memmap_md_matches_ir(generated: tuple[Spec, Path]) -> None:
    spec, root = generated
    tables = _md_tables((root / "doc" / "memmap.md").read_text())
    for fabric, expected in _region_rows(spec).items():
        assert _parse_rows(tables[fabric]) == expected, fabric
    for f in spec.fabrics:  # aliases
        expected_aliases = {(d.master, a.base, a.size, a.to, a.enable)
                            for d in f.decode for a in d.aliases}  # fmt: skip
        rows = tables.get(f"{f.name}/Aliases", [])
        got = {(m, int(b.replace("_", ""), 16), int(s.replace("_", ""), 16),
                int(t.replace("_", ""), 16), en) for m, b, s, t, en in rows}  # fmt: skip
        assert got == expected_aliases, f.name


class _HtmlTables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h2 = ""
        self.tables: dict[str, list[list[str]]] = {}
        self.cell: str | None = None
        self.row: list[str] = []
        self.links: list[str] = []
        self._heading = ""  # "h2" / "h3" while inside one

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h2", "h3"):
            self._heading = tag
        self.links += [v or "" for k, v in attrs if k in ("href", "src")]
        if tag == "td":
            self.cell = ""
        elif tag == "tr":
            self.row = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h2", "h3"):
            self._heading = ""
        if tag == "td" and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row:
            self.tables.setdefault(self.h2, []).append(self.row)

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell += data
        elif self._heading == "h2":
            self.h2 = data
        elif self._heading == "h3":
            self.h2 = f"{self.h2.split('/')[0]}/{data}"


def test_memmap_html_matches_ir_and_is_self_contained(generated: tuple[Spec, Path]) -> None:
    spec, root = generated
    text = (root / "doc" / "memmap.html").read_text()
    parser = _HtmlTables()
    parser.feed(text)
    for fabric, expected in _region_rows(spec).items():
        assert _parse_rows(parser.tables.get(fabric, [])) == expected, fabric
    assert parser.links == [] and "http" not in text.split("-->", 1)[1]
    md = _md_tables((root / "doc" / "memmap.md").read_text())
    assert {k: v for k, v in parser.tables.items() if "/" not in k} == {
        k: v for k, v in md.items() if "/" not in k
    }  # the same rows in both documents


def test_port_map_matches_ir(generated: tuple[Spec, Path]) -> None:
    spec, root = generated
    tables = _md_tables((root / "doc" / "port_map.md").read_text())
    for f in spec.fabrics:
        for side, refs in (("Master ports", f.master_ports), ("Slave ports", f.slave_ports)):
            rows = tables[f"{f.name}/{side}"]
            got = [(int(r[0]), r[1], None if r[7] == "-" else int(r[7])) for r in rows]
            assert got == sorted((p.index, p.port, p.priority) for p in refs), (f.name, side)
            chains = {r[1]: r[8] for r in rows}
            for p in refs:
                names = [a.name for a in sorted(spec.adapters, key=lambda a: a.chain_index)
                         if a.port == p.port and a.fabric == f.name]  # fmt: skip
                assert chains[p.port] == (", ".join(names) or "-")


def test_output_flags(tmp_path: Path) -> None:
    src = CONFIGS["ahb2apb_pt"]
    spec = load(src)
    outputs = spec.project.outputs.model_copy(update={"c_header": False, "doc": False})
    spec = spec.model_copy(update={"project": spec.project.model_copy(update={"outputs": outputs})})
    generate(spec, tmp_path, src.parent)
    assert not (tmp_path / "sw").exists() and not (tmp_path / "doc").exists()


# ---------------------------------------------------------------- golden snapshots


def _snapshot_text(root: Path, rel: str) -> bytes:
    return (root / rel).read_bytes().replace(f"hdl-bag {__version__} ".encode(), b"hdl-bag X ")


def test_golden_collateral_snapshot(tmp_path: Path) -> None:
    """demo_ss collateral is byte-stable. BAG_UPDATE_SNAPSHOTS=1 rewrites the snapshot."""
    root = _generate(load(GOLDEN_IR), GOLDEN_IR, tmp_path)
    if os.environ.get("BAG_UPDATE_SNAPSHOTS") == "1":
        for rel in FILES:
            (SNAPSHOTS / rel).parent.mkdir(parents=True, exist_ok=True)
            (SNAPSHOTS / rel).write_bytes(_snapshot_text(root, rel))
    for rel in FILES:
        assert _snapshot_text(root, rel) == (SNAPSHOTS / rel).read_bytes(), rel


# ---------------------------------------------------------------- the header compiles (eda)


@pytest.mark.eda
def test_c_header_compiles(tmp_path: Path) -> None:
    gcc = shutil.which("gcc")
    assert gcc, "gcc not installed"
    spec = load(GOLDEN_IR)
    root = _generate(spec, GOLDEN_IR, tmp_path / "out")
    asserts = "\n".join(
        f'_Static_assert({f"{r.slave}_{r.region}".upper()}_BASE == 0x{r.base:X}ULL, "base");'
        for r in spec.memory_map
    )
    c = tmp_path / "use.c"
    c.write_text(f'#include "demo_ss_memmap.h"\n#include "demo_ss_memmap.h"\n{asserts}\n')
    cmd = [gcc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", "-fsyntax-only",
           "-I", str(root / "sw"), str(c)]  # fmt: skip
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
