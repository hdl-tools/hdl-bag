# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Install the pinned EDA tools (tools.lock) for this platform.

    uv run --no-project python scripts/install_tools.py             # every tool
    uv run --no-project python scripts/install_tools.py slang       # just one
    uv run --no-project python scripts/install_tools.py --list      # resolve only, no network
    uv run --no-project python scripts/install_tools.py --check     # versions against the lock
    uv run --no-project python scripts/install_tools.py --print-path

Prebuilt archives come from each project's own releases; Verilator publishes none for either
platform and is built from source. scripts/tool_specs.py holds the per-platform table, so
this file is only the work: fetch, verify the layout, swap into place, record what landed.

The tools image runs this with ``--prefix /opt`` and one ``--set`` per pin, so a developer's
Mac and CI install the same way from the same table.

This never runs sudo or a package manager. If the prefix is not writable, or a build
dependency is missing, it prints the exact command to run and stops.
"""

import argparse
import copy
import http.client
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zlib
from collections.abc import Iterator
from pathlib import Path

from tool_specs import (
    DARWIN,
    DEFAULT_PREFIX,
    LOCK,
    NOT_INSTALLED,
    PLATFORMS,
    TOOLS,
    Asset,
    Tool,
    UnsupportedPlatform,
    asset_for,
    asset_url,
    host_platform,
    path_line,
    read_lock,
    tool,
)

# Homebrew formulae Verilator's build needs on macOS. Apple's bison is 2.3 and Verilator
# wants 3 or newer; flex has to be Homebrew's because macOS's own emits
# `yy_create_buffer(FILE *, yy_size_t)`, which does not match Verilator's src/V3PreLex.h and
# leaves V3PreLex's symbols undefined at link. Homebrew's own verilator formula depends on
# flex for exactly that reason, and this mirrors the rest of its recipe: autoreconf rather
# than autoconf, and no CPPFLAGS or LDFLAGS pointing at the keg.
BREW_BUILD_DEPS = ("autoconf", "automake", "bison", "flex", "help2man")

# A stalled connection must not hang the install for ever, and nor must a wedged tool.
DOWNLOAD_TIMEOUT_S = 60
VERSION_TIMEOUT_S = 30


def _say(*parts: str) -> None:
    """Progress and diagnostics go to stderr, so --list and --print-path own stdout."""
    print(*parts, file=sys.stderr, flush=True)


def _stamp(prefix: Path, name: str) -> Path:
    return prefix / ".hdl-bag" / f"{name}.txt"


def _installed(prefix: Path, t: Tool, want: str) -> bool:
    """Whether T is already installed at WANT, with its executable present."""
    stamp = _stamp(prefix, t.name)
    if not stamp.is_file() or stamp.read_text(encoding="utf-8").strip() != want:
        return False
    return (prefix / t.bin_dir / t.executable).exists()


def _fetch(url: str, dest: Path) -> None:
    """Download URL to DEST.

    Every failure becomes a RuntimeError naming the URL: urllib's own messages do not
    include it, so "HTTP Error 404" alone leaves no way to tell which pin is wrong.
    """
    _say(f"    fetching {url}")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError(f"cannot download {url}: {exc}") from exc


def _members(tar: tarfile.TarFile, strip: int) -> Iterator[tarfile.TarInfo]:
    """TAR's members with STRIP leading path components removed.

    A member left with no name is dropped, which is what ``tar --strip-components`` does and
    is exactly how the slang archive — one bare executable — ends up extracting nothing when
    the strip level is wrong (#130). The caller checks the result is not empty.
    """
    for m in tar:
        parts = Path(m.name).parts
        if len(parts) <= strip:
            continue
        # A shallow copy, not a fresh TarInfo: tarfile locates a member's bytes through
        # offset_data, which a newly built TarInfo does not carry, and extracting with one
        # silently writes garbage rather than failing.
        kept = copy.copy(m)
        kept.name = "/".join(parts[strip:])
        # tar strips a hard link's target too. None of the pinned archives contains one
        # (the suite, which has many links, is extracted whole at strip=0), so this only
        # keeps a future one from pointing at a path that was stripped away.
        if kept.islnk():
            link = Path(kept.linkname).parts
            kept.linkname = "/".join(link[strip:]) if len(link) > strip else kept.linkname
        yield kept


def _unpack(archive: Path, into: Path, strip: int, what: str) -> None:
    """Extract ARCHIVE under INTO, dropping STRIP leading components.

    ``filter="data"`` refuses absolute paths, ``..`` and device files, so a malformed or
    hostile archive cannot write outside INTO.
    """
    if sys.version_info < (3, 11, 4):
        raise RuntimeError(
            f"extracting safely needs Python 3.11.4 or newer (this is "
            f"{'.'.join(str(n) for n in sys.version_info[:3])}); run under `uv run`, which "
            f"uses the version tools.lock pins"
        )
    try:
        # Streamed, not getmembers(): the list form reads the whole compressed archive once
        # to build it and extractall reads it again, which is half a gigabyte twice for the
        # OSS CAD Suite.
        with tarfile.open(archive) as tar:
            tar.extractall(into, members=_members(tar, strip), filter="data")
    except (tarfile.TarError, EOFError, lzma.LZMAError, zlib.error) as exc:
        raise RuntimeError(
            f"{what}: {archive.name} did not extract ({exc}); if the download was cut short, "
            f"run this again"
        ) from exc
    if not any(into.iterdir()):
        raise RuntimeError(
            f"{what}: nothing left to extract after dropping {strip} path component(s) — "
            f"the archive's layout changed; check Asset.strip in scripts/tool_specs.py"
        )


def _swap_in(staged: Path, prefix: Path, dest: str) -> list[str]:
    """Move everything in STAGED to PREFIX/DEST, replacing what is there. Returns the names."""
    target = (prefix / dest).resolve() if dest != "." else prefix.resolve()
    target.mkdir(parents=True, exist_ok=True)
    moved = []
    for item in sorted(staged.iterdir()):
        final = target / item.name
        if final.is_dir() and not final.is_symlink():
            shutil.rmtree(final)
        elif final.exists() or final.is_symlink():
            final.unlink()
        shutil.move(str(item), str(final))
        moved.append(item.name)
    return moved


def _install_asset(t: Tool, a: Asset, url: str, prefix: Path, want: str) -> None:
    """Fetch and unpack T's archive into PREFIX, then record WANT.

    The staging directory lives inside the prefix, not in the system temporary directory, so
    moving the unpacked tree into place is a rename on one filesystem rather than a copy
    across two. That keeps the swap close to atomic and keeps a gigabyte of unpacked OSS CAD
    Suite out of /tmp. Only the archive itself goes to the temporary directory.
    """
    prefix.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hdl-bag-dl-") as dl:
        archive = Path(dl) / Path(url).name
        _fetch(url, archive)
        with tempfile.TemporaryDirectory(prefix=".hdl-bag-stage-", dir=prefix) as tmp:
            staged = Path(tmp) / "staged"
            staged.mkdir()
            _unpack(archive, staged, a.strip, t.name)
            # Drop the stamp first: if the swap dies part-way the tree is incomplete, and a
            # stamp still matching `want` would make the next run report "already at".
            _stamp(prefix, t.name).unlink(missing_ok=True)
            names = _swap_in(staged, prefix, a.dest)
    exe = prefix / t.bin_dir / t.executable
    if not exe.exists():
        raise RuntimeError(
            f"{t.name}: unpacked {', '.join(names)} but {exe} is not there — the archive's "
            f"layout changed; check Asset.strip and Asset.dest in scripts/tool_specs.py"
        )
    _say(f"    installed {', '.join(names)} -> {prefix / a.dest}")
    _record(prefix, t, want)


def _record(prefix: Path, t: Tool, want: str) -> None:
    stamp = _stamp(prefix, t.name)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{want}\n", encoding="utf-8")


def _brew_prefix(formula: str) -> str | None:
    """FORMULA's install prefix, or None when it is not installed.

    ``brew --prefix <formula>`` prints the canonical path and exits 0 even for a formula that
    was never installed, so the directory has to be checked as well. Trusting the exit status
    alone lets the build run against Apple's bison 2.3 and fail at link time with an
    undefined flex symbol, which is a long way from the real cause.
    """
    if shutil.which("brew") is None:
        return None
    proc = subprocess.run(
        ["brew", "--prefix", formula], capture_output=True, text=True, check=False
    )
    prefix = proc.stdout.strip()
    return prefix if prefix and Path(prefix).is_dir() else None


def _darwin_build_env(env: dict[str, str]) -> dict[str, str]:
    """ENV with Homebrew's bison and flex ahead of Apple's. See BREW_BUILD_DEPS for why.

    Scoped to the build subprocess, so it is not part of the PATH the user is told to
    export, which needs only the installed tools.
    """
    found = {f: _brew_prefix(f) for f in BREW_BUILD_DEPS}
    missing = sorted(f for f, where in found.items() if where is None)
    if missing:
        raise RuntimeError(
            f"Verilator's build needs {', '.join(missing)}: run "
            f"`brew install {' '.join(missing)}` (and `xcode-select --install` for the "
            f"compiler), then run this again"
        )
    ahead = [f"{found[f]}/bin" for f in ("bison", "flex")]
    return {**env, "PATH": os.pathsep.join([*ahead, *filter(None, [env.get("PATH", "")])])}


def _run(cmd: list[str], cwd: Path, env: dict[str, str], what: str) -> None:
    proc = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if proc.returncode:
        raise RuntimeError(f"{what}: `{' '.join(cmd)}` exited {proc.returncode}")


def _build_verilator(t: Tool, version: str, prefix: Path, plat: str, jobs: int) -> None:
    """Clone T at VERSION and install it under PREFIX. Minutes, not seconds."""
    for need in ("git", "autoconf", "make"):
        if shutil.which(need) is None:
            raise RuntimeError(f"Verilator is built from source and {need} is not on PATH")
    env = dict(os.environ)
    if plat == DARWIN:
        env = _darwin_build_env(env)
    with tempfile.TemporaryDirectory(prefix="hdl-bag-verilator-") as tmp:
        src = Path(tmp) / "verilator"
        _say(f"    cloning {t.source} at {version}")
        _run(
            ["git", "clone", "--depth", "1", "--branch", version, "--", t.source, str(src)],
            Path(tmp), env, "verilator clone",
        )  # fmt: skip
        target = (prefix / "verilator").resolve()
        _say(f"    building with -j{jobs} (this takes a while)")
        # autoconf, not autoreconf: Verilator's own headers refuse the latter with
        # "Something failed during ./configure as config_package.h is incomplete. Perhaps
        # you used autoreconf, don't." The tools image does the same.
        _run(["autoconf"], src, env, "verilator autoconf")
        _run([f"{src}/configure", f"--prefix={target}"], src, env, "verilator configure")
        _run(["make", f"-j{jobs}"], src, env, "verilator make")
        if target.is_dir():
            shutil.rmtree(target)
        _run(["make", "install"], src, env, "verilator make install")
    _say(f"    installed verilator -> {prefix / 'verilator'}")
    _record(prefix, t, version)


def _reported_version(t: Tool, prefix: Path) -> str | None:
    """What T's installed executable reports, or None when it is missing or silent."""
    exe = prefix / t.bin_dir / t.executable
    if not exe.exists():
        return None
    try:
        proc = subprocess.run(
            [str(exe), *t.version_args], capture_output=True, text=True,
            check=False, timeout=VERSION_TIMEOUT_S,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        # Present but unrunnable: the wrong architecture, no execute bit, or a corrupt
        # extract. --check must report that, not raise.
        return None
    text = (proc.stdout + proc.stderr).strip()
    return text.splitlines()[0] if text else None


def _wanted(t: Tool, lock: dict[str, str]) -> str:
    try:
        return lock[t.lock_key]
    except KeyError:
        raise KeyError(f"{t.name}: tools.lock has no {t.lock_key}") from None


def _check(chosen: list[Tool], prefix: Path, wants: dict[str, str]) -> int:
    """Print each tool's reported version and whether the pin appears in it.

    Deliberately a substring test, not a parse: the authoritative version check is
    tests/flows/test_lint.py, and a second parser here would be a second thing to keep right.
    """
    bad = []
    for t in chosen:
        want = wants[t.name].removeprefix("v")
        got = _reported_version(t, prefix)
        # A tool whose pin is not a version can only be confirmed present.
        ok = got is not None and (want in got or not t.versioned)
        mark = "ok  " if ok else "BAD "
        shown = want if t.versioned else f"{want} (a build date)"
        print(f"{mark} {t.name:14} wants {shown:28} {got or '(not installed)'}")
        if not ok:
            bad.append(t.name)
    if bad:
        _say(f"not at the pinned version: {', '.join(bad)}")
    return 1 if bad else 0


def _writable(prefix: Path) -> bool:
    probe = prefix if prefix.is_dir() else prefix.parent
    return os.access(probe, os.W_OK)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Install the pinned EDA tools for this platform.",
        epilog="Run with no tool names to install every tool.",
    )
    ap.add_argument("tools", nargs="*", help="tool names; default every tool")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"install root ({DEFAULT_PREFIX})")
    ap.add_argument("--platform", choices=PLATFORMS, help="override the detected platform")
    ap.add_argument("--lock", default=str(LOCK), help="tools.lock to read; '-' reads none")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override or supply one pin; repeatable")  # fmt: skip
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2,
                    help="parallel jobs for Verilator's build")  # fmt: skip
    ap.add_argument("--list", action="store_true", help="resolve and print the plan, no network")
    ap.add_argument("--check", action="store_true", help="report versions; 1 if any drift")
    ap.add_argument("--print-path", action="store_true", help="print only the PATH line")
    ap.add_argument("--force", action="store_true", help="reinstall even if already at the pin")
    args = ap.parse_args(argv)

    try:
        plat = args.platform or host_platform()
    except UnsupportedPlatform as exc:
        _say(str(exc))
        return 2
    prefix = Path(args.prefix).expanduser()

    if args.print_path:
        print(path_line(str(prefix), plat))
        return 0

    try:
        lock = read_lock(None if args.lock == "-" else Path(args.lock), tuple(args.set))
        chosen = [tool(n) for n in args.tools] if args.tools else list(TOOLS)
        # Resolved here, not at each use: _wanted raises for a pin the lock lacks, and every
        # later call site sits outside this block.
        wants = {t.name: _wanted(t, lock) for t in chosen}
    except (KeyError, ValueError, OSError) as exc:
        _say(str(exc))
        return 2

    if args.jobs < 1:
        _say(f"--jobs must be at least 1, not {args.jobs}")
        return 2

    if args.check:
        return _check(chosen, prefix, wants)

    if args.list:
        for t in chosen:
            a = asset_for(t, plat)
            where = asset_url(t, plat, lock) if a else f"{t.source} (source build)"
            print(f"{t.name:14} {wants[t.name]:28} -> {prefix / t.bin_dir}")
            print(f"{'':14} {where}")
        return 0

    if not _writable(prefix):
        _say(
            f"{prefix} is not writable. Create it once with:\n\n"
            f"  sudo mkdir -p {prefix} && sudo chown $(id -u):$(id -g) {prefix}\n"
        )
        return 2

    for t in chosen:
        want = wants[t.name]
        if not args.force and _installed(prefix, t, want):
            _say(f"  {t.name}: already at {want}")
            continue
        _say(f"  {t.name}: installing {want}")
        try:
            a = asset_for(t, plat)
            if a is None:
                _build_verilator(t, want, prefix, plat, args.jobs)
            else:
                _install_asset(t, a, asset_url(t, plat, lock), prefix, want)
        except (RuntimeError, OSError, KeyError, ValueError) as exc:
            _say(f"  {t.name}: {exc}")
            return 1

    _say(
        f"\nDone. Put the tools on PATH, for example in ~/.zshrc:\n\n"
        f'  export PATH="{path_line(str(prefix), plat)}"\n\n'
        f"Then check them with --check. Not installed here: {', '.join(NOT_INSTALLED)}, and "
        f"LibreOffice for scripts/recalc.py (brew install --cask libreoffice)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
