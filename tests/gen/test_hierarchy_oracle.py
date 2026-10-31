# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Hierarchy variants through the structural oracle; invalid ones are refused."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from hier_oracle import check
from hier_variants import CONFIGS, INVALID, VALID, build

from hdl_bag.gen import generate
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir import dump


@pytest.mark.parametrize("variant", VALID)
def test_variant_matches_the_oracle(tmp_path: Path, variant: str) -> None:
    generate(build(variant), tmp_path, CONFIGS, "flat")
    assert check(build(variant), tmp_path / "rtl") == []


@pytest.mark.parametrize("variant", sorted(INVALID))
def test_invalid_variant_is_refused(tmp_path: Path, variant: str) -> None:
    with pytest.raises(GenerateError, match=INVALID[variant]):
        generate(build(variant), tmp_path, CONFIGS, "flat")


def test_oracle_sees_a_swapped_connection(tmp_path: Path) -> None:
    """The oracle itself: swapping two same-width boundary connections is caught."""
    generate(build("deep"), tmp_path, CONFIGS, "flat")
    aon = tmp_path / "rtl" / "aon_wrapper.sv"
    text = aon.read_text()
    a, b = ".wdt0_apb4_pwdata(wdt0_apb4_pwdata)", ".rtc0_apb4_pwdata(rtc0_apb4_pwdata)"
    assert a in text and b in text
    aon.write_text(
        text.replace(a, "@@")
        .replace(b, a.replace("(wdt0", "(rtc0"))
        .replace("@@", b.replace("(rtc0", "(wdt0"))
    )
    errors = check(build("deep"), tmp_path / "rtl")
    assert any("shares a net" in e or "separate nets" in e for e in errors), errors


def test_two_domain_hierarchy_is_byte_identical_across_processes(tmp_path: Path) -> None:
    dump(build("sibling2dom"), tmp_path / "v.ir.yaml")
    (tmp_path / "header.txt").write_text((CONFIGS / "header.txt").read_text())
    trees = []
    for seed in ("0", "3", "99"):
        out = tmp_path / seed
        cmd = [sys.executable, "-m", "hdl_bag.cli", "generate", str(tmp_path / "v.ir.yaml"),
               "-o", str(out)]  # fmt: skip
        subprocess.run(cmd, env={**os.environ, "PYTHONHASHSEED": seed}, check=True,
                       capture_output=True)  # fmt: skip
        trees.append({p.relative_to(out).as_posix(): p.read_bytes()
                      for p in out.rglob("*") if p.is_file()})  # fmt: skip
    assert trees[0] == trees[1] == trees[2]
