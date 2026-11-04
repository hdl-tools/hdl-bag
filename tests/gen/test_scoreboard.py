# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The scoreboard's own checks, and that the probes reach what they claim to."""

from pathlib import Path

import pytest

from hdl_bag.ir import load
from hdl_bag.tests_gen.route_model import Outcome, check_read, check_write, probes

TESTS = Path(__file__).parents[1]
CONFIGS = [*sorted((TESTS / "configs").glob("p*/*.ir.yaml")), TESTS / "golden" / "demo_ss.ir.yaml"]
DATA = bytes.fromhex("11223344")
RIGHT = [(0x40, 0x11), (0x41, 0x22), (0x42, 0x33), (0x43, 0x44)]


def test_a_write_stored_exactly_passes() -> None:
    check_write("w", Outcome("s", 0x40), DATA, RIGHT)


@pytest.mark.parametrize(
    "stored",
    [
        RIGHT[:3],  # a byte lost
        [*RIGHT, (0x44, 0x55)],  # a byte beyond the access
        [(0x40, 0x22), (0x41, 0x11), (0x42, 0x33), (0x43, 0x44)],  # lanes swapped
        [(a + 4, v) for a, v in RIGHT],  # the wrong word
        [*RIGHT, RIGHT[0]],  # a byte written twice
    ],
)
def test_a_wrong_write_fails(stored: list[tuple[int, int]]) -> None:
    with pytest.raises(AssertionError):
        check_write("w", Outcome("s", 0x40), DATA, stored)


def test_a_failed_write_may_store_part_but_nothing_else() -> None:
    failed = Outcome("s", 0x40, slave_error=True)
    check_write("w", failed, DATA, RIGHT[:2])
    check_write("w", failed, DATA, [])
    with pytest.raises(AssertionError):
        check_write("w", failed, DATA, [(0x44, 0x55)])


def _read(seen: set[int], words: set[int]) -> None:
    check_read("r", Outcome("s", 0x40, word_bytes=1, words=frozenset(words)), 4, seen)


def test_a_read_must_touch_all_it_needs_and_nothing_beyond_its_cover() -> None:
    need = {0x40, 0x41, 0x42, 0x43}
    _read(need, need)
    _read(need | {0x44}, need | {0x44})  # a wide beat on the way can cover more
    with pytest.raises(AssertionError):
        _read(need - {0x42}, need)
    with pytest.raises(AssertionError):
        _read(need | {0x44}, need)


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.stem)
def test_probes_hit_the_first_and_last_word_of_every_hole(config: Path) -> None:
    spec = load(config)
    addrs = set(probes(spec))
    width = max(m.addr_width for m in spec.masters)
    regions = sorted((r.base, r.base + r.size) for r in spec.memory_map)
    assert {0, (1 << width) - 4} <= addrs
    edge = 0
    for base, end in regions:
        if base > edge:  # a hole [edge, base)
            assert {edge, base - 4} <= addrs, (hex(edge), hex(base))
        edge = max(edge, end)
    if edge < 1 << width:
        assert edge in addrs
