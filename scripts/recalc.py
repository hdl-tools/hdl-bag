# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Recalculate an .xlsx with LibreOffice headless so it holds cached formula values.

openpyxl writes formulas without results; tests that read derived values
(``data_only=True``) need a recalculated copy. Normally run inside the tools image:

    docker run --rm -v "$PWD:/work" ghcr.io/hdl-tools/hdl-bag-tools:main \
        uv run --no-project python scripts/recalc.py in.xlsx out.xlsx

It also runs wherever LibreOffice is installed: ``soffice`` or ``libreoffice`` on PATH, the
macOS application bundle, or whatever ``BAG_SOFFICE`` points at.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Force "always recalculate" for Excel 2007+ files; LibreOffice's default never does.
_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <item oor:path="/org.openoffice.Office.Calc/Formula/Load">
    <prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>
  </item>
</oor:items>
"""


_MAC_BUNDLE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")


def soffice() -> str:
    """The LibreOffice binary to run.

    macOS installs LibreOffice as an application bundle and puts nothing on PATH, so the
    bundle is checked after PATH. BAG_SOFFICE overrides both, for an install in neither
    place.
    """
    found = os.environ.get("BAG_SOFFICE") or shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    if _MAC_BUNDLE.is_file():
        return str(_MAC_BUNDLE)
    raise SystemExit(
        "soffice not found. Install LibreOffice (macOS: brew install --cask libreoffice), "
        "or run inside the hdl-bag tools image, or set BAG_SOFFICE to the binary."
    )


def recalc(src: Path, dst: Path) -> None:
    soffice_bin = soffice()
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(_XCU)
        outdir = Path(tmp) / "out"
        subprocess.run(
            [
                soffice_bin,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--calc",
                "--convert-to",
                "xlsx:Calc MS Excel 2007 XML",
                "--outdir",
                str(outdir),
                str(src),
            ],
            check=True,
            capture_output=True,
        )
        shutil.copyfile(outdir / (src.stem + ".xlsx"), dst)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: recalc.py IN.xlsx OUT.xlsx")
    recalc(Path(sys.argv[1]), Path(sys.argv[2]))
