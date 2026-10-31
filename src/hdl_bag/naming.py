# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Names shared by the checks and the generators, so both always agree."""


def flat_signal_name(pattern: str, port: str, proto: str, signal: str, prefix: str | None) -> str:
    """Wrapper port name of SIGNAL on PORT (Wrapper > Signal naming pattern).

    A Top-level prefix replaces "{port}_{proto}" in the pattern; patterns without that
    sequence ignore the prefix.
    """
    stem = pattern.replace("{port}_{proto}", prefix) if prefix else pattern
    return stem.replace("{port}", port).replace("{proto}", proto).replace("{signal}", signal)
