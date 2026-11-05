# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""IR models (schema 0.6). The YAML dump of ``Spec`` is the canonical spec.

Field order is the dump order. Every list is sorted by resolve, so equal specs give
byte-identical files. Addresses and sizes are ints here and hex text in YAML.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from hdl_bag.reader.values import parse_hex


def _to_hex(v: int) -> str:
    h, parts = f"{v:08X}", list[str]()
    while h:
        parts.insert(0, h[-4:])
        h = h[:-4]
    return "0x" + "_".join(parts)


def _from_hex(v: object) -> object:
    return parse_hex(v) if isinstance(v, str) else v


Addr = Annotated[int, BeforeValidator(_from_hex), PlainSerializer(_to_hex, return_type=str)]
Signal = Literal["drive", "tie-0", "tie-default", "n/a"]


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Outputs(_M):
    rtl: bool
    c_header: bool
    doc: bool
    tests: bool
    formal: bool


class Project(_M):
    name: str
    revision: str | None
    author: str | None
    sv_std: str
    output_dir: str
    outputs: Outputs
    lint_tools: tuple[str, ...]
    simulator: str


class Clock(_M):
    name: str
    mhz: int | float
    source: str | None
    domain: str
    reset: str


class Reset(_M):
    name: str
    polarity: Literal["active-low", "active-high"]
    assertion: Literal["async", "sync"]
    deassertion: Literal["async", "sync"]
    source: str | None
    domain: str


class PortRef(_M):
    index: int
    port: str
    priority: int | None = None


class IdWidth(_M):
    master_side: int
    slave_side: int


class Midpoint(_M):
    fifo_depth: int
    slice: Literal["none", "cut", "multicut-2", "multicut-3"]


class AddrRule(_M):
    """One decoder entry: addresses of a region on a fabric and the slave port it hits."""

    slave_port: int
    slave: str
    region: str
    base: Addr
    end: Addr
    access: Literal["RW", "RO", "WO"]
    secure: bool


class AccessRule(_M):
    master: str
    slave: str
    access: Literal["RO", "WO", "none"]


class Alias(_M):
    base: Addr
    size: Addr
    to: Addr
    enable: str  # always | top-level input


class Decode(_M):
    master: str
    miss: Literal["DECERR", "SLVERR", "HRESP_ERROR", "PSLVERR", "default-slave"]
    default_slave: str | None
    aliases: tuple[Alias, ...]


class Fabric(_M):
    name: str
    protocol: str
    topology: Literal["crossbar", "shared-bus", "single-layer", "decoder-mux"]
    data_width: int
    addr_width: int
    domain: str
    arbitration: Literal["round-robin", "fixed-priority", "single-master"]
    latency_mode: str | None
    max_mst_trans: int | None
    max_slv_trans: int | None
    midpoint: Midpoint | None
    template: str
    backing_ip: str | None
    external_module: str | None  # bag_external: the user's module, with ports named by position
    master_ports: tuple[PortRef, ...]
    slave_ports: tuple[PortRef, ...]
    id_width: IdWidth | None
    max_parallel: int
    addr_rules: tuple[AddrRule, ...]
    access_rules: tuple[AccessRule, ...]
    decode: tuple[Decode, ...]


class Master(_M):
    name: str
    protocol: str
    fabric: str
    data_width: int
    addr_width: int
    id_width: int | None
    user_width: int
    domain: str
    max_outstanding_rd: int | None
    max_outstanding_wr: int | None


class Slave(_M):
    name: str
    protocol: str
    fabric: str
    data_width: int
    addr_width: int
    id_width: int | None  # resolved; equals the fabric slave-side width when auto
    id_width_auto: bool
    user_width: int
    domain: str
    error_response: str
    wait_states: int | Literal["variable"] | None


class Bridge(_M):
    name: str
    template: str
    upstream: str
    downstream: str
    window_base: Addr
    pass_through: bool
    fifo_depth: int
    upstream_protocol: str
    downstream_protocol: str
    upstream_width: int
    downstream_width: int
    cdc: bool  # inside the bridge (D10)


class Adapter(_M):
    name: str
    type: Literal["pc", "dw", "cdc", "iw", "fifo", "cut"]
    port: str
    fabric: str
    chain_index: int  # position from the port side (D9)
    template: str
    params: dict[str, str | int]
    placed_in: str
    rtl_name: str


class PortOption(_M):
    port: str
    fifo_depth: int
    slice: Literal["none", "cut", "multicut-2", "multicut-3", "n/a"]
    prot: Signal
    qos: Signal
    region: Signal
    cache: Signal
    lock: Signal
    pstrb: Signal
    pslverr: Signal


class UserPort(_M):
    """An extra port of a user-supplied module (bag_external) and its connection (D41).

    ``target`` is the extra top-level input (connection extra-input) or the clock domain
    (clock, reset); ``tie`` the constant of connection tie."""

    instance: str
    port: str
    direction: Literal["input", "output"]
    width: int
    connection: Literal["expose", "extra-input", "tie", "open", "clock", "reset"]
    target: str | None = None
    tie: Addr | None = None


class Region(_M):
    slave: str
    region: str
    base: Addr
    size: Addr
    access: Literal["RW", "RO", "WO"]
    secure: bool
    fabric: str


class SubWrapper(_M):
    name: str
    parent: str


class Placement(_M):
    instance: str
    kind: Literal["fabric", "bridge", "adapter"]
    wrapper: str
    rtl_name: str


class Hierarchy(_M):
    top: str
    wrappers: tuple[SubWrapper, ...]
    placement: tuple[Placement, ...]


class ExposedPort(_M):
    port: str
    exposed: bool
    prefix: str | None


class Wrapper(_M):
    top: str
    port_style: Literal["flat", "sv-interface"]
    signal_pattern: str
    clock_reset_ports: str
    package: str
    file_header: str | None
    extra_inputs: tuple[str, ...]
    adapter_placement: Literal["with fabric", "with port", "top"]
    adapter_naming: str
    subwrapper_port_naming: str
    ports: tuple[ExposedPort, ...]


class Provenance(_M):
    bag_version: str
    source: str
    sha256: str


class Spec(_M):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: str = Field(alias="schema")
    project: Project
    clocks: tuple[Clock, ...]
    resets: tuple[Reset, ...]
    fabrics: tuple[Fabric, ...]
    masters: tuple[Master, ...]
    slaves: tuple[Slave, ...]
    bridges: tuple[Bridge, ...]
    adapters: tuple[Adapter, ...]
    port_options: tuple[PortOption, ...]
    user_ports: tuple[UserPort, ...]
    memory_map: tuple[Region, ...]
    hierarchy: Hierarchy
    wrapper: Wrapper
    provenance: Provenance
