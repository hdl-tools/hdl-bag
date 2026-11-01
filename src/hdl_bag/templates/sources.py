# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Resolve template ``sources`` to the package files a design must compile.

A source is either a package-relative path (in-house RTL, ``rtl/inhouse/*.sv``) or a
Bender reference ``bender:<package>:<module>`` into the vendored PULP tree. A Bender
reference expands to the module's file and everything it needs, as recorded in
``rtl/vendor/pulp/index.json`` by ``scripts/vendor_pulp.py``. Paths stay package-relative,
and generation copies them to the same relative path in the output directory.
"""

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

VENDOR = "rtl/vendor/pulp"


class SourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSet:
    files: tuple[str, ...]  # compile order: vendored files (index order), then in-house
    headers: tuple[str, ...]  # `include files to copy (not compiled on their own)
    incdirs: tuple[str, ...]  # include directories the headers need


@cache
def _index() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (files("hdl_bag") / VENDOR / "index.json").read_text(encoding="utf-8")
    )
    return data


def vendored_units() -> dict[str, str]:
    """Every design unit in the vendored tree -> its vendored path (relative to VENDOR)."""
    return {u: f["path"] for f in _index()["files"] for u in f["defines"]}


def resolve_sources(refs: list[str]) -> SourceSet:
    """Expand REFS (in-house paths and Bender references) into a SourceSet."""
    index = _index()
    units = vendored_units()
    requires = {e["path"]: e["requires"] for e in index["files"] + index["headers"]}
    keep: set[str] = set()
    inhouse: set[str] = set()
    for ref in refs:
        if not ref.startswith("bender:"):
            inhouse.add(ref)
            continue
        _, _, rest = ref.partition(":")
        pkg, sep, module = rest.partition(":")
        path = units.get(module)
        if not sep or path is None or not path.startswith(f"{pkg}/"):
            raise SourceError(f"{ref}: no such module in the vendored PULP tree")
        todo = [path]
        while todo:
            rel = todo.pop()
            if rel not in keep:
                keep.add(rel)
                todo += requires[rel]
    ordered = [e["path"] for e in index["files"] if e["path"] in keep]
    headers = sorted(h for h in keep if h not in set(ordered))
    incdirs = [d for d in index["incdirs"] if any(h.startswith(f"{d}/") for h in headers)]
    return SourceSet(
        files=(*(f"{VENDOR}/{p}" for p in ordered), *sorted(inhouse)),
        headers=tuple(f"{VENDOR}/{h}" for h in headers),
        incdirs=tuple(f"{VENDOR}/{d}" for d in incdirs),
    )
