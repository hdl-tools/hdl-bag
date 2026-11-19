# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""What a failed generated test reports. The nightly keeps only the findings of a seed, so a
finding must say why the test failed (first nightly, run 44188540781: "test X failed" on 32
seeds, and each diagnosis needed a rerun)."""

import xml.etree.ElementTree as ET

from hdl_bag.flows.sim import MESSAGE_LIMIT, _findings


def _case(name: str, kind: str, message: str | None) -> ET.Element:
    case = ET.Element("testcase", {"name": name})
    attrs = {} if message is None else {"message": message}
    ET.SubElement(case, kind, attrs)
    return case


def _messages(*cases: ET.Element) -> list[str]:
    names = [str(c.get("name")) for c in cases]
    return [f.message for f in _findings(list(cases), names, 7)]


def test_a_failed_test_says_why_on_one_line() -> None:
    message = "m0 W 0x40: stored 1 byte, wrote 4\nassert {64: 82} == {64: 82, 65: 49}"
    assert _messages(_case("routes", "failure", message)) == [
        "routes failed: m0 W 0x40: stored 1 byte, wrote 4 (seed 7)"
    ]


def test_a_test_that_errored_says_why_too() -> None:
    assert _messages(_case("routes", "error", "TimeoutError: no response")) == [
        "routes failed: TimeoutError: no response (seed 7)"
    ]


def test_a_long_reason_is_cut_to_the_limit() -> None:
    got = _messages(_case("routes", "failure", "x" * (MESSAGE_LIMIT + 50)))
    assert got == [f"routes failed: {'x' * MESSAGE_LIMIT}... (seed 7)"]


def test_a_failure_without_a_message_still_reads_as_failed() -> None:
    assert _messages(_case("routes", "failure", None)) == ["routes failed (seed 7)"]
    assert _messages(_case("routes", "failure", "  \n")) == ["routes failed (seed 7)"]
