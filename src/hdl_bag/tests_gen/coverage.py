# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Functional coverage of a generated test run (D52): a report, with no closure target.

The bins are what the spec says the design does, from the reference model (route_model):
- **path**: each master reaches each slave, by read and by write;
- **response**: each master's response kinds: OKAY, a slave error, a miss (masked, where a
  tied PSLVERR hides it);
- **secure**: each master's accesses to each secure-only region, secure (allowed) and
  non-secure (denied);
- **burst**: each AXI4 master's burst types (INCR, FIXED, WRAP) and whether a converter on the
  way refuses them.

The model's view of the probe addresses and burst shapes the generated tests use defines the
space; a test records a bin (``hit``) only once it has checked that access. So the report says
what the run exercised: a filtered run, a failing test, or a test that does not apply leaves
bins uncovered. `bag sim` writes sim/coverage.json (the hits) and sim/coverage.md (the
report). Nothing here imports cocotb.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from hdl_bag.bursts import FIXED, INCR, WRAP
from hdl_bag.ir.model import Spec
from hdl_bag.tests_gen.route_model import (
    Outcome,
    burst_refused,
    burst_shapes,
    pin_sets,
    probes,
    prot_tied,
    route,
)

Bin = tuple[str, ...]
KINDS = ("path", "response", "secure", "burst")
BURSTS = {INCR: "INCR", FIXED: "FIXED", WRAP: "WRAP"}
_HITS: Counter[tuple[str, Bin]] = Counter()


def access_bins(spec: Spec, m: Any, addr: int, write: bool, nonsec: bool,
                exp: Outcome) -> list[tuple[str, Bin]]:  # fmt: skip
    """The bins one single access covers, given the model's outcome EXP for it."""
    out: list[tuple[str, Bin]] = []
    if exp.slave is not None:
        out.append(("path", (m.name, exp.slave, "write" if write else "read")))
    kind = "miss" if exp.slave is None else "slave error" if exp.slave_error else "OKAY"
    if exp.masked:
        kind += " (masked)"
    out.append(("response", (m.name, kind)))
    region = next((r for r in spec.memory_map if r.secure and r.base <= addr < r.base + r.size),
                  None)  # fmt: skip
    if region is not None:
        out.append(("secure", (m.name, f"{region.slave}.{region.region}",
                               "non-secure" if nonsec else "secure",
                               "denied" if exp.slave is None else "allowed")))  # fmt: skip
    return out


def burst_bin(m: Any, burst: int, refused: bool) -> tuple[str, Bin]:
    return "burst", (m.name, BURSTS[burst], "refused" if refused else "passed")


def space(spec: Spec) -> dict[str, set[Bin]]:
    """Every bin of SPEC: what the generated tests' accesses can cover, per the model."""
    out: dict[str, set[Bin]] = {k: set() for k in KINDS}
    secure = any(r.secure for r in spec.memory_map)
    for pins in pin_sets(spec):
        for m in spec.masters:
            options = [False, True] if secure and not prot_tied(spec, m.name) else [False]
            for nonsec in options:
                for addr in probes(spec):
                    if addr >= 1 << m.addr_width:
                        continue
                    for write in (True, False):
                        exp = route(spec, m.name, addr, write, nonsec, pins)
                        for kind, key in access_bins(spec, m, addr, write, nonsec, exp):
                            out[kind].add(key)
    for m in spec.masters:
        for s in spec.slaves:
            for _, addr, burst, _, beats in burst_shapes(spec, m, s):
                exp = route(spec, m.name, addr, True, False, {})
                kind, key = burst_bin(m, burst, burst_refused(spec, m, exp, burst, beats))
                out[kind].add(key)
    return out


def hit(kind: str, key: Bin) -> None:
    """Record that a generated test checked an access covering bin KEY of KIND."""
    _HITS[(kind, key)] += 1


def save(path: Path) -> None:
    """Add this process's hits to PATH (JSON: {kind: [[*bin, count], ...]}), and forget them."""
    data: dict[str, list[list[Any]]] = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )
    counts = Counter(
        {(k, tuple(row[:-1])): int(row[-1]) for k, rows in data.items() for row in rows}
    )
    counts.update(_HITS)
    _HITS.clear()
    merged: dict[str, list[list[Any]]] = {}
    for (kind, key), n in sorted(counts.items()):
        merged.setdefault(kind, []).append([*key, n])
    path.write_text(json.dumps(merged, indent=1) + "\n", encoding="utf-8")


def load(path: Path) -> Counter[tuple[str, Bin]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Counter({(k, tuple(row[:-1])): int(row[-1]) for k, rows in data.items() for row in rows})


_COLUMNS = {"path": ("Master", "Slave", "Access"), "response": ("Master", "Response"),
            "secure": ("Master", "Region", "Access", "Outcome"),
            "burst": ("Master", "Burst", "Outcome")}  # fmt: skip


def report(spec: Spec, hits: Counter[tuple[str, Bin]]) -> tuple[str, list[str]]:
    """(the Markdown report, one summary line per kind) of HITS against SPEC's space."""
    bins = space(spec)
    summary, lines = [], [f"# {spec.project.name} functional coverage", "",
                          "What the generated tests exercised, against the bins the reference "
                          "model gives for this design (hdl_bag.tests_gen.coverage). A report "
                          "only: it gates nothing.", ""]  # fmt: skip
    lines += ["| Kind | Covered | Bins |", "|---|---|---|"]
    for kind in KINDS:
        covered = sum(1 for b in bins[kind] if hits[(kind, b)])
        lines.append(f"| {kind} | {covered} | {len(bins[kind])} |")
        if bins[kind]:
            summary.append(f"coverage: {kind} {covered}/{len(bins[kind])}")
    for kind in KINDS:
        if not bins[kind]:
            continue
        lines += ["", f"## {kind}", "", "| " + " | ".join(_COLUMNS[kind]) + " | Hits |",
                  "|" + "---|" * (len(_COLUMNS[kind]) + 1)]  # fmt: skip
        lines += ["| " + " | ".join(b) + f" | {hits[(kind, b)] or '**none**'} |"
                  for b in sorted(bins[kind])]  # fmt: skip
    return "\n".join(lines) + "\n", summary
