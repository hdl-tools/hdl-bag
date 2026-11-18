# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""How scripts/recalc.py finds LibreOffice.

It is the one script a schema change has to run, and macOS installs LibreOffice as an
application bundle with nothing on PATH, so the lookup is the part worth pinning down. The
conversion itself needs LibreOffice and is exercised by the CI golden job.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).parents[1]


def _recalc() -> ModuleType:
    spec = importlib.util.spec_from_file_location("recalc", REPO / "scripts" / "recalc.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["recalc"] = module
    spec.loader.exec_module(module)
    return module


def test_bag_soffice_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _recalc()
    monkeypatch.setenv("BAG_SOFFICE", "/somewhere/soffice")
    monkeypatch.setattr(r.shutil, "which", lambda _: "/usr/bin/soffice")
    assert r.soffice() == "/somewhere/soffice"


def test_path_is_used_when_there_is_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _recalc()
    monkeypatch.delenv("BAG_SOFFICE", raising=False)
    monkeypatch.setattr(
        r.shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None
    )
    assert r.soffice() == "/usr/bin/soffice"


def test_the_macos_bundle_is_the_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """macOS puts nothing on PATH, so without this the script is unusable there even with
    LibreOffice installed."""
    r = _recalc()
    bundle = tmp_path / "soffice"
    bundle.write_text("")
    monkeypatch.delenv("BAG_SOFFICE", raising=False)
    monkeypatch.setattr(r.shutil, "which", lambda _: None)
    monkeypatch.setattr(r, "_MAC_BUNDLE", bundle)
    assert r.soffice() == str(bundle)


def test_nothing_installed_says_how_to_get_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The message has to name a way forward on every platform, not just the tools image."""
    r = _recalc()
    monkeypatch.delenv("BAG_SOFFICE", raising=False)
    monkeypatch.setattr(r.shutil, "which", lambda _: None)
    monkeypatch.setattr(r, "_MAC_BUNDLE", Path("/nonexistent/soffice"))
    with pytest.raises(SystemExit, match="brew install --cask libreoffice"):
        r.soffice()
