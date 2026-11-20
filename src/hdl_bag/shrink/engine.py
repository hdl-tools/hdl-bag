# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The greedy fixed-point loop of `bag shrink`.

The engine knows nothing about EDA tools or workbooks: the caller supplies an oracle that
decides whether a candidate still shows the original failure. Everything here is
deterministic: edits come in a fixed order and nothing is random, unordered or parallel.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass

from hdl_bag.shrink.reductions import KINDS
from hdl_bag.template.data import SpecData

# True: the candidate reproduces the original failure. False: the step ran and did not.
# None: the candidate is invalid (it failed `bag check` and so on) and no step was run.
Oracle = Callable[[SpecData], bool | None]


@dataclass(frozen=True)
class Shrunk:
    data: SpecData
    kept: tuple[str, ...]  # labels of the accepted edits, in order
    runs: int  # oracle answers that were True or False
    invalid: int  # oracle answers that were None
    passes: int  # passes started, the last of which accepted nothing unless exhausted
    exhausted: bool  # a full pass did not finish within the budget: maybe not minimal


def shrink(
    data: SpecData,
    oracle: Oracle,
    *,
    budget: int | None = None,
    log: Callable[[str], None] = lambda _line: None,
) -> Shrunk:
    """Apply edits while ORACLE keeps answering True, until a full pass accepts nothing.

    BUDGET caps the oracle answers that cost a real step (invalid candidates are free).
    The input DATA is never modified.
    """
    current = copy.deepcopy(data)
    kept: list[str] = []
    runs = invalid = passes = 0
    while True:
        passes += 1
        accepted = len(kept)
        for kind in KINDS:
            i = 0
            while True:
                edits = kind(current)  # recomputed: an accepted edit shifts the rows beneath it
                if i >= len(edits):
                    break
                if budget is not None and runs >= budget:
                    return Shrunk(current, tuple(kept), runs, invalid, passes, exhausted=True)
                label, apply = edits[i]
                candidate = copy.deepcopy(current)
                apply(candidate)
                verdict = oracle(candidate)
                if verdict is None:
                    invalid += 1
                    log(f"invalid  {label}")
                    i += 1
                    continue
                runs += 1
                if verdict:
                    log(f"kept     {label}")
                    current = candidate
                    kept.append(label)  # same i: the next edit now sits at this index
                else:
                    log(f"rejected {label}")
                    i += 1
        if len(kept) == accepted:
            return Shrunk(current, tuple(kept), runs, invalid, passes, exhausted=False)
