# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The nightly report (#59, plan PR 7), one Markdown page in the nightly's artifact:

- the pass rate over time, one row per night: earlier nights from their summary.json
  artifacts (D55: the history lives in the artifacts), then tonight;
- configuration-space coverage: per dimension of regress.space, each value with the seeds
  that drew it and the seeds that passed with it, over every night the summaries record, and
  the gaps, the values drawn but never passed;
- tonight's open failures, each with its seed, step, first finding, IR and repro command.

It only reports (D56): nothing here fails the night.
"""

from collections.abc import Iterable
from typing import Any

from hdl_bag.regress.run import SeedResult
from hdl_bag.regress.space import Dimensions

STATUSES = ("pass", "fail", "timeout", "generator")


def _values(value: object) -> list[str]:
    """The buckets one dimension value counts in: each item of a list, else the value."""
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def coverage(rows: Iterable[tuple[Dimensions, bool]]) -> dict[str, dict[str, tuple[int, int]]]:
    """{dimension: {value: (seeds that drew it, seeds that passed with it)}} over ROWS, each a
    seed's dimensions and whether it passed."""
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for dims, passed in rows:
        for name, value in dims.items():
            for v in _values(value):
                drawn, ok = out.setdefault(name, {}).get(v, (0, 0))
                out[name][v] = (drawn + 1, ok + int(passed))
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def _rate_row(label: str, counts: dict[str, int], missing: int) -> str:
    seeds = sum(counts.get(s, 0) for s in STATUSES) + missing
    rate = f"{round(100 * counts.get('pass', 0) / seeds)}%" if seeds else "-"
    cells = " | ".join(str(counts.get(s, 0)) for s in STATUSES)
    return f"| {label} | {seeds} | {cells} | {missing} | {rate} |"


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "\\|")[:200]


def nightly_report(nights: list[tuple[str, dict[str, Any]]], tonight: str,
                   results: list[SeedResult]) -> str:  # fmt: skip
    """The report for TONIGHT's RESULTS, after the earlier NIGHTS: (label, summary.json data),
    oldest first. Missing seeds (a job cut off) count against the pass rate."""
    counts = {s: sum(r.status == s for r in results) for s in STATUSES}
    lines = ["# Nightly report", "", "## Pass rate over time", "",
             "| Night | Seeds | Pass | Fail | Timeout | Generator | Missing | Pass rate |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]  # fmt: skip
    lines += [_rate_row(label, data.get("counts", {}), len(data.get("missing", [])))
              for label, data in nights]  # fmt: skip
    lines.append(_rate_row(tonight, counts, 0))

    rows: list[tuple[Dimensions, bool]] = [
        (s["dimensions"], s["status"] == "pass")
        for _, data in nights for s in data.get("seeds", []) if s.get("dimensions")
    ]  # fmt: skip
    rows += [(r.dimensions, r.status == "pass") for r in results if r.dimensions]
    cover = coverage(rows)
    lines += ["", "## Configuration-space coverage", "",
              f"Over {len(rows)} seed(s) whose dimensions the summaries record.", "",
              "| Dimension | Value | Drawn | Passed |", "| --- | --- | --- | --- |"]  # fmt: skip
    for name, values in cover.items():
        lines += [f"| {name} | {v} | {drawn} | {ok} |" for v, (drawn, ok) in values.items()]
    gaps = [f"- {name}: {v} (drawn {drawn}, never passed)"
            for name, values in cover.items()
            for v, (drawn, ok) in values.items() if not ok]  # fmt: skip
    lines += ["", "### Gaps", ""]
    lines += gaps or ["No gaps: every value drawn has passed at least once."]

    open_ = sorted((r for r in results if r.status != "pass"), key=lambda r: r.seed)
    lines += ["", "## Open failures tonight", ""]
    if not open_:
        lines.append("No open failures tonight.")
    else:
        lines += ["| Seed | Status | Step | First finding | IR | Reproduce |",
                  "| --- | --- | --- | --- | --- | --- |"]  # fmt: skip
        lines += [f"| {r.seed} | {r.status} | {r.step or ''} | "
                  f"{_cell(r.findings[0] if r.findings else None)} | `{r.ir or ''}` | "
                  f"`{r.repro[0] if r.repro else ''}` |" for r in open_]  # fmt: skip
    return "\n".join(lines) + "\n"
