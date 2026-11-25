# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""`bag shrink`: delta-debug a failing spec down to a minimal repro.

`reductions` lists the edits that can be made to a `SpecData`; `engine` applies them greedily
for as long as an oracle says the original failure still reproduces.

shrink.yml runs this package from main over an older commit's code (#219), so it may only use
names the rest of hdl_bag has had since 51c66b7, and must keep what that commit's cli.py
imports from it, with the same signatures; the job's shrinker input pins another ref.
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
