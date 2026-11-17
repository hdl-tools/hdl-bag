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
from hdl_bag.regress.dsl import APB3_NA, AXI, AXI4_NA, AXIL_NA, OPTION_COLUMNS, Cfg
from hdl_bag.regress.generate import check, random_config
from hdl_bag.regress.space import dimensions
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
    """Python's str hash changes with PYTHONHASHSEED; the draw must not (D97). Seeds 2 and 15
    draw bridges, 11 none."""
    code = ("from hdl_bag.regress.generate import random_config\n"
            "for s in (2, 11, 15): print(repr(random_config(s).data))")  # fmt: skip
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
        out |= {f"tie {c}" for c, v in zip(OPTION_COLUMNS[2:], r[3:], strict=False)
                if v in ("tie-0", "tie-default")}  # fmt: skip
    out.add(f"bridge chain {_chain(d.bridges, d.topology)}")
    return out


def _chain(bridges: Rows, topology: Rows) -> int:
    """The most bridges on one path from fabric to fabric."""
    names = {str(r[0]) for r in bridges}
    up = {str(r[1]): str(r[0]) for r in topology if r[1] in names}  # bridge -> upstream
    down = {str(r[0]): str(r[1]) for r in topology if r[0] in names}  # bridge -> downstream
    into = {down[b]: b for b in names}  # a fabric -> one bridge feeding it

    def depth(b: str, seen: frozenset[str] = frozenset()) -> int:
        parent = into.get(up[b])
        return 1 + (depth(parent, seen | {b}) if parent and parent not in seen else 0)

    return max((depth(b) for b in names), default=0)


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
        "port fifo", "port slice", "tie qos", "tie region", "tie cache", "tie lock",
        "tie pstrb", "tie pslverr", "bridge chain 3", "bridge chain 4",
    }  # fmt: skip
    assert wanted - seen == set()


def test_deep_bridge_chains_are_not_rare() -> None:
    """Verification review G4: with a uniform parent, chains of three bridges came up in
    under 5% of draws. Growing behind the newest fabric half the time keeps them common."""
    deep = sum(any(f in _features(s) for f in ("bridge chain 3", "bridge chain 4"))
               for s in SPREAD)  # fmt: skip
    assert deep >= len(SPREAD) // 10


def test_dense_and_sparse_maps_are_both_common(tmp_path: Path) -> None:
    """Verification review G3: shrinking every region made 90% of maps sparse. The map is
    now drawn once per config; both kinds come up in a third of the draws or more, measured
    with the coverage report's own dimensions."""
    maps = []
    for seed in SEEDS:
        _, result = check(random_config(seed), tmp_path / str(seed))
        maps.append(dimensions(resolve(result))["map"])
    assert min(maps.count("dense"), maps.count("sparse")) >= len(SEEDS) // 3


def _small(name: str) -> Cfg:
    c = Cfg(name, phase=5)
    c.domain("D0", "clk_0", "rst_0_n")
    return c


def test_the_rules_the_generator_restates_still_hold(tmp_path: Path) -> None:
    """The generator steers clear of three refusals, restating rules that live in the checks
    and in bag generate (verification review G5). Each refusal still stands, so a change to
    one of those rules shows here rather than as a blind spot in the nightly."""
    # PULP's crossbars arbitrate round robin only (FAB-ARBITRATION).
    c = _small("r_arb")
    c.fabric("x", "AXI4", 32, 32, "D0", "fixed-priority", latency="CUT_ALL_AX")
    for k in range(2):
        c.master(f"m{k}", "AXI4", 32, 32, "D0").link(f"m{k}", "x", k, k).decode(f"m{k}", "DECERR")
        c.option(f"m{k}", **AXI4_NA)
    c.slave("s0", "AXI4", 32, 32, "D0").link("x", "s0", 0).region("s0", "r0", 0x4000_0000, 0x1000)
    c.option("s0", **AXI4_NA)
    _, result = check(c, tmp_path / "arb")
    assert [f.rule for f in result.errors] == ["FAB-ARBITRATION"]
    # A secure region reachable from APB3, which has no PPROT (SEC-PROT-TIED).
    c = _small("r_prot")
    c.fabric("p", "APB3", 32, 32, "D0").master("m0", "APB3", 32, 32, "D0").link("m0", "p", 0)
    c.slave("s0", "APB3", 32, 12, "D0").link("p", "s0", 0)
    c.region("s0", "r0", 0x4000_0000, 0x1000, secure=True).decode("m0", "PSLVERR")
    c.option("m0", **APB3_NA).option("s0", **APB3_NA)
    _, result = check(c, tmp_path / "prot")
    assert [f.rule for f in result.errors] == ["SEC-PROT-TIED"]
    # A direct master beside a pass-through N bridge on one fabric (DEC-MIXED-TRANSLATION;
    # bag generate refuses it too, gen/decode.py fabric_offset).
    c = _small("r_offset")
    c.fabric("a", "AXI4", 32, 32, "D0", "round-robin")
    c.fabric("b", "AXI4-Lite", 32, 32, "D0", "round-robin")
    c.master("m0", "AXI4", 32, 32, "D0").link("m0", "a", 0).decode("m0", "DECERR")
    c.master("m1", "AXI4-Lite", 32, 32, "D0").link("m1", "b", 1).decode("m1", "DECERR")
    c.slave("s0", "AXI4-Lite", 32, 32, "D0").link("b", "s0", 0)
    c.bridge("br", 0x4000_0000, pass_through=False, template="bag_axi2axil")
    c.link("a", "br", 0).link("br", "b", 0).decode("br", "DECERR")
    c.region("br", "win", 0x4000_0000, 0x1_0000).region("s0", "r0", 0x4000_0000, 0x1000)
    c.option("m0", **AXI4_NA)
    for port in ("m1", "s0", "br"):
        c.option(port, **AXIL_NA)
    _, result = check(c, tmp_path / "offset")
    assert [f.rule for f in result.errors] == ["DEC-MIXED-TRANSLATION"]


def test_the_file_header_is_systemverilog_comments() -> None:
    """The File header goes into every generated file as written; a plain-text header made
    every random seed fail lint in CI (dispatch 44020916215)."""
    from hdl_bag.regress.generate import HEADER

    assert HEADER.splitlines() and all(line.startswith("//") for line in HEADER.splitlines())
