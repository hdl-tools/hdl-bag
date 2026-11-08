# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The vendored ZipCPU property files (D60): what is there, under which licence, and that it
is what the formal generator uses. The vendor CI job checks them against the pinned commit
(scripts/vendor_zipcpu.py --check)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from hdl_bag.formal_gen import _CHECKER

REPO = Path(__file__).parents[2]
DEST = REPO / "src" / "hdl_bag" / "formal" / "vendor" / "zipcpu"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "vendor_zipcpu", REPO / "scripts" / "vendor_zipcpu.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["vendor_zipcpu"] = module
    spec.loader.exec_module(module)
    return module


def test_only_the_listed_files_are_vendored_with_the_licence_and_source() -> None:
    script = _script()
    assert sorted(p.name for p in DEST.iterdir()) == sorted([*script.FILES, "LICENSE", "SOURCE.md"])
    assert (DEST / "LICENSE").read_bytes() == (REPO / "LICENSE").read_bytes()
    assert script.COMMIT in (DEST / "SOURCE.md").read_text(encoding="utf-8")


def test_every_vendored_file_carries_the_apache_header() -> None:
    for name in _script().FILES:
        head = (DEST / name).read_text(encoding="utf-8")[:2000]
        assert "Apache License, Version 2.0" in head, name
        assert "not intended to be functional" not in head, name  # D60: no redacted subsets


def test_the_generator_uses_exactly_the_vendored_checkers() -> None:
    assert sorted(f"{v}.v" for v in set(_CHECKER.values())) == sorted(_script().FILES)
