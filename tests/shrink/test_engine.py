# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The greedy loop of `bag shrink`, driven by stub oracles: no workbook, no EDA tool."""

from pathlib import Path

from hdl_bag.ir import load
from hdl_bag.ir.to_workbook import to_spec_data
from hdl_bag.shrink.engine import shrink
from hdl_bag.template.data import SpecData

MULTI = Path(__file__).parents[1] / "configs" / "p2" / "multi.ir.yaml"


def multi() -> SpecData:
    return to_spec_data(load(MULTI))


def has_master(data: SpecData, name: str) -> bool:
    return any(r[0] == name for r in data.masters)


def test_everything_but_the_culprit_master_is_removed() -> None:
    data = multi()
    data.masters[0][0] = "m_a"
    data.masters[1][0] = "m_b"
    data.masters[2][0] = "m_c"
    data.topology = [[("m_a", "m_b", "m_c")[i], "ahb_m", i, None, None] for i in range(3)]
    data.topology += [["ahb_m", "ram", 0, None, None]]
    result = shrink(data, lambda d: has_master(d, "m_b"))
    assert [r[0] for r in result.data.masters] == ["m_b"]
    assert result.kept[:2] == ("drop fabric ahb_s and its ports", "drop fabric p3 and its ports")
    assert "drop master m_a" in result.kept and "drop master m_c" in result.kept
    assert "drop master m_b" not in result.kept
    assert not result.exhausted


def test_an_invalid_candidate_is_skipped_and_not_counted_as_a_run() -> None:
    seen: list[int] = []

    def oracle(d: SpecData) -> bool | None:
        seen.append(len(d.masters))
        return None

    result = shrink(multi(), oracle)
    assert result.runs == 0 and result.invalid == len(seen) > 0
    assert result.kept == ()


def test_an_accepted_edit_retries_the_same_index() -> None:
    """After a master drop is accepted the rows shift up, so index 0 is tried again: it is the
    next master, not the one after it, that is asked about."""
    asked: list[str] = []

    def oracle(d: SpecData) -> bool | None:
        intact = len(d.slaves) == 5 and len(d.fabrics) == 4 and len(d.bridges) == 2
        if intact and len(d.masters) < 3:
            asked.append(",".join(str(r[0]) for r in d.masters))
        return intact

    result = shrink(multi(), oracle)
    assert result.kept[:3] == ("drop master aux", "drop master cpu", "drop master dma")
    assert asked[:3] == ["cpu,dma", "dma", ""]


def test_passes_repeat_until_one_makes_no_progress() -> None:
    """dma may only go once ram's port_options row is gone, and that row is dropped by a later
    kind than the masters, so dma is dropped by the second pass."""

    def oracle(d: SpecData) -> bool | None:
        if not any(r[0] == "ram" for r in d.slaves):
            return False
        return has_master(d, "dma") or not any(r[0] == "ram" for r in d.port_options)

    result = shrink(multi(), oracle)
    row = result.kept.index("drop port_options row 0 (ram)")
    assert result.kept.index("drop master dma") > row
    assert not has_master(result.data, "dma") and not result.exhausted


def test_the_budget_stops_the_search_and_says_so() -> None:
    result = shrink(multi(), lambda d: False, budget=3)
    assert result.exhausted is True
    assert result.runs == 3
    assert result.kept == ()


def test_the_same_input_gives_the_same_labels_and_result() -> None:
    def oracle(d: SpecData) -> bool | None:
        return has_master(d, "cpu")

    one, two = shrink(multi(), oracle), shrink(multi(), oracle)
    assert one == two
    assert one.kept
