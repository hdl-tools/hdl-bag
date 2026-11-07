# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Functional coverage of a generated test run (D52): a report, with no closure target.

The bins are what the spec says the design does, from the reference model (route_model):
- **path**: each master reaches each slave, by read and by write;
- **response**: each master's response kinds per slave: OKAY, a slave error, a miss (masked,
  where a tied PSLVERR hides it);
- **secure**: each master's accesses to each secure-only region, secure and non-secure (the
  bit the slave would see: a tied PROT is non-secure), allowed or refused;
- **burst**: each AXI4 master's burst shapes (INCR, narrow, at a 4 KB edge, FIXED, WRAP) and
  whether a converter on the way refuses them.

The space is the model's view of the same probe addresses and burst shapes the generated tests
use, so a full passing run reads 100% by construction: the report says what a run exercised,
not what the tests leave untested. A test records a bin (``hit``) only once it has checked
that access, so a filtered run, a test that fails (from where it fails) or does not apply,
or a shard (BAG_ROUTE_MASTERS) leaves bins uncovered. `bag sim` writes sim/coverage.json (the hits) and sim/coverage.md (the
report). Nothing here imports cocotb.
"""

import json
import os
from collections import Counter
from collections.abc import Collection
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
    out.append(("response", (m.name, exp.slave or "-", kind)))
    region = next((r for r in spec.memory_map if r.secure and r.base <= addr < r.base + r.size),
                  None)  # fmt: skip
    if region is not None:
        seen_nonsec = nonsec or prot_tied(spec, m.name)  # as route() takes it
        out.append(("secure", (m.name, f"{region.slave}.{region.region}",
                               "non-secure" if seen_nonsec else "secure",
                               "refused" if exp.slave is None else "allowed")))  # fmt: skip
    return out


def burst_bin(m: Any, shape: str, burst: int, refused: bool) -> tuple[str, Bin]:
    return "burst", (m.name, shape, BURSTS[burst], "refused" if refused else "passed")


def space(spec: Spec, masters: Collection[str] | None = None) -> dict[str, set[Bin]]:
    """Every bin of SPEC (of MASTERS only, if given): what the generated tests' accesses can
    cover, per the model."""
    out: dict[str, set[Bin]] = {k: set() for k in KINDS}
    secure = any(r.secure for r in spec.memory_map)
    chosen = [m for m in spec.masters if masters is None or m.name in masters]
    for pins in pin_sets(spec):
        for m in chosen:
            options = [False, True] if secure and not prot_tied(spec, m.name) else [False]
            for nonsec in options:
                for addr in probes(spec):
                    if addr >= 1 << m.addr_width:
                        continue
                    for write in (True, False):
                        exp = route(spec, m.name, addr, write, nonsec, pins)
                        for kind, key in access_bins(spec, m, addr, write, nonsec, exp):
                            out[kind].add(key)
    for m in chosen:
        for s in spec.slaves:
            for shape, addr, burst, _, beats in burst_shapes(spec, m, s):
                exp = route(spec, m.name, addr, True, False, {})
                kind, key = burst_bin(m, shape, burst, burst_refused(spec, m, exp, burst, beats))
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
    tmp = path.with_suffix(".tmp")  # replaced whole, so a killed run never leaves half a file
    tmp.write_text(json.dumps(merged, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path) -> Counter[tuple[str, Bin]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Counter({(k, tuple(row[:-1])): int(row[-1]) for k, rows in data.items() for row in rows})


_COLUMNS = {"path": ("Master", "Slave", "Access"), "response": ("Master", "Slave", "Response"),
            "secure": ("Master", "Region", "Access", "Outcome"),
            "burst": ("Master", "Shape", "Burst", "Outcome")}  # fmt: skip


def report(spec: Spec, hits: Counter[tuple[str, Bin]],
           masters: Collection[str] | None = None) -> tuple[str, list[str]]:  # fmt: skip
    """(the Markdown report, one summary line per kind) of HITS against SPEC's space (of
    MASTERS only, if given)."""
    bins = space(spec, masters)
    summary, lines = [], [f"# {spec.project.name} functional coverage", "",
                          "What the generated tests exercised. The bins are the reference "
                          "model's view of the same probe addresses and burst shapes the tests "
                          "use, so a full passing run reads 100%: this says what ran, not what "
                          "the tests leave untested (hdl_bag.tests_gen.coverage). A report "
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
