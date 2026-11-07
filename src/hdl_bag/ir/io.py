# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Deterministic IR YAML dump and load."""

import io
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from hdl_bag.ir.model import Spec
from hdl_bag.template import SCHEMA_VERSION


class IrError(ValueError):
    """An IR this version of BAG cannot read: another schema, or not an IR at all."""


def _yaml() -> YAML:
    yaml = YAML(typ="rt", pure=True)
    yaml.default_flow_style = False
    yaml.width = 4096  # never wrap: line breaks must not depend on content length
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.representer.add_representer(
        type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null")
    )
    return yaml


def dumps(spec: Spec) -> str:
    """The canonical text of an IR: same spec, same bytes (LF, trailing newline)."""
    data = spec.model_dump(mode="json", by_alias=True)
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def loads(text: str, source: str = "IR") -> Spec:
    """The IR in TEXT. Raises IrError, naming SOURCE, for another schema or a malformed IR."""
    try:
        data = YAML(typ="safe", pure=True).load(text)
    except YAMLError as exc:
        raise IrError(f"{source} is not a valid IR: {exc}") from exc
    if not isinstance(data, dict):
        raise IrError(f"{source} is not a valid IR: expected a mapping at the top")
    schema = str(data.get("schema"))
    if schema != SCHEMA_VERSION:
        raise IrError(
            f"{source} is schema {schema}; this bag reads schema {SCHEMA_VERSION}. Update its "
            "workbook (docs/schema-changelog.md), then write the IR again with bag ir"
        )
    try:
        return Spec.model_validate(data)
    except ValidationError as exc:
        where = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        more = f" (and {exc.error_count() - 5} more)" if exc.error_count() > 5 else ""
        raise IrError(f"{source} is not a valid IR: {where}{more}") from exc


def dump(spec: Spec, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(spec))


def load(path: Path) -> Spec:
    return loads(path.read_text(encoding="utf-8"), path.name)
