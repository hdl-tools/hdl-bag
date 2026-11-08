# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Vendor ZipCPU's AXI4-Lite and APB formal property files (D60).

    uv run --no-project python scripts/vendor_zipcpu.py          # refresh the vendored files
    uv run --no-project python scripts/vendor_zipcpu.py --check  # fail if they are out of date

The files come from wb2axip's ``bench/formal`` at the pinned commit, byte for byte. Each
carries an Apache-2.0 header, and the repository's README licenses it under Apache 2. The
repository has no licence text file, so ``LICENSE`` here is the standard Apache-2.0 text
(hdl-bag's own, which is that text unmodified). Only the AXI4-Lite and APB files are
taken: the public full-AXI4 files (faxi_master, faxi_slave) are deliberately
non-functional subsets, so BAG's AXI4 properties are its own (D60). The script needs only
the standard library.
"""

import argparse
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "src" / "hdl_bag" / "formal" / "vendor" / "zipcpu"
COMMIT = "2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b"
URL = "https://raw.githubusercontent.com/ZipCPU/wb2axip/{commit}/bench/formal/{name}"
FILES = ("faxil_master.v", "faxil_slave.v", "fapb_master.v", "fapb_slave.v")
LICENCE_LINE = "Apache License, Version 2.0"
SOURCE = (
    "# Vendored ZipCPU formal properties\n\n"
    "Files from <https://github.com/ZipCPU/wb2axip> `bench/formal/` at commit\n"
    f"`{COMMIT}`, unmodified. Copyright Gisselquist Technology, LLC; licensed under\n"
    "the Apache License, Version 2.0 (see each file's header and `LICENSE`).\n"
    "Refresh with `scripts/vendor_zipcpu.py`; never edit these files by hand.\n"
)


def _fetch(url: str) -> bytes:
    """URL's content, with one retry; a clear message, not a traceback, if it fails."""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return bytes(r.read())
        except OSError as exc:  # URLError and HTTPError are OSErrors
            if attempt == 2:
                msg = f"cannot fetch {url}: {exc} (network, or the commit moved?)"
                raise SystemExit(msg) from exc
    raise AssertionError("unreachable")


def expected() -> dict[str, bytes]:
    """Every file of the vendored directory, as it should be."""
    out: dict[str, bytes] = {}
    for name in FILES:
        data = _fetch(URL.format(commit=COMMIT, name=name))
        if LICENCE_LINE.encode() not in data[:2000]:
            raise SystemExit(f"{name}: no '{LICENCE_LINE}' header at {COMMIT}; stop and check")
        out[name] = data
    # wb2axip has no licence text file, so this is the standard Apache-2.0 text, taken from
    # hdl-bag's own LICENSE (that text, unmodified). Editing hdl-bag's LICENSE therefore makes
    # --check fail here too, on purpose: the vendored copy must stay the plain licence.
    out["LICENSE"] = (REPO / "LICENSE").read_bytes()
    out["SOURCE.md"] = SOURCE.encode()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the files differ")
    args = parser.parse_args()
    files = expected()
    if args.check:
        have = {p.name for p in DEST.iterdir()} if DEST.is_dir() else set()
        stale = sorted(n for n in files if not (DEST / n).is_file()
                       or (DEST / n).read_bytes() != files[n])  # fmt: skip
        extra = sorted(have - set(files))
        for n in stale + extra:
            print(f"{DEST / n}: {'stale' if n in stale else 'not vendored'}")
        return 1 if stale or extra else 0
    DEST.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (DEST / name).write_bytes(data)
    print(f"vendored {len(FILES)} files at {COMMIT[:7]} into {DEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
