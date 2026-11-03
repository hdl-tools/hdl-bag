# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""One passing and one failing test, so tests/gen/test_tests_gen.py can see a failure reported."""

from typing import Any

import cocotb


@cocotb.test()
async def this_one_passes(dut: Any) -> None:
    pass


@cocotb.test()
async def this_one_fails(dut: Any) -> None:
    raise AssertionError("on purpose")
