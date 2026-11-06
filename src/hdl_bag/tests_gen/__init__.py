# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generated cocotb tests: which tests apply to a config, and the module that runs them.

``bag generate`` writes ``tests/<project>_tests.py``, the test list for this config, and
``tests/<project>.ir.yaml``, the IR it reads; ``bag sim`` runs them. The tests themselves,
the reference model and the bus models live in this package and need the ``sim`` extra.
Importing this module does not import cocotb.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hdl_bag import __version__
from hdl_bag.gen.render import header
from hdl_bag.ir.model import Spec
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import PROTO_TOKEN, Protocol
from hdl_bag.tests_gen.route_model import (
    blocking_path,
    burst_path,
    burst_shapes,
    contention,
    crossbar_path,
    exokay_paths,
    forced_resp_paths,
    midpoint_path,
    narrow_apb_path,
    plain_word,
    private_words,
    pstrb_path,
    takes_partial_writes,
)

_AXI = ("AXI4", "AXI4-Lite")


def _unaligned_path(spec: Spec) -> bool:
    """An AXI master reaches, without error, a slave that can take a partial write."""
    return any(plain_word(spec, m, s) and takes_partial_writes(spec, s)
               for m in spec.masters if m.protocol in _AXI for s in spec.slaves)  # fmt: skip


def _split_path(spec: Spec) -> bool:
    """A master reaches, without error, a slave narrower than the 32-bit word it sends."""
    return any(plain_word(spec, m, s)
               for m in spec.masters for s in spec.slaves if s.data_width < 32)  # fmt: skip


@dataclass(frozen=True)
class GeneratedTest:
    name: str  # a function in route_tests.py
    timeout_us: int
    # False only when the test could check nothing on this config. Each mirrors the filter
    # at the top of the test, and may be broader.
    applies: Callable[[Spec], bool]


def _plain_bursts(spec: Spec) -> bool:
    for i, m in enumerate(spec.masters):
        words, miss = burst_path(spec, m, i)
        if words or miss is not None:
            return True
    return False


def _any_pair(spec: Spec, path: Callable[[Spec, Any, Any], Any]) -> bool:
    return any(path(spec, m, s) for m in spec.masters for s in spec.slaves)


# Timeouts are a few times the longest run seen (the route test on demo_ss, the largest config,
# stays under 1 ms of simulated time), so a deadlocked design fails within minutes.
TESTS: tuple[GeneratedTest, ...] = (
    GeneratedTest("every_master_reaches_what_the_model_says", 3000, lambda _: True),
    GeneratedTest("arbitration_follows_the_fabric_policy", 50, lambda s: any(
        f.protocol == "AHB-Lite" and contention(s, f, secure_too=True) for f in s.fabrics)),
    GeneratedTest("concurrent_traffic_keeps_every_masters_data", 50, lambda s: any(
        private_words(s, m, i, 8) for i, m in enumerate(s.masters))),
    GeneratedTest("traffic_survives_back_pressure_on_every_channel", 500, lambda s: any(
        private_words(s, m, i, 8) for i, m in enumerate(s.masters))),
    GeneratedTest("reset_during_traffic_leaves_nothing_behind", 1000, lambda s: any(
        private_words(s, m, i, 8) for i, m in enumerate(s.masters))),
    GeneratedTest("traffic_crosses_clock_domains_at_every_ratio", 2000, lambda s: len(s.clocks) >= 2 and any(
        private_words(s, m, i, 8) for i, m in enumerate(s.masters))),
    GeneratedTest("shared_buses_block_behind_a_stalled_slave_and_crossbars_do_not", 100, lambda s: any(
        blocking_path(s, f) for f in s.fabrics)),
    GeneratedTest("slave_decerr_returns_as_the_model_says", 200, lambda s: bool(forced_resp_paths(s))),
    GeneratedTest("exclusive_accesses_carry_lock_and_exokay_unchanged", 100, lambda s: bool(exokay_paths(s))),
    GeneratedTest("axi_arbitration_rounds_starvation_and_lock", 200, lambda s: any(
        f.protocol in _AXI and contention(s, f) for f in s.fabrics)),
    GeneratedTest("axi_shared_bus_midpoint_buffers_reads", 50, lambda s: any(
        midpoint_path(s, f) for f in s.fabrics)),
    GeneratedTest("axi4_bursts_reach_slaves_and_error_paths", 50, _plain_bursts),
    GeneratedTest("axi4_crossbars_admit_max_mst_trans_per_id", 50, lambda s: any(
        crossbar_path(s, f, m) for f in s.fabrics for m in s.masters)),
    GeneratedTest("narrow_apb_behind_axi2apb_keeps_lanes_strobes_and_errors", 200, lambda s: _any_pair(s, narrow_apb_path)),
    GeneratedTest("unaligned_axi_writes_store_only_their_bytes", 200, _unaligned_path),
    GeneratedTest("an_error_on_any_beat_of_a_split_access_reaches_the_master", 200, _split_path),
    GeneratedTest("axi4_burst_shapes_match_the_model", 3000, lambda s: _any_pair(s, burst_shapes)),
    GeneratedTest("apb_byte_writes_carry_pstrb", 50, lambda s: _any_pair(s, pstrb_path)),
    GeneratedTest("axi_ports_take_every_handshake_ordering", 3000, lambda s: any(
        x.protocol in _AXI for x in s.masters) or any(x.protocol in _AXI for x in s.slaves)),
)  # fmt: skip


# The test the READY shim runs (D38): all masters at once, under back-pressure.
SHIM_TEST = "traffic_survives_back_pressure_on_every_channel"


def ready_shim_module(top: str) -> str:
    return f"{top}_ready_shim"


def render_ready_shim(spec: Spec, top: str, top_text: str, user_header: str | None) -> str | None:
    """A module with the ports of the flat top TOP, whose every AXI READY into the design is
    driven combinationally from that channel's VALID (D38). A design that holds a VALID until it
    sees READY then has a combinational loop, which does not settle in simulation. The models
    drive the ports as usual and never see a difference. None if the design has no AXI port, or
    its test does not apply."""
    if SHIM_TEST not in applicable_tests(spec):
        return None
    pairs = []
    for name, protocol, channels in (
        *((m.name, m.protocol, ("b", "r")) for m in spec.masters),
        *((s.name, s.protocol, ("aw", "w", "ar")) for s in spec.slaves),
    ):
        if protocol not in _AXI:
            continue
        prefix = next((p.prefix for p in spec.wrapper.ports if p.port == name), None)
        token = PROTO_TOKEN[Protocol(protocol)]
        for ch in channels:
            signal = [flat_signal_name(spec.wrapper.signal_pattern, name, token, f"{ch}{x}", prefix)
                      for x in ("valid", "ready")]  # fmt: skip
            pairs.append((signal[0], signal[1]))
    if not pairs:
        return None
    match = re.search(rf"^module {re.escape(top)} \((.*?)^\);", top_text, re.M | re.S)
    assert match, top
    conns = ",\n".join(f"      .{ready}({valid} & {ready})" for valid, ready in pairs)
    return (
        header(spec, user_header)
        + f"\nmodule {ready_shim_module(top)} ({match[1]});\n\n"
        + f"  {top} i_dut (\n      .*,\n{conns}\n  );\n\nendmodule\n"
    )


def applicable_tests(spec: Spec) -> list[str]:
    """The tests that apply to SPEC, in the order they run."""
    return [t.name for t in TESTS if t.applies(spec)]


def render_tests(spec: Spec, user_header: str | None) -> str:
    """The generated cocotb module: the banner, then one test per entry of TESTS that applies."""
    # First, so a user's text on the next line cannot be read as an encoding declaration.
    head = "# coding: utf-8\n"
    if user_header:  # verbatim, as comments: it is usually written for SystemVerilog
        head += "".join(f"# {line}".rstrip() + "\n" for line in user_header.splitlines())
    lines = [
        head + header(spec, None, "#").rstrip("\n"),
        f'"""cocotb tests for {spec.project.name}: `bag sim <output directory>` runs them."""',
        "",
        "from pathlib import Path",
        "from typing import Any",
        "",
        "import cocotb",
        "",
        "from hdl_bag.tests_gen import route_tests",
        "",
        f'route_tests.load_spec(Path(__file__).with_name("{spec.project.name}.ir.yaml"), '
        f'generated_by="{__version__}")',
    ]
    for t in TESTS:
        if t.applies(spec):
            lines += [
                "",
                "",
                f'@cocotb.test(timeout_time={t.timeout_us}, timeout_unit="us")',
                f"async def {t.name}(dut: Any) -> None:",
                f"    await route_tests.{t.name}(dut)",
            ]
    return "\n".join(lines) + "\n"
