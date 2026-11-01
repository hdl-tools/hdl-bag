# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Read parameter, port and struct-field names from the vendored PULP sources.

PULP declares every module header ANSI-style, one declaration per line, so a comment-
stripping text scan is enough. It needs no EDA tool, so the API check runs on every OS.
"""

import re
from functools import cache
from importlib.resources import files

from hdl_bag.templates.sources import VENDOR, vendored_units

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_PARAM = re.compile(r"\b(?:parameter|localparam)\b[^=;,]*?\b(\w+)\s*(?:\[[^\]]*\]\s*)*=")
_PORT = re.compile(r"^\s*(?:input|output|inout)\b.*?\b(\w+)\s*(?:\[[^\]]*\]\s*)*,?\s*$", re.M)


@cache
def _text(path: str) -> str:
    return _COMMENT.sub("", (files("hdl_bag") / VENDOR / path).read_text(encoding="utf-8"))


def _header(module: str) -> tuple[str, str]:
    """(parameter list, port list) of MODULE's header."""
    text = _text(vendored_units()[module])
    m = re.search(rf"^\s*module\s+{module}\b(?!_)", text, re.M)
    assert m, f"module {module} not found"
    i, params = m.end(), ""
    hash_at = text.find("#(", i)
    paren_at = text.find("(", i)
    if hash_at != -1 and hash_at < paren_at + 1:
        end = _close(text, hash_at + 1)
        params, i = text[hash_at + 2 : end], end + 1
    start = text.find("(", i)
    return params, text[start + 1 : _close(text, start)]


def _close(text: str, open_at: int) -> int:
    depth = 0
    for j in range(open_at, len(text)):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            return j
    raise AssertionError("unbalanced parentheses")


def parameters(module: str) -> set[str]:
    return set(_PARAM.findall(_header(module)[0]))


def ports(module: str) -> set[str]:
    return set(_PORT.findall(_header(module)[1]))


def struct_fields(package: str, typedef: str) -> set[str]:
    text = _text(vendored_units()[package])
    m = re.search(rf"typedef\s+struct\s+packed\s*\{{([^}}]*)\}}\s*{typedef}\s*;", text)
    assert m, f"{package}::{typedef} not found"
    return set(re.findall(r"(\w+)\s*;", m[1]))
