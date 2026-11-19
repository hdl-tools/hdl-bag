# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""`bag shrink`: delta-debug a failing spec down to a minimal repro.

`reductions` lists the edits that can be made to a `SpecData`; `engine` applies them greedily
for as long as an oracle says the original failure still reproduces.
"""

from hdl_bag.shrink.engine import Oracle, Shrunk, shrink
from hdl_bag.shrink.evaluate import (
    NoFailure,
    ShrinkError,
    Signature,
    Step,
    ToolUnavailable,
    build,
    discover,
    formal_step,
    lint_step,
    make_oracle,
    sim_step,
)
from hdl_bag.shrink.reductions import KINDS, Edit, Kind, drop_names, fabric_ports

__all__ = [
    "KINDS", "Edit", "Kind", "NoFailure", "Oracle", "ShrinkError", "Shrunk", "Signature", "Step",
    "ToolUnavailable", "build", "discover", "drop_names", "fabric_ports", "formal_step",
    "lint_step", "make_oracle", "shrink", "sim_step",
]  # fmt: skip
