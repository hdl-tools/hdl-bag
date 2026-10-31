# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Deterministic IR YAML dump and load."""

import io
from pathlib import Path

from ruamel.yaml import YAML

from hdl_bag.ir.model import Spec


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


def loads(text: str) -> Spec:
    return Spec.model_validate(YAML(typ="safe", pure=True).load(text))


def dump(spec: Spec, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(spec))


def load(path: Path) -> Spec:
    return loads(path.read_text(encoding="utf-8"))
