# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Project and Wrapper settings, name definitions, clocks and resets."""

import re
from typing import TYPE_CHECKING, Final

from hdl_bag.checks.context import Context, Kind
from hdl_bag.reader.model import Cell
from hdl_bag.template import SCHEMA_VERSION

if TYPE_CHECKING:
    from hdl_bag.checks import CheckResult

LINT_TOOLS: Final = ("slang", "verilator", "verible")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# (setting, required placeholders) for Wrapper naming patterns
PATTERNS: Final = {
    "Signal naming pattern": ("port", "proto", "signal"),
    "Adapter instance naming": ("from", "type", "to"),
    "Sub-wrapper port naming": ("instance", "proto", "signal"),
}


def _setting(ctx: Context, settings: dict[str, Cell], sheet: str, key: str) -> Cell | None:
    cell = settings.get(key)
    if cell is None:
        ctx.report("VALUE-MISSING", sheet, f"setting '{key}' is missing")
    return cell


def check_schema_version(ctx: Context) -> bool:
    """False when the workbook's schema differs: nothing else can be checked."""
    cell = _setting(ctx, ctx.raw.project, "Project", "Spec schema version")
    if cell is None:
        return False
    if str(cell.value) != SCHEMA_VERSION:
        ctx.report(
            "PROJ-SCHEMA-VERSION", cell,
            f"Spec schema version {cell.value!r} differs from {SCHEMA_VERSION!r} expected by this BAG release",
        )  # fmt: skip
        return False
    return True


def check_project(ctx: Context) -> None:
    p = ctx.raw.project

    def get(key: str) -> Cell | None:
        return _setting(ctx, p, "Project", key)

    if c := get("Project name"):
        ctx.name(c)
    for key in ("Spec revision", "Author"):
        if c := get(key):
            ctx.text(c)
    if c := get("SystemVerilog standard"):
        ctx.enum(c, ["IEEE 1800-2017"])
    if c := get("Output directory"):
        ctx.text(c, required=True)
    for key in (
        "Generate RTL", "Generate C header", "Generate memory-map doc",
        "Generate cocotb unit tests", "Generate formal (SymbiYosys)",
    ):  # fmt: skip
        if c := get(key):
            ctx.yes_no(c)
    if (c := get("Lint tools")) and (v := ctx.text(c, required=True)):
        tools = [t.strip() for t in v.split(",")]
        bad = [t for t in tools if t not in LINT_TOOLS]
        if bad or len(set(tools)) != len(tools):
            ctx.report(
                "VALUE-ILLEGAL",
                c,
                f"Lint tools {v!r} must be a comma-separated subset of {', '.join(LINT_TOOLS)}",
            )
    if c := get("Simulator"):
        ctx.enum(c, ["verilator"])


def check_wrapper_settings(ctx: Context) -> None:
    w = ctx.raw.wrapper

    def get(key: str) -> Cell | None:
        return _setting(ctx, w, "Wrapper", key)

    if c := get("Top module name"):
        ctx.name(c)
    if c := get("Port style"):
        ctx.enum(c, ["flat", "sv-interface"])
    for key, needed in PATTERNS.items():
        if (c := get(key)) and (v := ctx.text(c, required=True)):
            found = _PLACEHOLDER.findall(v)
            unknown = sorted(set(found) - set(needed))
            missing = [n for n in needed if n not in found]
            if missing or unknown or len(found) != len(set(found)):
                ctx.report(
                    "WRAP-PATTERN",
                    c,
                    f"{key} {v!r} must use each of {{{'}, {'.join(needed)}}} exactly once",
                )
    if c := get("Clock/reset ports"):
        ctx.enum(c, ["one per domain"])
    if c := get("Parameter package"):
        ctx.name(c)
    if (c := get("File header")) and (v := ctx.text(c)) and not (ctx.base_dir / v).is_file():
        ctx.report("WRAP-FILE-HEADER", c, f"File header {v!r} not found next to the workbook")
    if (c := get("Extra top-level inputs")) and (v := ctx.text(c)):
        for name in (n.strip() for n in v.split(",")):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                ctx.report(
                    "NAME-IDENT",
                    c,
                    f"Extra top-level input {name!r} is not a SystemVerilog identifier",
                )
    if c := get("Adapter placement"):
        ctx.enum(c, ["with fabric", "with port", "top"])


def extra_inputs(ctx: Context) -> list[str]:
    c = ctx.raw.wrapper.get("Extra top-level inputs")
    v = ctx.text(c) if c else None
    return [n.strip() for n in v.split(",")] if v else []


_DEFINING: Final[tuple[tuple[Kind, str], ...]] = (
    ("master", "Master"), ("slave", "Slave"), ("bridge", "Bridge"), ("fabric", "Fabric"),
)  # fmt: skip


def check_names(ctx: Context) -> None:
    """Define masters, slaves, bridges and fabrics; names unique incl. sub-wrappers."""
    seen: dict[str, str] = {}  # name -> "<Sheet> row <n>"
    for kind, column in _DEFINING:
        for row in ctx.rows(kind):
            name = ctx.name(row[column])
            if name is None:
                continue
            if name in seen:
                ctx.report("NAME-UNIQUE", row[column], f"{name} is already defined on {seen[name]}")
                continue
            seen[name] = row.where
            ctx.kinds[name] = kind
            ctx.defs[name] = row
    for i, row in enumerate(ctx.raw.sub_wrappers):
        cell = row["Sub-wrapper"]
        # Row 5 is the top wrapper, checked on Wrapper > Top module name.
        name = ctx.text(cell) if i == 0 else ctx.name(cell)
        if name is None:
            continue
        if name in seen:
            ctx.report("NAME-UNIQUE", cell, f"{name} is already defined on {seen[name]}")
        else:
            seen[name] = row.where


def check_clocks_resets(ctx: Context) -> None:
    ports: dict[str, str] = {}  # top-level clock/reset port names
    resets: dict[str, tuple[str | None, str]] = {}  # reset -> (domain, where)
    for row in ctx.raw.resets:
        name = ctx.name(row["Reset"], lowercase=False)
        ctx.enum(row["Polarity"], ["active-low", "active-high"])
        ctx.enum(row["Assertion"], ["async", "sync"])
        ctx.enum(row["Deassertion"], ["async", "sync"])
        domain = ctx.name(row["Domain"], lowercase=False)
        if name is None:
            continue
        if name in ports:
            ctx.report("NAME-UNIQUE", row["Reset"], f"{name} is already defined on {ports[name]}")
            continue
        ports[name] = row.where
        resets[name] = (domain, row.where)

    for row in ctx.raw.clocks:
        name = ctx.name(row["Clock"], lowercase=False)
        freq = row["Freq (MHz)"]
        if freq.value is None:
            ctx.report("VALUE-MISSING", freq, "Freq (MHz) is required")
        elif (
            isinstance(freq.value, bool)
            or not isinstance(freq.value, int | float)
            or freq.value <= 0
        ):
            ctx.report("VALUE-ILLEGAL", freq, f"Freq (MHz) {freq.value!r} is not a positive number")
        domain = ctx.name(row["Domain"], lowercase=False)
        reset = ctx.name(row["Reset"], lowercase=False)
        if name is not None:
            if name in ports:
                ctx.report(
                    "NAME-UNIQUE", row["Clock"], f"{name} is already defined on {ports[name]}"
                )
            else:
                ports[name] = row.where
        if domain is None:
            continue
        if domain in ctx.domains:
            ctx.report("CLK-DOMAIN-TWO-CLOCKS", row["Domain"],
                       f"domain {domain} already has a clock on {ctx.domains[domain].where}")  # fmt: skip
            continue
        ctx.domains[domain] = row
        if reset is not None:
            if reset not in resets:
                ctx.report("REF-UNKNOWN", row["Reset"], f"reset {reset} is not defined on Resets")
            elif resets[reset][0] is not None and resets[reset][0] != domain:
                ctx.report("CLK-RESET-DOMAIN", row["Reset"],
                           f"reset {reset} belongs to domain {resets[reset][0]}, not {domain}")  # fmt: skip

    by_domain: dict[str, str] = {}
    for row in ctx.raw.resets:
        name, domain = ctx.text(row["Reset"]), ctx.text(row["Domain"])
        if name is None or domain is None:
            continue
        if domain not in ctx.domains:
            ctx.report("REF-UNKNOWN", row["Domain"], f"domain {domain} is not defined on Clocks")
        elif domain in by_domain:
            ctx.report(
                "CLK-RESET-DOMAIN",
                row["Domain"],
                f"domain {domain} already has reset {by_domain[domain]}",
            )
        else:
            by_domain[domain] = name
    for domain, row in ctx.domains.items():
        if domain not in by_domain:
            ctx.report(
                "CLK-DOMAIN-NO-RESET", row["Domain"], f"domain {domain} has no reset on Resets"
            )


def check_domain_ref(ctx: Context, cell: Cell) -> str | None:
    domain = ctx.name(cell, lowercase=False)
    if domain is not None and domain not in ctx.domains:
        ctx.report("REF-UNKNOWN", cell, f"domain {domain} is not defined on Clocks")
        return None
    return domain


def check_cdc_resets(ctx: Context, result: "CheckResult") -> None:
    """Both sides of every clock-domain crossing (a cdc adapter, or a bridge whose fabrics
    are in different domains) need async assertion and sync deassertion: the crossing's
    gray-coded FIFOs (PULP axi_cdc) or 4-phase handshake (in-house bridges) hold pointers
    in both domains, which must be reset together and leave reset cleanly in each clock.
    That the two resets assert together (one source, no warm reset of one side) cannot be
    seen in the workbook; it is the integrator's contract (docs).
    """
    from hdl_bag.ir.resolve import resolve

    spec = resolve(result)
    fabrics = {f.name: f for f in spec.fabrics}
    crossings: list[tuple[str, str, str]] = []  # (name, domain, domain)
    for a in spec.adapters:
        if a.type == "cdc":
            crossings.append((a.name, str(a.params["from"]), str(a.params["to"])))
    for b in spec.bridges:
        up, down = fabrics[b.upstream].domain, fabrics[b.downstream].domain
        if up != down:
            crossings.append((b.name, up, down))
    resets = {r.domain: r for r in spec.resets}
    rows = {ctx.text(r["Reset"]): r for r in ctx.raw.resets}
    reported: set[str] = set()
    for name, *domains in sorted(crossings):
        for domain, other in (domains, domains[::-1]):
            r = resets[domain]
            if r.name in reported or (r.assertion, r.deassertion) == ("async", "sync"):
                continue
            reported.add(r.name)
            column = "Assertion" if r.assertion != "async" else "Deassertion"
            ctx.report(
                "CLK-CDC-RESET",
                rows[r.name][column],
                f"reset {r.name} ({r.assertion} assertion, {r.deassertion} deassertion) "
                f"resets one side of the clock-domain crossing {name} ({domain} -> {other}); "
                "a crossing needs async assertion and sync deassertion on both sides",
            )
