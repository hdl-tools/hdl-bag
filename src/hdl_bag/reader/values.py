# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Parsers for raw cell values. Each raises ValueError with a short reason."""

import re

from hdl_bag.reader.model import RawValue

_HEX = re.compile(r"0[xX][0-9a-fA-F]+(?:_?[0-9a-fA-F]+)*")
_IDENT = re.compile(r"[a-z][a-z0-9_]*")


def parse_hex(value: RawValue) -> int:
    """Hex text with a 0x prefix and optional underscores, e.g. 0x4000_0000."""
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"{value!r} is not hex like 0x4000_0000")
    return int(value.replace("_", ""), 16)


def parse_int(value: RawValue, minimum: int | None = None) -> int:
    """An integer cell. Integral floats (Excel stores numbers as floats) are accepted."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{value!r} is not an integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{value!r} is not an integer")
        value = int(value)
    if isinstance(value, str):
        if not re.fullmatch(r"-?[0-9]+", value):
            raise ValueError(f"{value!r} is not an integer")
        value = int(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{value} is less than {minimum}")
    return value


def parse_yes_no(value: RawValue) -> bool:
    if value == "Y":
        return True
    if value == "N":
        return False
    raise ValueError(f"{value!r} is not Y or N")


def is_identifier(value: RawValue) -> bool:
    """Lowercase SystemVerilog identifier, as required for instance and port names."""
    return isinstance(value, str) and _IDENT.fullmatch(value) is not None


def parse_connection(value: RawValue) -> tuple[str, str | None, int | None]:
    """User_Ports > Connection -> (kind, target, tie value).

    expose | open | tie-0 | tie-1 | 0x<hex> | extra input <name> | clock <domain> |
    reset <domain>. tie-1 is returned as -1 (all ones; the width is applied later).
    """
    if not isinstance(value, str):
        raise ValueError(f"{value!r} is not a connection")
    if value in ("expose", "open"):
        return value, None, None
    if value in ("tie-0", "tie-1"):
        return "tie", None, -1 if value == "tie-1" else 0
    if _HEX.fullmatch(value):
        return "tie", None, parse_hex(value)
    for prefix, kind in (("extra input ", "extra-input"), ("clock ", "clock"), ("reset ", "reset")):
        if value.startswith(prefix) and value[len(prefix) :].strip():
            return kind, value[len(prefix) :].strip(), None
    raise ValueError(
        f"{value!r} is not expose, open, tie-0, tie-1, 0x<hex>, extra input <name>, "
        "clock <domain> or reset <domain>"
    )
