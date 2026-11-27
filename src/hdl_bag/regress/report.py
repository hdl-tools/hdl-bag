# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The nightly report (#59, plan PR 7), one Markdown page in the nightly's artifact:

- the pass rate over time, one row per night: earlier nights from their summary.json
  artifacts (D55: the history lives in the artifacts), then tonight;
- configuration-space coverage: per dimension of regress.space, each value with the seeds
  that drew it and the seeds that passed with it, over every night the summaries record, and
  the gaps: the values drawn but never passed, and (D113) for the dimensions with a known
  domain (DOMAINS) the values never drawn at all;
- tonight's open failures, each with its seed, step, first finding, IR and repro command, and
  the seeds that left no result (a job cut off).

It only reports (D56): nothing here fails the night.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from hdl_bag.regress.run import SeedResult
from hdl_bag.regress.space import Dimensions

STATUSES = ("pass", "fail", "timeout", "generator")
NONE = "(none)"  # the bucket of an empty list: no bridges, no port options, ...


def domains() -> dict[str, list[str]]:
    """D113: every value the random draw can give the enumerable dimensions, so a value never
    drawn shows as a gap: the bridge templates and the protocol pairs they join (and the pc
    pairs), the fabric topologies, the map, the port style and the port options."""
    from hdl_bag.regress.generate import _bridges

    joins = _bridges()
    templates = sorted({t for ts in joins.values() for t in ts})
    pairs = sorted({f"{up}>{down}" for up, down in joins} | {"AXI4~AXI4-Lite", "AXI4-Lite~AXI4"})
    topologies = [f"{p} {t}" for p in ("AXI4", "AXI4-Lite") for t in ("crossbar", "shared-bus")]
    topologies += ["AHB-Lite single-layer", "APB3 decoder-mux", "APB4 decoder-mux"]
    return {
        "bridges": [NONE, *templates],
        "protocol_pairs": [NONE, *pairs],
        "topologies": sorted(topologies),
        "map": ["dense", "sparse"],
        "port_style": ["flat", "sv-interface"],
        "port_options": [NONE, "fifo", "slice", "tie"],
        "secure": ["False", "True"],
        "external": ["False", "True"],
    }


def _values(value: object) -> list[str]:
    """The buckets one dimension value counts in: each item of a list (NONE for an empty one),
    else the value."""
    if isinstance(value, list):
        return [str(v) for v in value] or [NONE]
    return [str(value)]


def _order(value: str) -> tuple[int, int, str]:
    """Counts in number order (1, 2, 10), the rest by name."""
    return (0, int(value), "") if value.isdigit() else (1, 0, value)


def coverage(rows: Iterable[tuple[Dimensions, bool]]) -> dict[str, dict[str, tuple[int, int]]]:
    """{dimension: {value: (seeds that drew it, seeds that passed with it)}} over ROWS, each a
    seed's dimensions and whether it passed."""
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for dims, passed in rows:
        for name, value in dims.items():
            for v in _values(value):
                drawn, ok = out.setdefault(name, {}).get(v, (0, 0))
                out[name][v] = (drawn + 1, ok + int(passed))
    return {k: dict(sorted(v.items(), key=lambda kv: _order(kv[0])))
            for k, v in sorted(out.items())}  # fmt: skip


def _rate_row(label: str, counts: dict[str, int], missing: int) -> str:
    seeds = sum(counts.get(s, 0) for s in STATUSES) + missing
    rate = f"{round(100 * counts.get('pass', 0) / seeds)}%" if seeds else "-"
    cells = " | ".join(str(counts.get(s, 0)) for s in STATUSES)
    return f"| {label} | {seeds} | {cells} | {missing} | {rate} |"


def _cell(text: str | None) -> str:
    """Text for one table cell: one line, at most 200 characters, | escaped."""
    return " ".join((text or "").split())[:200].replace("|", "\\|")


def nightly_report(nights: list[tuple[str, dict[str, Any]]], tonight: str,
                   results: list[SeedResult], missing: Sequence[int] = ()) -> str:  # fmt: skip
    """The report for TONIGHT's RESULTS, after the earlier NIGHTS: (label, summary.json data),
    oldest first. MISSING seeds, tonight's that left no result (a job cut off), count against
    the pass rate and are open failures (review of #236)."""
    counts = {s: sum(r.status == s for r in results) for s in STATUSES}
    lines = ["# Nightly report", "", "## Pass rate over time", "",
             "| Night | Seeds | Pass | Fail | Timeout | Generator | Missing | Pass rate |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]  # fmt: skip
    lines += [_rate_row(label, data.get("counts", {}), len(data.get("missing", [])))
              for label, data in nights]  # fmt: skip
    lines.append(_rate_row(tonight, counts, len(missing)))

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
    gaps += [f"- {name}: {v} (never drawn)" for name, known in domains().items()
             for v in known if v not in cover.get(name, {})]  # fmt: skip
    lines += [
        "",
        "### Gaps",
        "",
        "Values drawn but never passed, and values the draw can give but never has.",
        "",
    ]
    lines += gaps or ["No gaps: every value drawn has passed at least once."]

    open_ = sorted((r for r in results if r.status != "pass"), key=lambda r: r.seed)
    lines += ["", "## Open failures tonight", ""]
    if missing:
        lines += [f"Left no result (their job was cut off): {', '.join(map(str, missing))}.", ""]
    if not open_ and not missing:
        lines.append("No open failures tonight.")
    if open_:
        lines += ["| Seed | Status | Step | First finding | IR | Reproduce |",
                  "| --- | --- | --- | --- | --- | --- |"]  # fmt: skip
        lines += [f"| {r.seed} | {r.status} | {_cell(r.step)} | "
                  f"{_cell(r.findings[0] if r.findings else None)} | `{r.ir or ''}` | "
                  f"`{r.repro[0] if r.repro else ''}` |" for r in open_]  # fmt: skip
    return "\n".join(lines) + "\n"
