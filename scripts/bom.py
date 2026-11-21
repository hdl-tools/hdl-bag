# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Write docs/bom.md, the bill of materials: every vendored file and every locked Python package.

    uv run --no-project python scripts/bom.py          # rewrite docs/bom.md
    uv run --no-project python scripts/bom.py --check  # fail if it differs

Licences of vendored files are read from each file's own header, never from index.json, whose
licence field is a constant. Python licences are hand-kept in LICENCES because installed
metadata is missing or partial for some packages; tests/vendor/test_bom.py keeps the keys equal
to the uv.lock closure. Platform markers are ignored so the output is machine-independent.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "hdl_bag"
PULP, DERIVED, ZIPCPU = (
    PKG / "rtl/vendor/pulp",
    PKG / "rtl/vendor/derived",
    PKG / "formal/vendor/zipcpu",
)
OUT = REPO / "docs" / "bom.md"
NOT_BESIDE = "not beside the file"

# Verified against each project's own licence file or License-Expression.
# fmt: off
LICENCES = {
    "annotated-types": "MIT", "click": "BSD-3-Clause", "cocotb": "BSD-3-Clause",
    "cocotb-bus": "BSD-3-Clause", "cocotbext-axi": "MIT", "colorama": "BSD-3-Clause",
    "et-xmlfile": "MIT", "find-libpython": "MIT", "iniconfig": "MIT", "jinja2": "BSD-3-Clause",
    "markupsafe": "BSD-3-Clause", "openpyxl": "MIT", "packaging": "Apache-2.0 OR BSD-2-Clause",
    "pluggy": "MIT", "pydantic": "MIT", "pydantic-core": "MIT", "pygments": "BSD-2-Clause",
    "pytest": "MIT", "ruamel-yaml": "MIT", "scapy": "GPL-2.0-only", "typing-extensions": "PSF-2.0",
    "typing-inspection": "MIT",
}
# fmt: on
_SPDX = re.compile(r"SPDX-License-Identifier:\s*(.+?)\s*$", re.M)
_NOTICES = (
    ("solderpad.org/licenses/SHL-0.51", "SHL-0.51"),
    ("Apache License, Version 2.0", "Apache-2.0"),
)
_TEXTS = (("SOLDERPAD HARDWARE LICENSE version 0.51", "SHL-0.51"), ("Apache License", "Apache-2.0"))


class FileSet(NamedTuple):
    name: str
    version: str
    commit: str
    url: str
    licence: str
    paths: list[str]
    text: str | None  # LICENSE beside the files that really is this licence, else None


class PyRow(NamedTuple):
    name: str
    use: str
    declared: str
    locked: str
    licence: str


def licence_of(path: Path) -> str:
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:40])
    if m := _SPDX.search(head):
        return m[1]
    for needle, spdx in _NOTICES:
        if needle in head:
            return spdx
    raise SystemExit(f"{path}: no licence notice recognised in its first 40 lines; stop and check")


def licence_of_text(path: Path) -> str:
    first = next(
        (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()), ""
    )
    for opening, spdx in _TEXTS:
        if first.startswith(opening):
            return spdx
    raise SystemExit(f"{path}: licence text not recognised from its opening line; stop and check")


def _sets(
    name: str, ver: str, rev: str, url: str, licdir: Path, paths: list[Path]
) -> list[FileSet]:
    by: dict[str, list[str]] = {}
    for p in sorted(paths):
        by.setdefault(licence_of(p), []).append(p.relative_to(REPO).as_posix())
    text = licdir / "LICENSE"
    shown = text.relative_to(REPO).as_posix()
    return [
        FileSet(name, ver, rev, url, lic, ps, shown if licence_of_text(text) == lic else None)
        for lic, ps in sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]


def file_sets() -> list[FileSet]:
    index = json.loads((PULP / "index.json").read_text(encoding="utf-8"))
    out: list[FileSet] = []
    entries = index["files"] + index["headers"]
    for name, pkg in sorted(index["packages"].items()):
        paths = [PULP / e["path"] for e in entries if e["path"].startswith(name + "/")]
        out += _sets(
            name,
            pkg["version"],
            pkg["revision"],
            pkg["url"].removesuffix(".git"),
            PULP / name,
            paths,
        )
    axi = index["packages"]["axi"]
    out += _sets(
        "axi, modified (`scripts/derive_mux.py`)",
        axi["version"],
        axi["revision"],
        axi["url"].removesuffix(".git"),
        PULP / "axi",
        list(DERIVED.glob("*.sv")),
    )
    src = (ZIPCPU / "SOURCE.md").read_text(encoding="utf-8")
    m = re.search(r"<(https://\S+?)> `(\S+?)` at commit\s+`([0-9a-f]{40})`", src)
    if not m:
        raise SystemExit(f"{ZIPCPU / 'SOURCE.md'}: no source and commit found; stop and check")
    return out + _sets(f"wb2axip (`{m[2]}`)", "", m[3], m[1], ZIPCPU, list(ZIPCPU.glob("*.v")))


def _closure(lock: dict[str, Any], roots: list[str]) -> tuple[dict[str, str], dict[str, set[str]]]:
    seen: dict[str, str] = {}
    why: dict[str, set[str]] = {}
    todo = list(roots)
    while todo:
        n = todo.pop()
        if n not in seen:
            seen[n] = lock[n]["version"]
            for d in lock[n].get("dependencies", []):
                why.setdefault(d["name"], set()).add(n)
                todo.append(d["name"])
    return seen, why


def python_rows() -> list[PyRow]:
    try:
        data = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
        lock = {p["name"]: p for p in data["package"]}
        me = lock["hdl-bag"]
        spec = {r["name"]: r["specifier"] for r in me["metadata"]["requires-dist"]}
        runtime = [d["name"] for d in me["dependencies"]]
        sim = [d["name"] for d in me["optional-dependencies"]["sim"]]
        rt_seen, rt_why = _closure(lock, runtime)
        sim_seen, sim_why = _closure(lock, sim)
        rows: list[PyRow] = []
        for use, roots, seen, why in (
            ("runtime", runtime, rt_seen, rt_why),
            ("`sim` extra", sim, {k: v for k, v in sim_seen.items() if k not in rt_seen}, sim_why),
        ):
            for n in sorted(seen):
                decl = spec[n] if n in roots else "via " + ", ".join(sorted(why[n]))
                rows.append(PyRow(n, use, decl, seen[n], LICENCES.get(n, "UNKNOWN")))
    except KeyError as e:
        raise SystemExit(f"uv.lock: unexpected format, missing {e}; stop and check") from e
    return rows


def render() -> str:
    sets = file_sets()
    L = [
        "<!-- Generated by scripts/bom.py; do not edit."
        " Refresh with: uv run --no-project python scripts/bom.py -->",
        "# Bill of materials",
        "",
        "Everything hdl-bag ships that its authors did not write, and every Python package it"
        " installs.",
        "[third-party.md](third-party.md) says how the vendored trees are made and checked.",
        "",
        "## Vendored files",
        "",
        "Shipped in the package under `src/hdl_bag/`; `bag generate` copies the ones a design uses",
        "into its output. Each file's licence is read from its own header, not from `index.json`.",
        "Two files are not SHL-0.51 and have no licence text beside them; shipping those texts is",
        "tracked by #172.",
        "",
        "| Package | Version | Commit | Licence | Files | Licence text | Source |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in sets:
        text = f"`{s.text}`" if s.text else NOT_BESIDE
        L.append(
            f"| {s.name} | {s.version or 'none'} | `{s.commit}` | {s.licence} | {len(s.paths)} |"
            f" {text} | <{s.url}> |"
        )
    L += ["", "### Files", ""]
    for s in sets:
        L += [f"{s.name}, {s.licence}:", ""] + [f"- `{p}`" for p in s.paths] + [""]
    L += [
        "## Python packages",
        "",
        "The full `uv.lock` closure of hdl-bag and its `sim` extra, so a copyleft package such as",
        "scapy (GPL-2.0-only, via `cocotbext-axi` and `cocotb-bus`) is visible. hdl-bag never",
        "redistributes these: they are installed by `pip`, never copied into the package or into",
        "generated output. *Declared* is what `pyproject.toml` asks for; *Locked* is the `uv.lock`",
        "version. Platform markers are ignored, so `colorama` appears although it installs only on"
        " Windows.",
        "",
        "| Package | For | Declared | Locked | Licence |",
        "|---|---|---|---|---|",
    ]
    for r in python_rows():
        decl = r.declared if r.declared.startswith("via") else f"`{r.declared}`"
        L.append(f"| {r.name} | {r.use} | {decl} | {r.locked} | {r.licence} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--check", action="store_true", help="fail if docs/bom.md is stale")
    text = render()
    if ap.parse_args().check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("docs/bom.md is stale: run `uv run --no-project python scripts/bom.py`")
            return 1
        return 0
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
