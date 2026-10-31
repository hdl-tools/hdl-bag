# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""``bag check``: every rule in the user guide's Validation checks table and more."""

from dataclasses import dataclass
from pathlib import Path

from hdl_bag.checks import basics, memory, ports, topology, wrapper
from hdl_bag.checks.context import Context
from hdl_bag.checks.core import RULES, Finding, Severity
from hdl_bag.reader.model import RawSpec
from hdl_bag.templates import Registry, builtin_registry

__all__ = ["RULES", "CheckResult", "Context", "Finding", "Severity", "run_checks"]


@dataclass(frozen=True)
class CheckResult:
    context: Context
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == Severity.ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == Severity.WARNING)


def run_checks(raw: RawSpec, base_dir: Path, registry: Registry | None = None) -> CheckResult:
    """Run every check. ``base_dir`` is the workbook's directory (for File header)."""
    ctx = Context(raw=raw, registry=registry or builtin_registry(), base_dir=base_dir)
    if basics.check_schema_version(ctx):
        basics.check_project(ctx)
        basics.check_wrapper_settings(ctx)
        basics.check_names(ctx)
        basics.check_clocks_resets(ctx)
        topology.check_topology(ctx)
        ports.check_ports(ctx)  # before fabrics: owns the port cells fabric widths read
        ports.check_fabrics(ctx)
        ports.check_bridges(ctx)
        ports.check_port_options(ctx)
        regions = memory.check_memory_map(ctx)
        memory.check_bridge_windows(ctx, regions)
        memory.check_access_rules(ctx)
        memory.check_address_decode(ctx, regions)
        memory.check_secure_prot(ctx, regions)
        wrapper.check_hierarchy(ctx)
        wrapper.check_wrapper_ports(ctx)
    # Checks on the resolved spec: they need every row-level check to pass first.
    if not any(f.severity == Severity.ERROR for f in ctx.findings):
        wrapper.check_boundary_names(ctx, CheckResult(ctx, tuple(ctx.findings)))
    unknown = {f.rule for f in ctx.findings} - RULES.keys()
    assert not unknown, f"unregistered rule IDs: {unknown}"
    findings = tuple(sorted(set(ctx.findings), key=Finding.sort_key))
    return CheckResult(ctx, findings)
