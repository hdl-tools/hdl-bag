# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The pure edits of `bag shrink`, on the `multi` corpus config: three AHB masters over four
fabrics (two AHB, two APB behind bridges), with a rule, an alias and a miss-mode decode."""

import copy
from pathlib import Path

from hdl_bag.checks import run_checks
from hdl_bag.ir import load
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.reader import read_workbook
from hdl_bag.shrink.reductions import KINDS, Edit, drop_names, fabric_ports
from hdl_bag.template.builder import build_workbook
from hdl_bag.template.data import SpecData

MULTI = Path(__file__).parents[1] / "configs" / "p2" / "multi.ir.yaml"


def multi() -> SpecData:
    return to_spec_data(load(MULTI))


def labels_and_edits(data: SpecData, kind_name: str) -> list[Edit]:
    return next(k for k in KINDS if k.__name__ == kind_name)(data)


def labels(data: SpecData, kind_name: str) -> list[str]:
    return [label for label, _ in labels_and_edits(data, kind_name)]


def test_dropping_a_master_removes_every_row_that_names_it() -> None:
    data = multi()
    drop_names(data, frozenset({"dma"}))
    assert [r[0] for r in data.masters] == ["aux", "cpu"]
    assert data.topology == [
        ["cpu", "ahb_m", 0, None, None],
        ["ahb_m", "ram", 0, None, None],
        ["ahb_m", "br_y", 1, None, None],
        ["ahb_m", "br_n", 2, None, None],
        ["aux", "ahb_s", 0, None, None],
        ["ahb_s", "aux_ram", 0, None, None],
        ["br_n", "p3", 0, None, None],
        ["p3", "legacy", 0, None, None],
        ["br_y", "p4", 0, None, None],
        ["p4", "uart", 0, None, None],
        ["p4", "spi", 1, None, None],
    ]
    assert data.access_rules == []  # its only rule was dma -> br_n
    assert [r[0] for r in data.address_decode] == ["cpu", "aux", "br_n", "br_y"]
    assert [r[0] for r in data.port_options if r[0] in {"dma", "cpu"}] == ["cpu"]
    assert [r[0] for r in data.exposed_ports] == [
        "aux", "aux_ram", "br_n", "br_y", "cpu", "legacy", "ram", "spi", "uart",
    ]  # fmt: skip


def test_dropping_a_slave_removes_its_fabric_first_topology_row() -> None:
    """A slave's row is [fabric, slave, ...], so it is the second column that names it."""
    data = multi()
    drop_names(data, frozenset({"uart"}))
    assert ["p4", "uart", 0, None, None] not in data.topology
    assert ["p4", "spi", 0, None, None] in data.topology  # renumbered into the freed index
    assert [r[0] for r in data.slaves] == ["aux_ram", "legacy", "ram", "spi"]
    assert [r for r in data.memory_map if r[0] == "uart"] == []


def test_topology_indices_are_renumbered_densely_per_fabric_and_side() -> None:
    """Without it, dropping index 0 of three leaves 1 and 2 in a group of two, and bag check
    reports TOPO-INDEX-OUT-OF-RANGE for nearly every candidate."""
    data = multi()
    drop_names(data, frozenset({"ram"}))  # index 0 of ahb_m's three slave ports
    assert [r for r in data.topology if r[0] == "ahb_m"] == [
        ["ahb_m", "br_y", 0, None, None],
        ["ahb_m", "br_n", 1, None, None],
    ]
    assert [r[2] for r in data.topology if r[1] == "ahb_m"] == [0, 1]  # masters untouched


def test_a_fabric_subtree_takes_its_masters_and_slaves_from_both_topology_columns() -> None:
    data = multi()
    assert fabric_ports(data, "ahb_m") == ["cpu", "dma", "ram", "br_y", "br_n"]
    assert fabric_ports(data, "p4") == ["br_y", "uart", "spi"]
    edit = dict(labels_and_edits(data, "_fabric_subtrees"))["drop fabric p3 and its ports"]
    edit(data)
    assert [r[0] for r in data.fabrics] == ["ahb_m", "ahb_s", "p4"]
    assert [r[0] for r in data.slaves] == ["aux_ram", "ram", "spi", "uart"]  # legacy went
    assert [r[0] for r in data.bridges] == ["br_y"]  # br_n is p3's master port


def test_dropping_a_sub_wrapper_reparents_its_children_and_unplaces_its_instances() -> None:
    data = multi()
    data.sub_wrappers = [["ss0", "multi_top", None], ["ss1", "ss0", None], ["ss2", "ss1", None]]
    data.placement = [["ahb_m", "ss1", None], ["p3", "ss2", None]]
    drop_names(data, frozenset({"ss1"}))
    assert data.sub_wrappers == [["ss0", "multi_top", None], ["ss2", "ss0", None]]
    assert data.placement == [["p3", "ss2", None]]


def test_a_dropped_default_slave_is_left_for_bag_check_to_reject() -> None:
    """The miss mode needs a protocol-specific error code, so the decode row is untouched."""
    data = multi()
    data.address_decode[0][1:3] = ["default-slave", "ram"]
    row = list(data.address_decode[0])
    drop_names(data, frozenset({"ram"}))
    assert data.address_decode[0] == row


def test_width_narrowing_goes_one_ladder_step_per_edit() -> None:
    data = multi()
    data.slaves[0][2] = 64
    data.slaves[0][3] = 32
    first = [lab for lab in labels(data, "_narrow") if "aux_ram" in lab]
    assert first == ["narrow data width of aux_ram to 32", "narrow address width of aux_ram to 24"]
    edits = dict(labels_and_edits(data, "_narrow"))
    edits["narrow data width of aux_ram to 32"](data)
    edits["narrow address width of aux_ram to 24"](data)
    assert data.slaves[0][2:4] == [32, 24]
    assert "narrow data width of aux_ram to 16" in labels(data, "_narrow")
    data.masters[0][4:9] = [8, 0, "D_S", 4, 1]
    assert "narrow id width of aux to 1" in labels(data, "_narrow")
    assert "narrow max reads of aux to 1" in labels(data, "_narrow")
    assert "narrow max writes of aux to 1" not in labels(data, "_narrow")  # already 1


def test_equalising_sets_a_port_to_its_fabrics_width_and_domain() -> None:
    data = multi()
    data.masters[0][2] = 64  # aux on ahb_s: 32 bits, D_S
    data.masters[0][6] = "D_M"
    edits = dict(labels_and_edits(data, "_equalise"))
    assert list(edits) == ["set data width of master aux to 32", "set domain of master aux to D_S"]
    for edit in edits.values():
        edit(data)
    assert data.masters[0][2] == 32
    assert data.masters[0][6] == "D_S"
    assert labels(data, "_equalise") == []


def test_every_kind_lists_its_edits_in_row_order_and_the_same_way_twice() -> None:
    data = multi()
    before = copy.deepcopy(data)
    for kind in KINDS:
        assert [lab for lab, _ in kind(data)] == [lab for lab, _ in kind(copy.deepcopy(data))]
    assert data == before  # listing edits never mutates
    assert labels(data, "_rows")[:3] == [
        "drop access_rules row 0 (dma)",
        "drop memory_map row 0 (ram)",
        "drop memory_map row 1 (br_y)",
    ]


def test_a_dropped_master_candidate_passes_bag_check(tmp_path: Path) -> None:
    """The proof that the cascade and the renumbering make candidates valid. If it fails, the
    cascade is wrong."""
    for dropped in ("cpu", "dma"):  # the first and the last port of their fabric
        data = multi()
        data.wrapper["File header"] = None
        drop_names(data, frozenset({dropped}))
        path = tmp_path / f"{dropped}.xlsx"
        build_workbook(data=data).save(path)
        result = run_checks(read_workbook(path), tmp_path)
        assert [f.message for f in result.errors] == []
