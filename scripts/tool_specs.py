# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""What the pinned EDA tools are, per platform: their release archives, the layout of those
archives, and the PATH the tools need. The one place the Linux/macOS difference lives.

Pure on purpose: no network, no subprocess, nothing written. tests/test_install_tools.py
walks every entry here offline, so a wrong URL or a missed pin fails without the tools.
scripts/install_tools.py does the work.

Nothing here imports hdl_bag: these scripts run under ``uv run --no-project``, where the
package is not installed.
"""

import platform
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).parents[1]
LOCK = REPO / "tools.lock"

LINUX = "linux-x64"
DARWIN = "darwin-arm64"
PLATFORMS = (LINUX, DARWIN)

# Where a Mac installs by default. Absolute and fixed, so Verilator's baked-in
# ``configure --prefix`` is right and every machine's layout matches; the tools image
# passes --prefix /opt to reproduce its own paths exactly.
DEFAULT_PREFIX = "/opt/hdl-bag-tools"


@dataclass(frozen=True)
class Asset:
    """A release archive: where to get it, how deep its paths are, where it unpacks to.

    STRIP is leading path components to drop, as ``tar --strip-components``. DEST is
    relative to the install prefix. URL is a format template over the lock's keys.
    """

    url: str
    strip: int
    dest: str


@dataclass(frozen=True)
class Tool:
    """A pinned tool. ASSETS is empty for one built from source (Verilator).

    BIN_DIR is relative to the prefix and is what goes on PATH. TAIL puts it after the
    inherited PATH instead of before, so the OSS CAD Suite's own bundled copies of tools
    pinned separately here stay shadowed.
    """

    name: str
    lock_key: str
    executable: str
    bin_dir: str
    assets: dict[str, Asset]
    version_args: tuple[str, ...] = ("--version",)
    tail: bool = False
    source: str = ""
    # Whether the pin can be compared with what the tool reports. False for the OSS CAD
    # Suite, whose pin is a build date while its tools report their own versions, with no
    # relation between the two; --check then only confirms it is installed and prints the
    # version for the record. tests/flows/test_lint.py is what ties that version to the lock.
    versioned: bool = True


_SLANG = "https://github.com/MikePopoloski/slang/releases/download/{SLANG_VERSION}"
_VERIBLE = "https://github.com/chipsalliance/verible/releases/download/{VERIBLE_VERSION}"
_BENDER = "https://github.com/pulp-platform/bender/releases/download/{BENDER_VERSION}"
_SUITE = "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/{OSS_CAD_SUITE_DATE}"

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="verilator",
        lock_key="VERILATOR_VERSION",
        executable="verilator",
        bin_dir="verilator/bin",
        assets={},  # no prebuilt binary exists for either platform
        source="https://github.com/verilator/verilator.git",
    ),
    Tool(
        name="slang",
        lock_key="SLANG_VERSION",
        executable="slang",
        bin_dir="slang/bin",
        # Both assets are a single bare executable with no directory to strip, so strip=0
        # and the dest is the bin directory itself (#130).
        assets={
            LINUX: Asset(f"{_SLANG}/slang-linux-x86_64.tar.gz", 0, "slang/bin"),
            DARWIN: Asset(f"{_SLANG}/slang-macos-arm64.tar.gz", 0, "slang/bin"),
        },
    ),
    Tool(
        name="verible",
        lock_key="VERIBLE_VERSION",
        executable="verible-verilog-lint",
        bin_dir="verible/bin",
        # Each archive holds one top-level directory with bin/ inside it.
        assets={
            LINUX: Asset(
                f"{_VERIBLE}/verible-{{VERIBLE_VERSION}}-linux-static-x86_64.tar.gz", 1, "verible"
            ),
            DARWIN: Asset(f"{_VERIBLE}/verible-{{VERIBLE_VERSION}}-macOS.tar.gz", 1, "verible"),
        },
    ),
    Tool(
        name="bender",
        lock_key="BENDER_VERSION",
        executable="bender",
        bin_dir="bender/bin",
        # One top-level directory holding the binary beside its licences.
        assets={
            LINUX: Asset(f"{_BENDER}/bender-x86_64-unknown-linux-gnu.tar.xz", 1, "bender/bin"),
            DARWIN: Asset(f"{_BENDER}/bender-aarch64-apple-darwin.tar.xz", 1, "bender/bin"),
        },
    ),
    Tool(
        name="oss-cad-suite",
        lock_key="OSS_CAD_SUITE_DATE",
        executable="sby",
        bin_dir="oss-cad-suite/bin",
        # The archive carries its own oss-cad-suite/ directory, so nothing is stripped.
        assets={
            LINUX: Asset(f"{_SUITE}/oss-cad-suite-linux-x64-{{OSS_CAD_SUITE_COMPACT}}.tgz", 0, "."),
            DARWIN: Asset(
                f"{_SUITE}/oss-cad-suite-darwin-arm64-{{OSS_CAD_SUITE_COMPACT}}.tgz", 0, "."
            ),
        },
        version_args=("--version",),
        tail=True,
        versioned=False,
    ),
)

# Keys tools.lock holds that no tool here installs: the image's base and its Python, and uv,
# which the tools image installs itself and a developer running this script already has.
NOT_INSTALLED = ("UBUNTU_VERSION", "PYTHON_VERSION", "UV_VERSION")


class UnsupportedPlatform(Exception):
    """The host is not one of PLATFORMS."""


def read_lock(path: Path | None = LOCK, overrides: tuple[str, ...] = ()) -> dict[str, str]:
    """The pins, as ``KEY=VALUE`` lines with blank and ``#`` lines skipped — the same shape
    tools-image.yml passes to ``docker build`` and tests/flows/test_lint.py reads.

    OVERRIDES are further ``KEY=VALUE`` strings, applied last, so the Dockerfile can pass one
    pin on the command line without copying the whole lock into a build stage. PATH of None
    reads no file at all.
    """
    text = path.read_text(encoding="utf-8") if path is not None else ""
    lock = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{n}: {line!r} is not KEY=VALUE")
        key, value = line.split("=", 1)
        lock[key] = value
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"--set {item!r} is not KEY=VALUE")
        key, value = item.split("=", 1)
        lock[key] = value
    # The suite's asset names carry the date without dashes; the Dockerfile used `tr -d -`.
    lock["OSS_CAD_SUITE_COMPACT"] = lock.get("OSS_CAD_SUITE_DATE", "").replace("-", "")
    return lock


def host_platform() -> str:
    """The platform key for this machine. Raises UnsupportedPlatform with what it saw."""
    system, machine = platform.system(), platform.machine()
    if system == "Linux" and machine in ("x86_64", "AMD64"):
        return LINUX
    if system == "Darwin" and machine == "arm64":
        return DARWIN
    raise UnsupportedPlatform(
        f"{system} on {machine} is not supported; hdl-bag's tools are pinned for "
        f"{' and '.join(PLATFORMS)}. Pass --platform to override."
    )


def tool(name: str) -> Tool:
    """The tool called NAME. Raises KeyError naming every tool if there is none."""
    for t in TOOLS:
        if t.name == name:
            return t
    raise KeyError(f"no such tool {name!r}; hdl-bag pins {', '.join(t.name for t in TOOLS)}")


def asset_for(t: Tool, plat: str) -> Asset | None:
    """T's archive on PLAT, or None when it is built from source."""
    return t.assets.get(plat)


def asset_url(t: Tool, plat: str, lock: dict[str, str]) -> str:
    """T's resolved download URL on PLAT. Raises KeyError for a pin the lock lacks."""
    a = asset_for(t, plat)
    if a is None:
        raise ValueError(f"{t.name} is built from source on {plat}, so it has no URL")
    try:
        return a.url.format(**lock)
    except KeyError as exc:
        raise KeyError(f"{t.name}: tools.lock has no {exc.args[0]}") from exc


def path_entries(prefix: str, plat: str) -> tuple[list[str], list[str]]:
    """The bin directories to put before and after the inherited PATH, in that order.

    The order is the tools image's: the pinned tools first, the OSS CAD Suite last, so its
    own bundled Verilator never shadows the pinned one.
    """
    del plat  # the layout is the same on every platform; the prefix is what differs
    head = [f"{prefix}/{t.bin_dir}" for t in TOOLS if not t.tail]
    tail = [f"{prefix}/{t.bin_dir}" for t in TOOLS if t.tail]
    return head, tail


def path_line(prefix: str, plat: str, var: str = "$PATH") -> str:
    """The PATH value the tools need, with VAR standing for the inherited PATH.

    tests/test_install_tools.py asserts this equals the ``ENV PATH`` literal in
    tools/Dockerfile for ``("/opt", LINUX)``, which is what keeps the image's PATH and this
    installer from drifting apart.
    """
    head, tail = path_entries(prefix, plat)
    return ":".join([*head, var, *tail])
