# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""What the determinism rule promises, checked against the source rather than an output file.

AGENTS.md: generated output is byte-identical across runs and `PYTHONHASHSEED`, with LF line
endings (`newline="\\n"`). On Linux and macOS `os.linesep` is already `"\\n"`, so a writer that
forgets `newline="\\n"` writes the same bytes here and CRLF on Windows: no amount of comparing
generated trees on this machine can catch it. Reading the calls can.
"""

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "hdl_bag"


def _text_writes_without_lf(root: Path) -> list[str]:
    """`path.write_text(...)` and `open(..., "w")` calls under ROOT that do not force LF."""
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            if name not in ("write_text", "open"):
                continue
            if name == "open":
                mode = next(
                    (a.value for a in node.args[1:2] if isinstance(a, ast.Constant)),
                    next(
                        (k.value.value for k in node.keywords
                         if k.arg == "mode" and isinstance(k.value, ast.Constant)),
                        "r",
                    ),
                )  # fmt: skip
                if not isinstance(mode, str) or "w" not in mode or "b" in mode:
                    continue
            if not any(k.arg == "newline" for k in node.keywords):
                out.append(f"{path.relative_to(root.parent.parent)}:{node.lineno} {name}")
    return out


def test_every_text_writer_forces_lf_line_endings() -> None:
    """One call in tests_gen/coverage.py was the only writer in src/ without it, and nothing
    failed on this machine: on POSIX the bytes are identical either way.

    `src/` is the scope because everything it writes is BAG's output. `scripts/` is not: its
    two writers without `newline` produce an install stamp under the tool prefix and a
    LibreOffice profile in a temp directory, neither of which anyone reads as output or
    commits. Widening this test to `scripts/` would assert an obligation they do not have.
    """
    assert _text_writes_without_lf(SRC) == []
