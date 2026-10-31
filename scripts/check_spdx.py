# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Fail if any given file lacks the hdl-bag SPDX header in its first five lines."""

import sys
from pathlib import Path

SPDX = "SPDX-License-Identifier: Apache-2.0"


def main(paths: list[str]) -> int:
    missing = [p for p in paths if SPDX not in "".join(Path(p).read_text().splitlines(True)[:5])]
    for p in missing:
        print(f"{p}: missing '{SPDX}' header")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
