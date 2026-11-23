# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""How scripts/release_notes.py cuts one version's section out of CHANGELOG.md.

The release workflow runs it before anything is published, so a tag without a changelog
section must fail loudly there. The tests use fixture changelogs, not the real file.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).parents[1]

CHANGELOG = """\
# Changelog

## [Unreleased]

Nothing yet.

## [1.0.0rc1] - 2026-01-01

Release candidate.

## [10.0.0] - 2026-02-01

Ten.

## [1.0.0] - 2026-03-01

### Added

- The first release.

## [0.9.0] - 2025-12-01

Older.
"""


def _release_notes() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_notes", REPO / "scripts" / "release_notes.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_notes"] = module
    spec.loader.exec_module(module)
    return module


def _run(tmp_path: Path, text: str, version: str) -> int:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    code: int = _release_notes().main([version, str(path)])
    return code


def test_a_section_is_printed_up_to_the_next_version_heading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, CHANGELOG, "1.0.0") == 0
    assert capsys.readouterr().out == (
        "## [1.0.0] - 2026-03-01\n\n### Added\n\n- The first release.\n"
    )


def test_the_last_section_runs_to_the_end_of_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, CHANGELOG, "0.9.0") == 0
    assert capsys.readouterr().out == "## [0.9.0] - 2025-12-01\n\nOlder.\n"


def test_a_version_that_prefixes_another_does_not_match_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only_longer = "## [1.0.0rc1] - 2026-01-01\n\nRC.\n\n## [10.0.0] - 2026-02-01\n\nTen.\n"
    assert _run(tmp_path, only_longer, "1.0.0") == 1
    assert capsys.readouterr().out == ""


def test_an_absent_version_fails_naming_the_file_and_the_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, CHANGELOG, "2.0.0") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "CHANGELOG.md" in captured.err
    assert "[2.0.0]" in captured.err


def test_a_missing_changelog_fails_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _release_notes().main(["1.0.0", str(tmp_path / "CHANGELOG.md")]) == 1
    assert "CHANGELOG.md" in capsys.readouterr().err
