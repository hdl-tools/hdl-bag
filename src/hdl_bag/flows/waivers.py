# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Lint waivers: versioned YAML, one entry per waived site, each with a reason.

A waiver file holds a list of entries::

    - rule: UNUSEDSIGNAL          # tool rule ID, exact
      file: rtl/inhouse/bag_*.sv  # glob on the run-relative posix path
      match: "Signal is not used: 'hprot'"   # optional regex on the message
      line: 42                    # optional
      reason: HPROT is decoded only when the PROT column is driven.

A waiver whose file glob matches a linted file but which waives nothing is reported as
``WAIVER-UNUSED``, so stale waivers cannot pile up.
"""

import re
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML

from hdl_bag.flows.base import Finding


class WaiverError(ValueError):
    pass


class Waiver(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str = Field(min_length=1)
    file: str = Field(min_length=1)
    match: str | None = None
    line: int | None = None
    reason: str = Field(min_length=1)

    def covers(self, f: Finding) -> bool:
        return (
            f.rule == self.rule
            and fnmatchcase(f.file, self.file)
            and (self.line is None or f.line == self.line)
            and (self.match is None or re.search(self.match, f.message) is not None)
        )


def load_waivers(path: Path) -> list[Waiver]:
    """Load one tool's waiver file. Missing reasons and unknown keys are errors."""
    data = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise WaiverError(f"{path}: expected a list of waivers")
    out = []
    for i, entry in enumerate(data, 1):
        try:
            w = Waiver.model_validate(entry)
            if w.match is not None:
                re.compile(w.match)
        except (ValidationError, re.error) as exc:
            raise WaiverError(f"{path}: waiver {i}: {exc}") from exc
        if not w.reason.strip():
            raise WaiverError(f"{path}: waiver {i}: reason is empty")
        out.append(w)
    return out


def apply_waivers(
    tool: str, findings: list[Finding], waivers: list[Waiver], linted: list[str]
) -> list[Finding]:
    """Mark waived findings and add a WAIVER-UNUSED finding per stale waiver."""
    used: set[int] = set()
    out = []
    for f in findings:
        hit = next((i for i, w in enumerate(waivers) if w.covers(f)), None)
        if hit is None:
            out.append(f)
        else:
            used.add(hit)
            out.append(f.waive(waivers[hit].reason))
    for i, w in enumerate(waivers):
        if i not in used and any(fnmatchcase(p, w.file) for p in linted):
            out.append(
                Finding(tool, "", 0, "WAIVER-UNUSED", "error",
                        f"waiver for {w.rule} on {w.file} matched nothing")
            )  # fmt: skip
    return out
