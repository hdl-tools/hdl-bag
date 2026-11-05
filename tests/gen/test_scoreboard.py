# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The scoreboard's own checks, and that the probes reach what they claim to."""

from pathlib import Path

import pytest

from hdl_bag.ir import load
from hdl_bag.tests_gen.route_model import (
    FIXED,
    INCR,
    WRAP,
    Outcome,
    burst_addresses,
    burst_bytes,
    check_burst_write,
    check_read,
    check_split_write_error,
    check_write,
    probes,
)

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
    failed = Outcome("s", 0x40, slave_error=True, hits_error_word=True)
    check_write("w", failed, DATA, RIGHT[:2])
    check_write("w", failed, DATA, [])
    with pytest.raises(AssertionError):
        check_write("w", failed, DATA, [(0x44, 0x55)])


def test_a_masked_write_must_still_store_unless_it_hit_an_error_word() -> None:
    masked = Outcome("s", 0x40, masked=True)
    with pytest.raises(AssertionError):
        check_write("w", masked, DATA, [])
    check_write("w", Outcome("s", 0x40, masked=True, hits_error_word=True), DATA, [])


def _split(failed: int, stored: list[tuple[int, int]]) -> None:
    check_split_write_error("w", 0x40, DATA, 1, failed, stored)


def test_a_split_write_error_keeps_the_beats_before_it() -> None:
    _split(0x42, RIGHT[:2])  # stopped at the error
    _split(0x42, [*RIGHT[:2], RIGHT[3]])  # carried on past it
    _split(0x40, [])


@pytest.mark.parametrize(
    ("failed", "stored"),
    [
        (0x42, RIGHT[:1]),  # a beat before the error lost
        (0x42, RIGHT[:3]),  # the failed beat stored
        (0x42, [*RIGHT[:2], (0x44, 0x55)]),  # a byte beyond the access
    ],
)
def test_a_wrong_split_write_error_fails(failed: int, stored: list[tuple[int, int]]) -> None:
    with pytest.raises(AssertionError):
        _split(failed, stored)


def _read(touched: list[int], words: set[int]) -> None:
    check_read("r", Outcome("s", 0x40, word_bytes=1, words=frozenset(words)), 4, touched)


def test_a_read_must_touch_all_it_needs_once_and_nothing_beyond_its_cover() -> None:
    need = [0x40, 0x41, 0x42, 0x43]
    _read(need, set(need))
    _read([*need, 0x44], {*need, 0x44})  # a wide beat on the way can cover more
    with pytest.raises(AssertionError):
        _read(need[:2], set(need))
    with pytest.raises(AssertionError):
        _read([*need, 0x44], set(need))
    with pytest.raises(AssertionError):
        _read([*need, 0x40], set(need))  # read twice


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.stem)
def test_probes_hit_the_first_and_last_word_of_every_hole(config: Path) -> None:
    spec = load(config)
    addrs = set(probes(spec))
    width = max(m.addr_width for m in spec.masters)
    assert {0, (1 << width) - 4} <= addrs
    for fabric in spec.fabrics:
        edge = 0
        for base, end in sorted(
            (r.base, r.base + r.size) for r in spec.memory_map if r.fabric == fabric.name
        ):
            if base > edge:  # a hole [edge, base) in this fabric's map
                assert {edge, base - 4} <= addrs, (fabric.name, hex(edge), hex(base))
            edge = max(edge, end)
        if edge < 1 << width:
            assert edge in addrs, (fabric.name, hex(edge))


@pytest.mark.parametrize(
    ("addr", "size", "beats", "burst", "want"),
    [
        (0x40, 2, 4, INCR, [0x40, 0x44, 0x48, 0x4C]),
        (0x41, 2, 3, INCR, [0x41, 0x44, 0x48]),  # the first beat keeps its unaligned address
        (0x40, 2, 3, FIXED, [0x40, 0x40, 0x40]),
        (0x48, 2, 4, WRAP, [0x48, 0x4C, 0x40, 0x44]),  # wraps at the 16-byte block
        (0x44, 2, 2, WRAP, [0x44, 0x40]),
        (0x7E, 1, 8, WRAP, [0x7E, 0x70, 0x72, 0x74, 0x76, 0x78, 0x7A, 0x7C]),
    ],
)
def test_burst_addresses(addr: int, size: int, beats: int, burst: int, want: list[int]) -> None:
    assert burst_addresses(addr, size, beats, burst) == want


def test_a_wrap_burst_must_be_aligned_and_a_legal_length() -> None:
    with pytest.raises(AssertionError):
        burst_addresses(0x41, 2, 4, WRAP)
    with pytest.raises(AssertionError):
        burst_addresses(0x40, 2, 3, WRAP)


def test_a_fixed_burst_writes_the_same_bytes_again() -> None:
    want = burst_bytes(0x40, 0, 2, FIXED, bytes([1, 2]))
    assert want == [(0x40, 1), (0x40, 2)]
    check_burst_write("w", want, [(0x40, 1), (0x40, 2)])
    with pytest.raises(AssertionError):
        check_burst_write("w", want, [(0x40, 2)])  # a beat lost
    with pytest.raises(AssertionError):
        check_burst_write("w", want, [(0x40, 1), (0x41, 2)])  # it moved on
