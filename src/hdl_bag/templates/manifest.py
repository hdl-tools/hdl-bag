# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Template manifest schema (api_version 1).

Built-in templates use the same manifest format that v2 user plug-ins will use.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from hdl_bag.protocols import Protocol

Kind = Literal["fabric", "bridge", "adapter"]
Topology = Literal["crossbar", "shared-bus", "single-layer", "decoder-mux"]
Arbitration = Literal["round-robin", "fixed-priority", "single-master"]
AdapterType = Literal["pc", "dw", "cdc", "iw", "fifo", "cut"]


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: Kind
    api_version: Literal[1]
    description: str
    # fabric: protocols of the fabric; bridge: upstream / downstream; adapter: both sides
    protocols: tuple[Protocol, ...] = ()
    upstream: tuple[Protocol, ...] = ()
    downstream: tuple[Protocol, ...] = ()
    topologies: tuple[Topology, ...] = ()
    arbitration: tuple[Arbitration, ...] = ()
    adapter_type: AdapterType | None = None
    data_widths: tuple[int, ...] = ()  # empty = the protocol's legal widths
    param_map: dict[str, str] = {}  # BAG parameter -> backing-IP parameter
    port_map: dict[str, str] = {}  # standard bus signal -> backing-IP port / struct field
    sources: tuple[str, ...] = ()  # Bender target "bender:<pkg>:<module>" or repo file paths
    licence: str  # SPDX identifier of the backing implementation
    qualification: Literal["unqualified", "qualified"] = "unqualified"
    opaque: bool = False  # True: user supplies the RTL (bag_external)
    # Generation: a template with RTL has an instance.sv.j2 next to its manifest that
    # renders one instantiation; templates without RTL yet stop `bag generate`.
    rtl: bool = False

    @model_validator(mode="after")
    def _by_kind(self) -> "Manifest":
        if self.kind == "fabric":
            if not (self.protocols and self.topologies and self.arbitration):
                raise ValueError("fabric needs protocols, topologies and arbitration")
        elif self.kind == "bridge":
            if not (self.upstream and self.downstream):
                raise ValueError("bridge needs upstream and downstream protocols")
        elif not (self.protocols and self.adapter_type):
            raise ValueError("adapter needs protocols and adapter_type")
        return self
