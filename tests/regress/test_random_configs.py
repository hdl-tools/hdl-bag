# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The random config generator of bag regress (PR 5): deterministic per seed (D97), legal by
bag check (a check error is a generator bug), and reaching every dimension of the plan."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hdl_bag.checks import run_checks
from hdl_bag.gen import generate
from hdl_bag.ir import dumps, resolve
from hdl_bag.ir.to_workbook import to_workbook
from hdl_bag.reader import read_workbook
from hdl_bag.regress.dsl import AXI
from hdl_bag.regress.generate import check, random_config
from hdl_bag.template.data import Cell, Rows

# A fixed range every host run checks end to end; the nightly draws far more.
SEEDS = range(40)
# The draws for the dimension test (drawing alone is fast).
SPREAD = range(300)


def _errors(seed: int, where: Path) -> list[str]:
    _, result = check(random_config(seed), where)
    return [f.message for f in result.errors]


def test_a_seed_draws_the_same_config_every_time() -> None:
    assert random_config(7).data == random_config(7).data
    assert random_config(7).data != random_config(8).data


def test_the_draw_does_not_depend_on_the_hash_seed() -> None:
    """Python's str hash changes with PYTHONHASHSEED; the draw must not (D97)."""
    code = "from hdl_bag.regress.generate import random_config; print(repr(random_config(11).data))"
    outs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": h}).stdout
        for h in ("0", "1", "12345")
    }  # fmt: skip
    assert len(outs) == 1


@pytest.mark.parametrize("seed", SEEDS)
def test_a_drawn_config_checks_resolves_and_generates(tmp_path: Path, seed: int) -> None:
    """bag check gives no error (warnings are fine, D49), and the IR resolves, generates and
    converts back to a workbook that checks the same (the repro a person opens)."""
    path, result = check(random_config(seed), tmp_path)
    assert [f.message for f in result.errors] == []
    spec = resolve(result)
    generate(spec, tmp_path / "out", tmp_path)
    back = tmp_path / "back"
    back.mkdir()
    to_workbook(spec).save(back / path.name)
    (back / "header.txt").write_text((tmp_path / "header.txt").read_text(encoding="utf-8"))
    again = run_checks(read_workbook(back / path.name), back)
    assert [f.message for f in again.errors] == []
    assert dumps(resolve(again)).split("provenance:")[0] == dumps(spec).split("provenance:")[0]


@settings(
    max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(seed=st.integers(min_value=0, max_value=10**12))
def test_any_seed_draws_a_config_bag_check_accepts(tmp_path: Path, seed: int) -> None:
    assert _errors(seed, tmp_path / str(seed)) == []


def _features(seed: int) -> set[str]:
    """What one drawn config exercises, at workbook level."""
    d = random_config(seed).data
    out: set[str] = {f"domains {len(d.clocks)}", f"style {d.wrapper['Port style']}"}
    fabric = {r[0]: r for r in d.fabrics}
    for r in d.fabrics:
        out |= {f"fabric {r[1]} {r[2]}", f"arbitration {r[6]}"}
        if r[11] == "user RTL":
            out.add("external")
    for r in d.bridges:
        out |= {f"bridge {r[1]}", f"pass-through {r[3]}"}
    links = {r[0]: r[1] for r in d.topology} | {r[1]: r[0] for r in d.topology}
    for kind, rows in (("master", d.masters), ("slave", d.slaves)):
        for r in rows:
            fab = fabric[links[r[0]]]
            if fab[1] in AXI:
                if r[1] != fab[1]:
                    out.add(f"{kind} protocol adapter")
                if r[2] != fab[3]:
                    out.add(f"{kind} width adapter")
                if r[6] != fab[5]:
                    out.add(f"{kind} cdc")
    # A feeder: a fabric besides the root (the first row) that no bridge feeds.
    bridges = {r[0] for r in d.bridges}
    fed = {r[1] for r in d.topology if r[0] in bridges}
    if any(r[0] not in fed for r in d.fabrics[1:]):
        out.add("feeder")
    for r in d.memory_map:
        out.add(f"access {r[4]}")
        if r[5] == "Y":
            out.add("secure")
    for r in d.address_decode:
        out.add(f"miss {r[1]}")
        if r[3] is not None:
            out.add("alias always" if r[6] == "always" else "alias pin")
    out |= {f"rule {r[2]}" for r in d.access_rules}
    out |= {"user ports" for _ in d.user_ports}
    if d.sub_wrappers:
        out.add(f"hierarchy depth {_depth(d.sub_wrappers)}")
    for r in d.port_options:
        if r[1]:
            out.add("port fifo")
        if r[2] != "none":
            out.add("port slice")
        if "tie-0" in r or "tie-default" in r:
            out.add("port tie")
    return out


def _depth(subs: Rows) -> int:
    parent = {r[0]: r[1] for r in subs}

    def depth(s: Cell) -> int:
        return 1 + depth(parent[s]) if s in parent else 0

    return max(depth(s) for s in parent)


def test_the_draws_reach_every_dimension() -> None:
    """Plan PR 5's dimensions: protocol pairs (every bridge template), width ratios and clock
    crossings (adapters), counts, both AXI topologies, maps, aliases, hierarchy depth,
    User_Ports, port options, external fabrics. A dimension the draws never reach would
    leave the nightly blind to it."""
    seen = set().union(*(_features(s) for s in SPREAD))
    wanted = {
        *(f"fabric {p} {t}" for p, t in (("AXI4", "crossbar"), ("AXI4", "shared-bus"),
          ("AXI4-Lite", "crossbar"), ("AXI4-Lite", "shared-bus"), ("AHB-Lite", "single-layer"),
          ("APB4", "decoder-mux"), ("APB3", "decoder-mux"))),
        *(f"bridge bag_{b}" for b in ("ahb2apb", "ahb2axi", "apb2ahb", "apb2axi", "axi2ahb",
                                      "axi2apb", "axi2axil")),
        "pass-through Y", "pass-through N", "domains 1", "domains 2", "domains 3",
        "arbitration round-robin", "arbitration fixed-priority", "arbitration single-master",
        *(f"{k} {a}" for k in ("master", "slave")
          for a in ("protocol adapter", "width adapter", "cdc")),
        "feeder", "access RW", "access RO", "access WO", "secure",
        "miss DECERR", "miss SLVERR", "miss HRESP_ERROR", "miss PSLVERR", "miss default-slave",
        "alias always", "alias pin", "rule RO", "rule WO", "rule -",
        "hierarchy depth 1", "hierarchy depth 2", "hierarchy depth 3",
        "external", "user ports", "style flat", "style sv-interface",
        "port fifo", "port slice", "port tie",
    }  # fmt: skip
    assert wanted - seen == set()
