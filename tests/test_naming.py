# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Tests for the names shared by the checks and the generators."""

import pytest

from hdl_bag.naming import emitted_units, unit_name


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [("", "bag_sync"), (None, "bag_sync"), ("p", "p_bag_sync")],
)
def test_unit_name_joins_a_prefix_with_an_underscore_and_leaves_a_blank_one_alone(
    prefix: str | None, expected: str
) -> None:
    assert unit_name(prefix, "bag_sync") == expected


def test_emitted_units_cover_every_source_and_call_an_interface_an_interface() -> None:
    units = emitted_units()
    assert units["AXI_BUS"] == "a vendored PULP interface"
    assert units["axi_mux"] == "a vendored PULP module"
    assert units["bag_axi_mux"] == "a module BAG derives from PULP"
    assert units["bag_sync"] == "an in-house module"
