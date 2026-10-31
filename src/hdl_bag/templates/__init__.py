# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Template manifests and the registry."""

from hdl_bag.templates.manifest import Manifest
from hdl_bag.templates.registry import Registry, builtin_registry

__all__ = ["Manifest", "Registry", "builtin_registry"]
