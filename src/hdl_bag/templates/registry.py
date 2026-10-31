# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Template registry: the only way generators and checks learn about templates."""

from functools import cache
from importlib.resources import files

from ruamel.yaml import YAML

from hdl_bag.templates.manifest import Kind, Manifest


class Registry:
    def __init__(self, manifests: list[Manifest]) -> None:
        self._by_name: dict[str, Manifest] = {}
        for m in manifests:
            if m.name in self._by_name:
                raise ValueError(f"duplicate template manifest '{m.name}'")
            self._by_name[m.name] = m

    def get(self, name: str) -> Manifest | None:
        return self._by_name.get(name)

    def names(self, kind: Kind | None = None) -> list[str]:
        return sorted(n for n, m in self._by_name.items() if kind is None or m.kind == kind)

    def adapter(self, adapter_type: str) -> Manifest | None:
        return next(
            (m for m in self._by_name.values() if m.adapter_type == adapter_type),
            None,
        )


@cache
def builtin_registry() -> Registry:
    """Load the built-in manifests shipped in hdl_bag/templates/builtin/<name>/."""
    yaml = YAML(typ="safe")
    manifests = []
    root = files("hdl_bag.templates") / "builtin"
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        manifest = entry / "manifest.yaml"
        if manifest.is_file():
            data = yaml.load(manifest.read_text(encoding="utf-8"))
            m = Manifest.model_validate(data)
            if m.name != entry.name:
                raise ValueError(f"manifest name '{m.name}' != directory '{entry.name}'")
            manifests.append(m)
    return Registry(manifests)
