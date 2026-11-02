# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""AMBA protocol facts used by checks, resolve and generators.

Signal names are the lowercase AMBA names; the wrapper prefixes them per port.
"""

from enum import StrEnum
from typing import Final


class Protocol(StrEnum):
    AXI4 = "AXI4"
    AXI4_LITE = "AXI4-Lite"
    AHB_LITE = "AHB-Lite"
    APB4 = "APB4"
    APB3 = "APB3"


AXI_FAMILY: Final = frozenset({Protocol.AXI4, Protocol.AXI4_LITE})
APB_FAMILY: Final = frozenset({Protocol.APB4, Protocol.APB3})

# {proto} token of the wrapper naming patterns (D22).
PROTO_TOKEN: Final[dict[Protocol, str]] = {
    Protocol.AXI4: "axi",
    Protocol.AXI4_LITE: "axil",
    Protocol.AHB_LITE: "ahb",
    Protocol.APB4: "apb4",
    Protocol.APB3: "apb3",
}

_AXI_WIDTHS: Final = frozenset(2**n for n in range(5, 11))  # 32 .. 1024
# Legal data widths (bits) for ports and fabrics (D19).
DATA_WIDTHS: Final[dict[Protocol, frozenset[int]]] = {
    Protocol.AXI4: _AXI_WIDTHS,
    Protocol.AXI4_LITE: frozenset({32, 64}),
    Protocol.AHB_LITE: frozenset({32, 64}),
    Protocol.APB4: frozenset({8, 16, 32}),
    Protocol.APB3: frozenset({8, 16, 32}),
}
FABRIC_ADDR_WIDTH: Final = range(12, 65)
PORT_ADDR_WIDTH: Final = range(1, 65)
MAX_SLAVE_ID_WIDTH_WARN: Final = 16

# Miss responses that suit a fabric of each protocol (D18).
MISS_RESPONSES: Final[dict[Protocol, frozenset[str]]] = {
    Protocol.AXI4: frozenset({"DECERR", "SLVERR", "default-slave"}),
    Protocol.AXI4_LITE: frozenset({"DECERR", "SLVERR", "default-slave"}),
    Protocol.AHB_LITE: frozenset({"HRESP_ERROR", "default-slave"}),
    Protocol.APB4: frozenset({"PSLVERR", "default-slave"}),
    Protocol.APB3: frozenset({"PSLVERR", "default-slave"}),
}
# Error responses a slave of each protocol can report.
ERROR_RESPONSES: Final[dict[Protocol, frozenset[str]]] = {
    Protocol.AXI4: frozenset({"SLVERR", "DECERR"}),
    Protocol.AXI4_LITE: frozenset({"SLVERR", "DECERR"}),
    Protocol.AHB_LITE: frozenset({"HRESP_ERROR"}),
    Protocol.APB4: frozenset({"PSLVERR"}),
    Protocol.APB3: frozenset({"PSLVERR"}),
}

# Port_Options optional-signal columns and the protocols that have them.
OPTIONAL_SIGNALS: Final[dict[str, frozenset[Protocol]]] = {
    "PROT": frozenset({Protocol.AXI4, Protocol.AXI4_LITE, Protocol.AHB_LITE, Protocol.APB4}),
    "QOS": frozenset({Protocol.AXI4}),
    "REGION": frozenset({Protocol.AXI4}),
    "CACHE": frozenset({Protocol.AXI4}),
    "LOCK / excl": frozenset({Protocol.AXI4, Protocol.AHB_LITE}),
    "PSTRB": frozenset({Protocol.APB4}),
    "PSLVERR": frozenset({Protocol.APB3, Protocol.APB4}),
}


def signal_applies(column: str, protocol: Protocol, is_slave: bool) -> bool:
    """Whether a Port_Options optional-signal column exists on a port."""
    if column == "LOCK / excl" and protocol == Protocol.AHB_LITE and is_slave:
        return False  # D26
    return protocol in OPTIONAL_SIGNALS[column]


# Signals each optional column controls (removed from the wrapper when tied).
OPTIONAL_SIGNAL_NAMES: Final[dict[str, frozenset[str]]] = {
    "PROT": frozenset({"awprot", "arprot", "hprot", "hnonsec", "pprot"}),
    "QOS": frozenset({"awqos", "arqos"}),
    "REGION": frozenset({"awregion", "arregion"}),
    "CACHE": frozenset({"awcache", "arcache"}),
    "LOCK / excl": frozenset({"awlock", "arlock", "hmastlock"}),
    "PSTRB": frozenset({"pstrb"}),
    "PSLVERR": frozenset({"pslverr"}),
}

_AXI4: Final = (
    "awid", "awaddr", "awlen", "awsize", "awburst", "awlock", "awcache", "awprot", "awqos",
    "awregion", "awuser", "awvalid", "awready",
    "wdata", "wstrb", "wlast", "wuser", "wvalid", "wready",
    "bid", "bresp", "buser", "bvalid", "bready",
    "arid", "araddr", "arlen", "arsize", "arburst", "arlock", "arcache", "arprot", "arqos",
    "arregion", "aruser", "arvalid", "arready",
    "rid", "rdata", "rresp", "rlast", "ruser", "rvalid", "rready",
)  # fmt: skip
_AXI4_LITE: Final = (
    "awaddr", "awprot", "awvalid", "awready", "wdata", "wstrb", "wvalid", "wready",
    "bresp", "bvalid", "bready", "araddr", "arprot", "arvalid", "arready",
    "rdata", "rresp", "rvalid", "rready",
)  # fmt: skip
# hnonsec is the AMBA 5 AHB security signal (D27): AHB-Lite HPROT has no secure bit.
_AHB_MASTER: Final = (
    "haddr", "hburst", "hmastlock", "hprot", "hnonsec", "hsize", "htrans", "hwdata", "hwrite",
    "hrdata", "hready", "hresp",
)  # fmt: skip
# HMASTLOCK is not forwarded to slaves in v1 (D26).
_AHB_SLAVE: Final = (*(x for x in _AHB_MASTER if x != "hmastlock"), "hsel", "hreadyout")
_APB4: Final = (
    "paddr", "pprot", "psel", "penable", "pwrite", "pwdata", "pstrb", "pready", "prdata",
    "pslverr",
)  # fmt: skip
_APB3: Final = tuple(s for s in _APB4 if s not in {"pprot", "pstrb"})

_ID_SIGNALS: Final = frozenset({"awid", "bid", "arid", "rid"})
_USER_SIGNALS: Final = frozenset({"awuser", "wuser", "buser", "aruser", "ruser"})


def port_signals(
    protocol: Protocol,
    is_slave: bool,
    *,
    has_id: bool = True,
    has_user: bool = False,
    removed: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Signal names of one bus port as exposed at the wrapper (lowercase AMBA names)."""
    base = {
        Protocol.AXI4: _AXI4,
        Protocol.AXI4_LITE: _AXI4_LITE,
        Protocol.AHB_LITE: _AHB_SLAVE if is_slave else _AHB_MASTER,
        Protocol.APB4: _APB4,
        Protocol.APB3: _APB3,
    }[protocol]
    drop = set(removed)
    if not has_id:
        drop |= _ID_SIGNALS
    if not has_user:
        drop |= _USER_SIGNALS
    return tuple(s for s in base if s not in drop)


# Signals driven towards the slave (by a master or by the interconnect); every other
# signal of a port is a response, driven by the slave.
_TOWARDS_SLAVE: Final[frozenset[str]] = frozenset({
    *(s for s in _AXI4 if s[0] in "aw" and not s.endswith("ready")), "bready", "rready",
    *(s for s in _AHB_SLAVE if s not in {"hrdata", "hreadyout", "hresp"}), "hmastlock",
    *(s for s in _APB4 if s not in {"pready", "prdata", "pslverr"}),
})  # fmt: skip


def family_signals(protocol: Protocol) -> frozenset[str]:
    """Every signal name of PROTOCOL's family (AXI, AHB or APB). Templates may ask for a signal
    a bus lacks, e.g. AxLEN on AXI4-Lite or PPROT on APB3, and take a default."""
    family = {
        Protocol.AXI4: (Protocol.AXI4, Protocol.AXI4_LITE),
        Protocol.AXI4_LITE: (Protocol.AXI4, Protocol.AXI4_LITE),
        Protocol.AHB_LITE: (Protocol.AHB_LITE,),
        Protocol.APB4: (Protocol.APB4, Protocol.APB3),
        Protocol.APB3: (Protocol.APB4, Protocol.APB3),
    }[protocol]
    return frozenset(
        s for p in family for slave in (False, True) for s in port_signals(p, slave, has_user=True)
    )


def towards_slave(signal: str, is_slave: bool) -> bool:
    """True if SIGNAL is driven by the master side (master or interconnect) on a port.

    HREADY is the one signal whose direction depends on the port: the interconnect drives
    it to a master as a response, and to a slave as an input."""
    if signal == "hready":
        return is_slave
    return signal in _TOWARDS_SLAVE


def signal_width(signal: str, addr_width: int, data_width: int, id_width: int = 0,
                 user_width: int = 0) -> int:  # fmt: skip
    """Bit width of one AMBA signal."""
    if signal.endswith("addr"):
        return addr_width
    if signal.endswith("data"):
        return data_width
    if signal in {"wstrb", "pstrb"}:
        return data_width // 8
    if signal in _ID_SIGNALS:
        return id_width
    if signal in _USER_SIGNALS:
        return user_width
    return _FIXED_WIDTHS.get(signal[2:] if signal[:2] in {"aw", "ar"} else signal, 1)


_FIXED_WIDTHS: Final[dict[str, int]] = {
    "len": 8, "size": 3, "burst": 2, "cache": 4, "prot": 3, "qos": 4, "region": 4,
    "bresp": 2, "rresp": 2,
    "hburst": 3, "hprot": 4, "hsize": 3, "htrans": 2, "pprot": 3,
}  # fmt: skip

# Port_Options tie-default values (AMBA recommended defaults); tie-0 is zero.
# A value of -1 means all ones; TIE_WRITE_STROBE means "all ones on writes, zero on
# reads" (APB4 requires PSTRB low for reads).
TIE_WRITE_STROBE: Final = -2
TIE_DEFAULTS: Final[dict[str, int]] = {
    "awcache": 0b0011, "arcache": 0b0011,
    "hprot": 0b0011,
    "pstrb": TIE_WRITE_STROBE,
}  # fmt: skip
# D28: a tied security attribute fails closed (non-secure), for tie-0 and tie-default.
# The value is ORed into the tie value of the signal.
TIE_NONSECURE: Final[dict[str, int]] = {
    "hnonsec": 1, "pprot": 0b010, "awprot": 0b010, "arprot": 0b010,
}  # fmt: skip


def tie_value(signal: str, mode: str) -> int:
    """Value of SIGNAL tied by a Port_Options column set to MODE (tie-0 / tie-default)."""
    value = TIE_DEFAULTS.get(signal, 0) if mode == "tie-default" else 0
    return value | TIE_NONSECURE.get(signal, 0)
