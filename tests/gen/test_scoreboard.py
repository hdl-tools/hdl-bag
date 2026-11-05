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
    burst_refused,
    burst_shapes,
    check_burst_write,
    check_read,
    check_split_write_error,
    check_write,
    probes,
    route,
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


def _refusal(config: str, master: str, slave: str, burst: int) -> bool:
    spec = load(TESTS / "configs" / "p3" / f"{config}.ir.yaml")
    m = next(x for x in spec.masters if x.name == master)
    s = next(x for x in spec.slaves if x.name == slave)
    addr = next(a for _, a, b, _, _ in burst_shapes(spec, m, s) if b == burst)
    beats = next(n for _, _, b, _, n in burst_shapes(spec, m, s) if b == burst)
    return burst_refused(spec, m, route(spec, m.name, addr, True, False, {}), burst, beats)


@pytest.mark.parametrize(
    ("slave", "fixed", "wrap", "why"),
    [
        ("ram", False, False, "AXI4 slave at the fabric's width: no converter"),
        ("s_a8", True, True, "bag_axi2apb narrows 32 to 8"),
        ("s_a16", True, True, "bag_axi2apb narrows 32 to 16"),
        ("s_l8", False, True, "bag_axi2axil at equal widths: splits FIXED, cannot split WRAP"),
        ("s_c8", False, True, "bag_axi2axil widens 32 to 64: FIXED passes the upsizer"),
    ],
)
def test_which_bursts_a_path_refuses(slave: str, fixed: bool, wrap: bool, why: str) -> None:
    assert _refusal("mixed_narrow_apb", "cpu", slave, FIXED) is fixed, why
    assert _refusal("mixed_narrow_apb", "cpu", slave, WRAP) is wrap, why


def test_the_corpus_exercises_every_burst_shape_both_ways() -> None:
    """A config edit or a stricter burst_shapes cannot silently drop a kind of burst, or leave
    WRAP and FIXED tested only as refused, or only as passing."""
    seen: set[tuple[str, bool]] = set()
    for config in CONFIGS:
        spec = load(config)
        for m in spec.masters:
            for s in spec.slaves:
                shapes = burst_shapes(spec, m, s)
                if m.protocol == "AXI4" and shapes:
                    assert "incr" in {x[0] for x in shapes}, (config.stem, m.name, s.name)
                for name, addr, burst, _, beats in shapes:
                    exp = route(spec, m.name, addr, True, False, {})
                    seen.add((name, burst_refused(spec, m, exp, burst, beats)))
    assert seen == {
        ("incr", False), ("incr-long", False), ("4k-edge", False),
        ("narrow-1", False), ("narrow-2", False), ("narrow-4", False),
        ("fixed", False), ("fixed", True), ("wrap", False), ("wrap", True),
    }  # fmt: skip
