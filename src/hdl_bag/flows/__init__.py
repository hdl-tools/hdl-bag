# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Tool flows (lint, compile, sim and formal) behind a backend-neutral interface."""

from hdl_bag.flows.base import Finding, Report
from hdl_bag.flows.compile import compile_design
from hdl_bag.flows.formal import prove, run_formal, run_sby
from hdl_bag.flows.lint import lint
from hdl_bag.flows.sim import SimUnavailable, run_tests, simulate
from hdl_bag.flows.waivers import Waiver, WaiverError, load_waivers

__all__ = [
    "Finding",
    "Report",
    "SimUnavailable",
    "Waiver",
    "WaiverError",
    "compile_design",
    "lint",
    "load_waivers",
    "prove",
    "run_formal",
    "run_sby",
    "run_tests",
    "simulate",
]
