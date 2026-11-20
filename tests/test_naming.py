# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Tests for the names shared by the checks and the generators."""

import pytest

from hdl_bag.naming import unit_name


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [("", "bag_sync"), (None, "bag_sync"), ("p", "p_bag_sync")],
)
def test_unit_name_joins_a_prefix_with_an_underscore_and_leaves_a_blank_one_alone(
    prefix: str | None, expected: str
) -> None:
    assert unit_name(prefix, "bag_sync") == expected
