# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Project > Module prefix applied to every design unit, macro and header BAG emits (issue #106).

All five kinds of unit take the prefix: the in-house modules, the modules BAG derives from PULP,
the vendored PULP units, BAG's own formal units and the vendored ZipCPU units. They are renamed
in the copied source by ``rewrite``, and the file that holds them takes the prefix on its
basename (``out_path``). Sub-wrapper names are a user's words and are only ever joined in the
generator (``unit``), never put in the rename map, so a sub-wrapper called like a local signal
cannot rename that signal. Every macro a packaged file defines takes the prefix too, include
guards and the integrator knobs among them, as does the basename of each vendored header and the
string of the include lines that name it; include directories keep their names. The macros the
environment defines (``VERILATOR``, ``SYNTHESIS``, ...) are only tested, never defined in the
tree, so they are not in the map. What the checks call prefixed and fixed
(``checks/wrapper.py``) is what this module does.

The rename exists only in the generated copy: the packaged PULP and ZipCPU files are never
edited. A renamed upstream file says so on its first line (``note``).
"""

import re
from collections.abc import Mapping
from functools import cache
from importlib.resources import files
from types import MappingProxyType

from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.model import Spec
from hdl_bag.naming import Unit, emitted_units, unit_name
from hdl_bag.templates.sources import VENDOR, vendored_includes

_RTL = (".sv", ".v", ".svh", ".vh")
_UPSTREAM = (f"{VENDOR}/", "formal/vendor/zipcpu/")

# One alternative per kind of token, so each token is classified once, by where it stands, and
# looked up in exactly one map: the path of an include in the header map, the operand of a
# define, ifdef, ifndef, elsif or undef and a macro reference in the macro map, a bare identifier
# in the unit map. Macros and identifiers are separate namespaces, so a macro and a unit of one
# spelling are each renamed once. It is one pass on purpose: a second pass would see the first's
# output, and a unit named <prefix>_<other unit> would be prefixed twice. Comments and strings
# come first so nothing inside them is touched.
#
# Two known limits, neither present in the trees BAG vendors:
#   \\\S+ skips an escaped identifier, but IEEE 1800-2017 5.6.1 says \axi_cut<space> *is* axi_cut,
#   so a reference written that way is missed. Skipping is the safer of the two wrong answers.
#   Token pasting (x``FOO) is skipped whole, so a macro or unit name built by pasting is never
#   renamed. The packaged tree pastes 483 tokens on 210 lines, but only formal-argument names
#   and fragments (__lhs``__lhs_sep``id), none of them a macro or unit name; a test asserts it.
_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/"
    r"|(?P<inc>`include[ \t]+\")(?P<path>[^\"\n]+)\""
    r"|(?P<dir>`(?:define|ifdef|ifndef|elsif|undef)[ \t]+)(?P<dname>\w+)"
    r"|\"(?:\\.|[^\"\\\n])*\"|``\w*|`(?P<ref>\w+)|\\\S+|\$\w+"
    r"|'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[A-Za-z_][A-Za-z0-9_$]*",
    re.DOTALL,
)
_NONE: Mapping[str, str] = MappingProxyType({})


@cache
def _applied() -> tuple[Unit, ...]:
    """The units the prefix is applied to: every unit BAG emits."""
    return tuple(emitted_units().values())


@cache
def _applied_files() -> frozenset[str]:
    return frozenset(u.path for u in _applied()) | frozenset(vendored_includes().values())


@cache
def _macros() -> tuple[str, ...]:
    """Every macro a packaged RTL file defines, sorted. Read with ``_TOKEN`` so a name inside a
    comment or string is not one."""
    found: set[str] = set()
    stack = [files("hdl_bag") / "rtl", files("hdl_bag") / "formal"]
    while stack:
        for f in stack.pop().iterdir():
            if f.is_dir():
                stack.append(f)
            elif f.name.endswith(_RTL):
                for m in _TOKEN.finditer(f.read_text(encoding="utf-8")):
                    if m["dname"] and m["dir"].startswith("`define"):
                        found.add(m["dname"])
    return tuple(sorted(found))


@cache
def renames(prefix: str | None) -> Mapping[str, str]:
    """Old unit name -> new, for every unit BAG emits; empty for a blank prefix."""
    if not prefix:
        return MappingProxyType({})
    return MappingProxyType({u.name: unit_name(prefix, u.name) for u in _applied()})


@cache
def macro_renames(prefix: str | None) -> Mapping[str, str]:
    """Old macro name -> new, for every macro a packaged file defines; empty for a blank prefix."""
    if not prefix:
        return _NONE
    return MappingProxyType({n: unit_name(prefix, n) for n in _macros()})


@cache
def include_renames(prefix: str | None) -> Mapping[str, str]:
    """Include string -> new, for every vendored header; empty for a blank prefix."""
    if not prefix:
        return _NONE
    return MappingProxyType({
        inc: out_path(path, prefix).removeprefix(path.removesuffix(inc))
        for inc, path in vendored_includes().items()
    })  # fmt: skip


def rewrite(
    text: str,
    units: Mapping[str, str],
    macros: Mapping[str, str] = _NONE,
    includes: Mapping[str, str] = _NONE,
) -> str:
    """TEXT with every whole identifier in UNITS, every macro in MACROS and every include string
    in INCLUDES replaced, each by the map for its kind of token; comments and strings are kept."""
    if not (units or macros or includes):
        return text

    def sub(m: re.Match[str]) -> str:
        if m["path"] is not None:
            return f'{m["inc"]}{includes.get(m["path"], m["path"])}"'
        if m["dname"] is not None:
            return f"{m['dir']}{macros.get(m['dname'], m['dname'])}"
        if m["ref"] is not None:
            return f"`{macros.get(m['ref'], m['ref'])}"
        return units.get(m[0], m[0])

    return _TOKEN.sub(sub, text)


def out_path(rel: str, prefix: str | None) -> str:
    """Where package file REL is written: its basename carries the prefix if it holds a unit
    that does. File names are never derived from module names (one file may hold several)."""
    if not prefix or rel not in _applied_files():
        return rel
    head, _, base = rel.rpartition("/")
    return f"{head}/{unit_name(prefix, base)}"


def unit(spec: Spec, name: str) -> str:
    """The generated name of the unit NAME that BAG emits; a typo fails here."""
    if name not in emitted_units():
        raise GenerateError(f"{name} is not a unit BAG emits")
    return unit_name(spec.project.module_prefix, name)


def note(rel: str, text: str, prefix: str | None, changed: bool = True) -> str:
    """TEXT of file REL with a line saying the unit, macro and include-file names carry PREFIX.

    A derived file gets it after the SPDX line: its header names the module it was derived
    into, which the rename makes stale. A vendored PULP or ZipCPU RTL file gets it as line 1,
    above the upstream header (a copyright paragraph or a banner that a line after line 1 would
    split), but only if the rewrite CHANGED its text; the licence still travels in the file.
    """
    if not prefix:
        return text
    if rel.startswith("rtl/vendor/derived/"):
        first, _, rest = text.partition("\n")
        line = (f'// Unit, macro and include-file names carry the prefix "{prefix}_"'
                " (Project > Module prefix).")  # fmt: skip
        return f"{first}\n{line}\n{rest}"
    if changed and rel.startswith(_UPSTREAM) and rel.endswith(_RTL):
        line = ("// Modified by hdl-bag: design unit, macro and include-file names carry the "
                f'prefix "{prefix}_" (Project > Module prefix). No other change.')  # fmt: skip
        return f"{line}\n{text}"
    return text


def source_note(text: str, prefix: str | None) -> str:
    """The ZipCPU SOURCE.md as written: it says "unmodified", so under a prefix one line says
    what changed. Blank prefix: verbatim.

    The blank line keeps it a paragraph of its own: Markdown joins consecutive lines, so without
    it the sentence would render inside the "never edit these files by hand" paragraph.
    """
    if not prefix:
        return text
    line = ("In this output the design unit, macro and file names carry the prefix "
            f'"{prefix}_" (hdl-bag Project > Module prefix), as each file\'s first line '
            "states.")  # fmt: skip
    return f"{text}\n{line}\n"


def transform(rel: str, text: str, prefix: str | None) -> str:
    """The copy of packaged file REL as it is written: renamed units, macros and includes, and
    the notes. Only RTL files pass through the rewrite; licences and other prose are returned
    as is."""
    if not rel.endswith(_RTL):
        return text
    out = rewrite(text, renames(prefix), macro_renames(prefix), include_renames(prefix))
    return note(rel, out, prefix, out != text)
