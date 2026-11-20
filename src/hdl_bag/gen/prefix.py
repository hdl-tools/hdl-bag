# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Project > Module prefix applied to BAG's own RTL (stage 2 of issue #106).

Three kinds of unit take the prefix: the in-house modules, the modules BAG derives from PULP,
and the sub-wrappers. The first two are renamed in the copied source by ``rewrite``; sub-wrapper
names are a user's words and are only ever joined in the generator (``unit``), never put in the
rename map, so a sub-wrapper called like a local signal cannot rename that signal. The vendored
PULP units, the formal checker units and the macros are not touched yet. What the checks call
prefixed and fixed (``checks/wrapper.py``) is what this module does.
"""

import re
from collections.abc import Mapping
from functools import cache
from types import MappingProxyType

from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.model import Spec
from hdl_bag.naming import PREFIXED_ORIGINS, Unit, emitted_units, unit_name

# One alternative per kind of token that must not be renamed, then the identifier itself, so a
# unit name is replaced only where it is a whole identifier outside a comment or string.
_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|`\w+|\\\S+|\$\w+"
    r"|'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[A-Za-z_][A-Za-z0-9_$]*",
    re.DOTALL,
)


@cache
def _applied() -> tuple[Unit, ...]:
    """The units the prefix is applied to."""
    return tuple(u for u in emitted_units().values() if u.origin in PREFIXED_ORIGINS)


@cache
def _applied_files() -> frozenset[str]:
    return frozenset(u.path for u in _applied())


@cache
def renames(prefix: str | None) -> Mapping[str, str]:
    """Old unit name -> new, for the in-house and derived units; empty for a blank prefix."""
    if not prefix:
        return MappingProxyType({})
    return MappingProxyType({u.name: unit_name(prefix, u.name) for u in _applied()})


def rewrite(text: str, mapping: Mapping[str, str]) -> str:
    """TEXT with every whole identifier in MAPPING replaced; comments and strings are kept."""
    if not mapping:
        return text
    return _TOKEN.sub(lambda m: mapping.get(m[0], m[0]), text)


def out_path(rel: str, prefix: str | None) -> str:
    """Where package file REL is written: its basename carries the prefix if it holds a unit
    that does. File names are never derived from module names (one file may hold several)."""
    if not prefix or rel not in _applied_files():
        return rel
    head, _, base = rel.rpartition("/")
    return f"{head}/{unit_name(prefix, base)}"


def unit(spec: Spec, name: str) -> str:
    """The generated name of the in-house or derived unit NAME; a typo fails here."""
    prefix = spec.project.module_prefix
    if name not in {u.name for u in _applied()}:
        raise GenerateError(f"{name} is not an in-house or derived unit")
    return unit_name(prefix, name)


def note(rel: str, text: str, prefix: str | None) -> str:
    """TEXT of derived file REL with a line saying the unit names carry PREFIX, after the SPDX
    line: its header names the module it was derived into, which the rename makes stale."""
    if not prefix or not rel.startswith("rtl/vendor/derived/"):
        return text
    first, _, rest = text.partition("\n")
    line = f'// Unit names carry the prefix "{prefix}_" (hdl-bag Project > Module prefix).'
    return f"{first}\n{line}\n{rest}"


def transform(rel: str, text: str, prefix: str | None) -> str:
    """The copy of packaged file REL as it is written: renamed units, plus the derived note."""
    return note(rel, rewrite(text, renames(prefix)), prefix)
