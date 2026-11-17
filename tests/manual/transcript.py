# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Replay the console transcripts shown in the manual (after hdl-vet's tests/transcript.py).

A transcript is a text file. A line that starts with "$ " is a command; the lines after it,
up to the next command, are what the command prints. Example:

    $ bag check spec.xlsx
    warning: ...
    spec.xlsx: 0 error(s), 2 warning(s)

Rules of the format:

- Commands: `bag ...` (run in this process through `hdl_bag.cli.main`), `cd DIR`,
  `cat FILE`, `cp SRC DST` (one file), `ls [DIR]` (names in sorted order, one per line, a
  directory with a trailing "/"). Words are split as a POSIX shell does; there are no
  pipes, variables or wildcards.
- `bag lint`, `bag sim`, `bag formal`, `bag flow` and `bag regress` need the EDA
  tools. They may appear
  only in a transcript under `examples/console/eda/`, which the EDA test replays in the
  tools image; the host test replays the others.
- A transcript starts in a fresh copy of `docs/manual/examples`. Commands of one transcript
  share that copy and the current directory, so a later command sees what an earlier one
  wrote. Every path stays inside the copy.
- What `bag` writes to stdout and stderr is shown as one stream, in the order it was
  written. A non-zero exit status is shown as a last line `[exit status N]`.
- Four things are made the same on every machine before comparing: a backslash reads "/"
  (so a transcript never contains a backslash); the path of the copy reads `<examples>`;
  the version of hdl-bag reads `<version>`; spaces at the end of a line and blank lines at
  the end of a command's output are dropped.
- Output that cannot be the same everywhere is refused, with a ValueError: `--help` and
  click usage messages (they follow the terminal width), a Python traceback, a Python
  warning, and any other path of this machine. Choose another example, or fix the message.

With the environment variable BAG_MANUAL_UPDATE=1, `replay` rewrites the transcript with
what the commands print now. Use it to write a new transcript or after an intended change
of output; read the resulting diff before committing it.
"""

import contextlib
import io
import os
import re
import shlex
import shutil
import tempfile
import warnings
from collections.abc import Iterator
from pathlib import Path

import hdl_bag
from hdl_bag.cli import main

REPO = Path(__file__).resolve().parents[2]
MANUAL = REPO / "docs" / "manual"
EXAMPLES = MANUAL / "examples"
UPDATE = os.environ.get("BAG_MANUAL_UPDATE") == "1"
# Commands that run EDA tools: only in transcripts under examples/console/eda/.
EDA_COMMANDS = frozenset({"lint", "sim", "formal", "flow", "regress"})

_VERSION = re.compile(r"(?<![\w.])" + re.escape(hdl_bag.__version__) + r"(?![\w.])")


def parse(text: str) -> list[tuple[str, list[str]]]:
    """[(command, expected output lines)] of a transcript."""
    steps: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("$ "):
            steps.append((line[2:].strip(), []))
        elif steps:
            steps[-1][1].append(line)
        elif line.strip():
            raise ValueError("a transcript starts with a command line ('$ ...')")
    for _, lines in steps:
        while lines and not lines[-1].strip():
            lines.pop()
    return steps


@contextlib.contextmanager
def _cwd(path: Path) -> Iterator[None]:
    before = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


def _inside(root: Path, cwd: Path, word: str) -> Path:
    path = (cwd / word).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"{word!r} leaves the copy of the examples")
    return path


def _bag(args: list[str], cwd: Path, eda: bool) -> tuple[str, int]:
    if any(a in ("-h", "--help") for a in args):
        raise ValueError("a transcript must not show --help")
    if args and args[0] in EDA_COMMANDS and not eda:
        raise ValueError(f"bag {args[0]} needs the EDA tools: put it in examples/console/eda/")
    buf = io.StringIO()
    with (
        _cwd(cwd),
        contextlib.redirect_stdout(buf),
        contextlib.redirect_stderr(buf),
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        try:
            main.main(args=args, prog_name="bag", standalone_mode=True)
            status = 0
        except SystemExit as e:
            status = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    if caught:
        raise ValueError(f"Python warning while running bag (a terminal would show it): "
                         f"{caught[0].message}")  # fmt: skip
    return buf.getvalue(), int(status or 0)


def _unstable(text: str) -> str | None:
    """Why this output cannot be shown: it would differ on another machine or Python."""
    if "Traceback (most recent call last)" in text:
        return "a Python traceback"
    if text.startswith("Usage:"):
        return "a click usage message (its line breaks follow the terminal width)"
    for place in (Path.home(), Path(tempfile.gettempdir())):
        if place.as_posix().lower() in text.lower():
            return f"a path of this machine ({place.as_posix()})"
    return None


def _normalise(text: str, root: Path) -> list[str]:
    """The same text on every machine: "/" for a backslash, `<examples>`, `<version>`."""
    text = text.replace("\\\\", "\\").replace("\\", "/")
    for spelling in sorted({root.as_posix(), root.as_posix().replace("/", "//")},
                           key=len, reverse=True):  # fmt: skip
        text = re.sub(re.escape(spelling), "<examples>", text, flags=re.IGNORECASE)
    text = _VERSION.sub("<version>", text)
    reason = _unstable(text)
    if reason:
        raise ValueError(f"the output contains {reason}; it cannot be shown:\n{text}")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def run(command: str, root: Path, cwd: Path, eda: bool = False) -> tuple[list[str], Path]:
    """Run one command; (normalised output lines, the current directory after it)."""
    words = shlex.split(command)
    if not words:
        raise ValueError("empty command")
    name, args = words[0], words[1:]
    if name == "bag":
        text, status = _bag(args, cwd, eda)
        lines = _normalise(text, root)
        if status:
            lines.append(f"[exit status {status}]")
        return lines, cwd
    if name == "cd" and len(args) == 1:
        target = _inside(root, cwd, args[0])
        if not target.is_dir():
            raise ValueError(f"cd: {args[0]!r} is not a directory")
        return [], target
    if name == "cat" and len(args) == 1:
        return _normalise(_inside(root, cwd, args[0]).read_text(encoding="utf-8"), root), cwd
    if name == "cp" and len(args) == 2:
        target = _inside(root, cwd, args[1])
        if target.is_dir():
            target = target / Path(args[0]).name
        shutil.copyfile(_inside(root, cwd, args[0]), target)
        return [], cwd
    if name == "ls" and len(args) <= 1:
        folder = _inside(root, cwd, args[0] if args else ".")
        return [p.name + ("/" if p.is_dir() else "")
                for p in sorted(folder.iterdir(), key=lambda p: p.name)], cwd  # fmt: skip
    raise ValueError(f"not a transcript command: {command!r}")


def render(steps: list[tuple[str, list[str]]]) -> str:
    out: list[str] = []
    for command, lines in steps:
        out.append("$ " + command)
        out.extend(lines)
    return "\n".join(out) + "\n"


def replay_text(text: str, tmp: Path, eda: bool = False) -> tuple[str, str]:
    """(the transcript as written, the transcript as the commands print it now)."""
    root = (tmp / "examples").resolve()
    shutil.copytree(EXAMPLES, root)
    cwd = root
    steps = parse(text)
    actual: list[tuple[str, list[str]]] = []
    for command, _ in steps:
        lines, cwd = run(command, root, cwd, eda)
        actual.append((command, lines))
    return render(steps), render(actual)


def replay(path: Path, tmp: Path, eda: bool = False) -> tuple[str, str]:
    """As `replay_text`, for a transcript file; rewrites the file in update mode."""
    expected, actual = replay_text(path.read_text(encoding="utf-8"), tmp, eda)
    if UPDATE and expected != actual:
        path.write_bytes(actual.encode("utf-8"))
        return actual, actual
    return expected, actual


def commands(text: str) -> list[str]:
    return [command for command, _ in parse(text)]
