# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Vendor the pinned PULP RTL into src/hdl_bag/rtl/vendor/pulp/ (run in the tools image).

    uv run --no-project python scripts/vendor_pulp.py          # refresh the vendored tree
    uv run --no-project python scripts/vendor_pulp.py --check  # fail if it is out of date

Bender checks out the commits in Bender.lock. ``bender script flist-plus`` gives the
compile order. Only the transitive closure of ``ROOTS`` (the PULP modules BAG's templates
instantiate) is copied, together with the headers it includes and each package's LICENSE.
``index.json`` records, per file, the design units it defines and the files it needs, so
``bag generate`` copies only what a design uses. The script needs only the standard
library, so it runs with ``--no-project`` on the image's Python.
"""

import argparse
import filecmp
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "src" / "hdl_bag" / "rtl" / "vendor" / "pulp"

# PULP modules BAG instantiates (checked against the templates' `bender:` sources by
# tests/vendor/test_vendor.py). Their dependencies are found automatically.
ROOTS = (
    "axi_cdc",
    "axi_cut",
    "axi_demux",
    "axi_dw_converter",
    "axi_err_slv",
    "axi_fifo",
    "axi_iw_converter",
    "axi_lite_demux",
    "axi_lite_dw_converter",
    "axi_lite_mux",
    "axi_lite_to_apb",
    "axi_lite_to_axi",
    "axi_lite_xbar",
    "axi_modify_address",
    "axi_multicut",
    "axi_mux",
    "axi_pkg",
    "axi_to_axi_lite",
    "axi_xbar",
    "rr_arb_tree",
)
LICENCE = "SHL-0.51"
URL = "https://github.com/pulp-platform/{}.git"

_UNIT = re.compile(r"^\s*(?:module|package|interface)\s+(?:automatic\s+|static\s+)?(\w+)", re.M)
_INCLUDE = re.compile(r'`include\s+"([^"]+)"')
_WORD = re.compile(r"\b[A-Za-z_]\w*\b")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=REPO, check=True, capture_output=True, text=True).stdout


def _package(path: Path) -> str:
    """The Bender package of a checkout path (.bender/git/checkouts/<pkg>-<hash>/...)."""
    rel = path.relative_to(REPO / ".bender" / "git" / "checkouts")
    return rel.parts[0].rsplit("-", 1)[0]


def _lock() -> dict[str, tuple[str, str]]:
    """package -> (version, revision) from Bender.lock (a tiny YAML subset)."""
    out: dict[str, tuple[str, str]] = {}
    name, rev = "", ""
    for line in (REPO / "Bender.lock").read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^  (\w+):$", line):
            name = m[1]
        elif m := re.match(r"^    revision: (\w+)$", line):
            rev = m[1]
        elif m := re.match(r"^    version: (\S+)$", line):
            out[name] = (m[1], rev)
    return out


def collect() -> tuple[list[tuple[str, Path]], list[tuple[str, Path]]]:
    """(sources in compile order, include dirs), each as (vendored rel path, checkout path)."""
    _run("bender", "checkout")
    srcs: list[tuple[str, Path]] = []
    incdirs: list[tuple[str, Path]] = []
    for line in _run("bender", "script", "flist-plus").split():
        if line.startswith("+define+"):
            continue
        is_inc = line.startswith("+incdir+")
        path = Path(line.removeprefix("+incdir+"))
        root = (
            REPO
            / ".bender"
            / "git"
            / "checkouts"
            / path.relative_to(REPO / ".bender" / "git" / "checkouts").parts[0]
        )
        rel = f"{_package(path)}/{path.relative_to(root).as_posix()}"
        (incdirs if is_inc else srcs).append((rel, path))
    return srcs, incdirs


def build(dest: Path) -> None:
    srcs, incdirs = collect()
    texts = {rel: path.read_text(encoding="utf-8") for rel, path in srcs}
    headers: dict[str, Path] = {}
    for inc_rel, inc_path in incdirs:
        for h in sorted(inc_path.rglob("*.svh")):
            headers[f"{inc_rel}/{h.relative_to(inc_path).as_posix()}"] = h
    defines: dict[str, str] = {}  # design unit -> file
    for rel, text in texts.items():
        for unit in _UNIT.findall(_COMMENT.sub("", text)):
            defines.setdefault(unit, rel)
    by_include = {}  # `include "axi/typedef.svh" -> vendored header path
    for hrel in headers:
        for inc_rel, _ in incdirs:
            if hrel.startswith(inc_rel + "/"):
                by_include[hrel.removeprefix(inc_rel + "/")] = hrel

    def needs(text: str, own: str) -> list[str]:
        body = _COMMENT.sub("", text)
        files = {defines[w] for w in set(_WORD.findall(body)) if w in defines} - {own}
        files |= {by_include[i] for i in _INCLUDE.findall(body) if i in by_include}
        return sorted(files)

    requires = {rel: needs(text, rel) for rel, text in texts.items()}
    for hrel, hpath in headers.items():
        requires[hrel] = needs(hpath.read_text(encoding="utf-8"), hrel)

    keep: set[str] = set()
    todo = [defines[r] for r in ROOTS]
    while todo:
        rel = todo.pop()
        if rel not in keep:
            keep.add(rel)
            todo += requires[rel]

    if dest.exists():
        shutil.rmtree(dest)
    lock = _lock()
    packages = sorted({rel.split("/", 1)[0] for rel in keep})
    for pkg in packages:
        root = next(p for r, p in srcs + incdirs if r.startswith(pkg + "/"))
        while root.parent.name != "checkouts":
            root = root.parent
        _copy(root / "LICENSE", dest / pkg / "LICENSE")
    for rel in sorted(keep):
        _copy(dict(srcs).get(rel) or headers[rel], dest / rel)

    index = {
        "comment": "Generated by scripts/vendor_pulp.py; do not edit.",
        "packages": {
            p: {
                "version": lock[p][0],
                "revision": lock[p][1],
                "licence": LICENCE,
                "url": URL.format(p),
            }
            for p in packages
        },
        "incdirs": [rel for rel, _ in incdirs if any(k.startswith(rel + "/") for k in keep)],
        "files": [
            {
                "path": rel,
                "defines": sorted(u for u, f in defines.items() if f == rel),
                "requires": requires[rel],
            }
            for rel, _ in srcs
            if rel in keep
        ],
        "headers": [{"path": h, "requires": requires[h]} for h in sorted(headers) if h in keep],
    }
    with (dest / "index.json").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(index, indent=2) + "\n")


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    with dst.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _same(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        print(f"differs: {a} vs {b}: {cmp.left_only + cmp.right_only + cmp.diff_files}")
        return False
    return all(_same(a / d, b / d) for d in cmp.common_dirs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the vendored tree is stale")
    args = ap.parse_args()
    if not args.check:
        build(DEST)
        print(f"vendored into {DEST.relative_to(REPO)}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "pulp"
        build(fresh)
        if not DEST.is_dir() or not _same(fresh, DEST):
            print("vendored PULP tree is stale: run scripts/vendor_pulp.py")
            return 1
    print("vendored PULP tree is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
