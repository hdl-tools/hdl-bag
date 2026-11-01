# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""SpecData: every input value of a spec workbook, in the builder's row layout.

Rows hold input-column values only, in input-column order; derived columns are
filled with formulas by the builder. The demo_ss sample is one SpecData; the IR is
written back to a workbook through another.
"""

from dataclasses import dataclass, field

Cell = str | int | float | None
Rows = list[list[Cell]]

# Defaults the blank template shows (single legal value or the guide's default).
PROJECT_DEFAULTS: dict[str, Cell] = {
    "SystemVerilog standard": "IEEE 1800-2017",
    "Lint tools": "slang, verilator, verible",
    "Simulator": "verilator",
}
WRAPPER_DEFAULTS: dict[str, Cell] = {
    "Port style": "flat",
    "Signal naming pattern": "{port}_{proto}_{signal}",
    "Clock/reset ports": "one per domain",
    "Adapter placement": "with fabric",
    "Adapter instance naming": "{from}_{type}_2_{to}",
    "Sub-wrapper port naming": "{instance}_{proto}_{signal}",
}


@dataclass
class SpecData:
    title: str  # README A1
    description: str  # README A2
    project: dict[str, Cell] = field(default_factory=lambda: dict(PROJECT_DEFAULTS))
    wrapper: dict[str, Cell] = field(default_factory=lambda: dict(WRAPPER_DEFAULTS))
    clocks: Rows = field(default_factory=list)
    resets: Rows = field(default_factory=list)
    fabrics: Rows = field(default_factory=list)
    masters: Rows = field(default_factory=list)
    slaves: Rows = field(default_factory=list)
    bridges: Rows = field(default_factory=list)
    topology: Rows = field(default_factory=list)
    access_rules: Rows = field(default_factory=list)
    memory_map: Rows = field(default_factory=list)
    address_decode: Rows = field(default_factory=list)
    port_options: Rows = field(default_factory=list)
    user_ports: Rows = field(default_factory=list)
    sub_wrappers: Rows = field(default_factory=list)  # excluding the top wrapper row
    placement: Rows = field(default_factory=list)
    exposed_ports: Rows = field(default_factory=list)
