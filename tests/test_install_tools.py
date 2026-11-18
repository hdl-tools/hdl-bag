# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""scripts/tool_specs.py checked without the tools or the network: every pin is accounted
for, every asset URL resolves on both platforms, and the PATH the installer prints is the
tools image's own ``ENV PATH``.

That last one is what keeps the image and the installer from drifting apart once
tools/Dockerfile calls the installer, since Docker cannot evaluate it and keeps a literal.
"""

import importlib.util
import io
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).parents[1]


def _specs() -> ModuleType:
    """scripts/tool_specs.py, loaded by path as tests/vendor/test_derived_mux.py does."""
    spec = importlib.util.spec_from_file_location("tool_specs", REPO / "scripts" / "tool_specs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["tool_specs"] = module
    spec.loader.exec_module(module)
    return module


def test_every_pin_is_installed_or_declared_not_installed() -> None:
    """A pin nobody installs is either deliberate (the image's base, Python, uv) or a tool
    that was added to tools.lock and forgotten here."""
    s = _specs()
    lock = s.read_lock()
    pinned = {k for k in lock if k != "OSS_CAD_SUITE_COMPACT"}
    consumed = {t.lock_key for t in s.TOOLS}
    assert sorted(pinned - consumed - set(s.NOT_INSTALLED)) == []
    assert sorted(consumed - pinned) == [], "a tool names a pin tools.lock does not have"


def test_every_asset_url_resolves_on_every_platform() -> None:
    """No leftover braces and no missing pin: the templates and the lock agree. Resolving is
    offline, so this catches a typo without downloading anything."""
    s = _specs()
    lock = s.read_lock()
    for t in s.TOOLS:
        for plat in s.PLATFORMS:
            if s.asset_for(t, plat) is None:
                assert t.source, f"{t.name} has no asset on {plat} and no source to build"
                continue
            url = s.asset_url(t, plat, lock)
            assert "{" not in url and "}" not in url, f"{t.name} on {plat}: {url}"
            assert url.startswith("https://"), url


def test_the_installers_path_is_the_tools_images_path() -> None:
    """tools/Dockerfile keeps a literal ENV PATH because Docker cannot evaluate the
    installer. They must say the same thing."""
    s = _specs()
    dockerfile = (REPO / "tools" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r'^ENV PATH="([^"]+)"', dockerfile, re.M)
    assert m, "tools/Dockerfile has no ENV PATH line"
    assert s.path_line("/opt", s.LINUX, "${PATH}") == m[1]


def test_every_tool_without_an_archive_can_be_built() -> None:
    """A tool with no release archive on some platform must have both a source URL and a
    builder, or the install reaches a platform it cannot satisfy. slang is the case that
    matters: it publishes nothing for linux-arm64."""
    s, inst = _specs(), _installer()
    for t in s.TOOLS:
        needs_source = [p for p in s.PLATFORMS if s.asset_for(t, p) is None]
        if not needs_source:
            continue
        assert t.source, f"{t.name} has no archive for {needs_source} and no source"
        assert t.name in inst._BUILDERS, f"{t.name} needs a source build and has no builder"


def test_slang_is_built_from_source_only_where_nothing_is_published() -> None:
    """Building when a tested binary exists would be a needless difference between
    platforms, and the prebuilt one is what the lint waivers were written against."""
    s = _specs()
    slang = s.tool("slang")
    built = sorted(p for p in s.PLATFORMS if s.asset_for(slang, p) is None)
    assert built == [s.LINUX_ARM]


def test_the_dockerfiles_arg_defaults_match_tools_lock() -> None:
    """The Dockerfile says its defaults must match tools.lock, and CI always overrides them
    with --build-arg, so a stale default breaks nothing until someone builds the image by
    hand and gets a different tool. #119 left one behind exactly that way."""
    s = _specs()
    lock = s.read_lock()
    dockerfile = (REPO / "tools" / "Dockerfile").read_text(encoding="utf-8")
    defaults = dict(re.findall(r"^ARG (\w+)=(\S+)$", dockerfile, re.M))
    shared = sorted(set(defaults) & set(lock))
    assert shared, "no ARG in tools/Dockerfile names a tools.lock key"
    assert {k: defaults[k] for k in shared} == {k: lock[k] for k in shared}


def test_the_image_installs_every_tool_it_puts_on_path() -> None:
    """Each tool needs its own installer invocation in the Dockerfile; a tool added to the
    table but not to the image would sit on PATH as an empty directory, which is how the
    pinned slang went missing (#130)."""
    s = _specs()
    dockerfile = (REPO / "tools" / "Dockerfile").read_text(encoding="utf-8")
    for t in s.TOOLS:
        assert re.search(
            rf"install_tools\.py[^\n]*(\\\n[^\n]*)*\b{re.escape(t.name)}\b", dockerfile
        ), f"tools/Dockerfile never installs {t.name}"


def test_the_oss_cad_suite_comes_last_on_path() -> None:
    """Its bundled copies of the separately pinned tools must stay shadowed (the image's
    PATH order), so the pinned Verilator wins over the suite's."""
    s = _specs()
    head, tail = s.path_entries("/opt", s.LINUX)
    assert [p for p in tail] == ["/opt/oss-cad-suite/bin"]
    assert "/opt/verilator/bin" in head and "/opt/oss-cad-suite/bin" not in head


def test_every_archive_has_a_recorded_digest() -> None:
    """A pinned version does not pin the bytes, so every asset on every platform must have a
    SHA-256 recorded. An entry with no asset means the table moved and the file did not."""
    s = _specs()
    lock = s.read_lock()
    expected = {
        s.asset_name(t, plat, lock)
        for t in s.TOOLS
        for plat in s.PLATFORMS
        if s.asset_for(t, plat) is not None
    }
    recorded = set(s.read_digests())
    assert sorted(expected - recorded) == [], "run install_tools.py --write-digests"
    assert sorted(recorded - expected) == [], "stale entries in tools/tools.sha256"


def test_digests_round_trip_through_the_sha256sum_format() -> None:
    s = _specs()
    digests = s.read_digests()
    assert digests, "tools/tools.sha256 is empty"
    assert all(len(h) == 64 and h.islower() for h in digests.values())
    assert s.read_digests.__module__  # loaded by path, so this is the real module
    text = s.format_digests(digests)
    assert text.endswith("\n") and "\r" not in text


def test_a_malformed_digest_line_names_the_file_and_line(tmp_path: Path) -> None:
    s = _specs()
    bad = tmp_path / "tools.sha256"
    bad.write_text("# ok\ndeadbeef  short-hash.tar.gz\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"tools\.sha256:2"):
        s.read_digests(bad)


def test_only_the_suite_has_a_pin_that_is_not_a_version() -> None:
    """The OSS CAD Suite is pinned by build date while its tools report their own versions,
    with nothing derivable between them, so --check can only confirm it is installed. Every
    other tool's pin is comparable, and treating one as unversioned by mistake would quietly
    stop checking it."""
    s = _specs()
    assert [t.name for t in s.TOOLS if not t.versioned] == ["oss-cad-suite"]


def test_read_lock_overrides_and_derives_the_compact_date() -> None:
    """The Dockerfile passes one pin per build stage with --set instead of copying the whole
    lock in, which is what keeps its Verilator layer cached."""
    s = _specs()
    lock = s.read_lock(None, ("OSS_CAD_SUITE_DATE=2026-01-02", "SLANG_VERSION=v9.9"))
    assert lock["OSS_CAD_SUITE_DATE"] == "2026-01-02"
    assert lock["OSS_CAD_SUITE_COMPACT"] == "20260102"
    assert lock["SLANG_VERSION"] == "v9.9"
    with pytest.raises(ValueError, match="not KEY=VALUE"):
        s.read_lock(None, ("NOPE",))


def test_the_real_lock_reads_the_same_way_the_workflow_does() -> None:
    """tools-image.yml greps the comment lines out and passes the rest as build-args, so a
    comment anywhere in tools.lock must not become part of a value."""
    s = _specs()
    lock = s.read_lock()
    assert all(not v.lstrip().startswith("#") for v in lock.values())
    assert lock["OSS_CAD_SUITE_COMPACT"] == lock["OSS_CAD_SUITE_DATE"].replace("-", "")


def test_an_unsupported_host_says_what_it_saw(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _specs()
    monkeypatch.setattr(s.platform, "system", lambda: "Windows")
    monkeypatch.setattr(s.platform, "machine", lambda: "AMD64")
    with pytest.raises(s.UnsupportedPlatform, match="Windows on AMD64"):
        s.host_platform()


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [("Linux", "x86_64", "linux-x64"), ("Darwin", "arm64", "darwin-arm64")],
)
def test_supported_hosts_map_to_their_platform(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, expected: str
) -> None:
    s = _specs()
    monkeypatch.setattr(s.platform, "system", lambda: system)
    monkeypatch.setattr(s.platform, "machine", lambda: machine)
    assert s.host_platform() == expected


def test_tool_lookup_names_every_tool_when_it_misses() -> None:
    s = _specs()
    with pytest.raises(KeyError, match="verilator"):
        s.tool("verilatorr")


# ------------------------------------------------- unpacking (scripts/install_tools.py)


def _installer() -> ModuleType:
    """scripts/install_tools.py. It imports tool_specs, so that is loaded first."""
    _specs()
    spec = importlib.util.spec_from_file_location(
        "install_tools", REPO / "scripts" / "install_tools.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["install_tools"] = module
    spec.loader.exec_module(module)
    return module


def _tar(path: Path, entries: dict[str, bytes]) -> Path:
    import tarfile

    with tarfile.open(path, "w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return path


def test_unpack_strips_components_and_keeps_the_bytes(tmp_path: Path) -> None:
    """The payload must survive, not just the paths: building fresh TarInfo objects to
    rename members loses tarfile's offset_data and silently extracts garbage, which shows up
    only as 'Exec format error' when the tool is run."""
    inst = _installer()
    archive = _tar(tmp_path / "a.tar.gz", {"top/bin/thing": b"\x7fELF-ish payload"})
    into = tmp_path / "out"
    into.mkdir()
    inst._unpack(archive, into, 1, "thing")
    assert (into / "bin" / "thing").read_bytes() == b"\x7fELF-ish payload"


def test_unpack_keeps_a_bare_file_when_nothing_is_stripped(tmp_path: Path) -> None:
    """slang ships as one bare executable with no directory, so strip must be 0 (#130)."""
    inst = _installer()
    archive = _tar(tmp_path / "s.tar.gz", {"slang": b"payload"})
    into = tmp_path / "out"
    into.mkdir()
    inst._unpack(archive, into, 0, "slang")
    assert (into / "slang").read_bytes() == b"payload"


def test_unpack_refuses_an_archive_that_strips_to_nothing(tmp_path: Path) -> None:
    """The failure mode worth catching loudly: a wrong strip level leaves an empty directory
    and the tool simply is not there, with no error from tar."""
    inst = _installer()
    archive = _tar(tmp_path / "s.tar.gz", {"slang": b"payload"})
    into = tmp_path / "out"
    into.mkdir()
    with pytest.raises(RuntimeError, match="nothing left to extract"):
        inst._unpack(archive, into, 1, "slang")


def test_a_wrong_digest_refuses_the_archive(tmp_path: Path) -> None:
    """A retagged release, a bad mirror or a truncated download all give the right URL and
    the wrong bytes, which is the whole reason the hashes are recorded."""
    inst = _installer()
    archive = _tar(tmp_path / "a.tar.gz", {"thing": b"payload"})
    with pytest.raises(RuntimeError, match="does not match"):
        inst._verify(archive, "a.tar.gz", {"a.tar.gz": "0" * 64}, False)
    # The real hash passes.
    inst._verify(archive, "a.tar.gz", {"a.tar.gz": inst._sha256(archive)}, False)


def test_an_archive_with_no_digest_is_refused_unless_allowed(tmp_path: Path) -> None:
    """Adding a tool must not quietly skip the check."""
    inst = _installer()
    archive = _tar(tmp_path / "a.tar.gz", {"thing": b"payload"})
    with pytest.raises(RuntimeError, match="no SHA-256 recorded"):
        inst._verify(archive, "a.tar.gz", {}, False)
    inst._verify(archive, "a.tar.gz", {}, True)  # --allow-unpinned


def test_a_missing_pin_is_a_message_not_a_traceback(tmp_path: Path) -> None:
    """The CLI never shows a raw traceback (AGENTS.md), and every pin is resolved before any
    work starts, so a lock without the pin fails immediately and says which one."""
    inst = _installer()
    assert inst.main(["slang", "--lock", "-", "--prefix", str(tmp_path)]) == 2


def test_jobs_below_one_is_refused(tmp_path: Path) -> None:
    """`make -j0` fails with something baffling, so it is caught here instead."""
    inst = _installer()
    assert inst.main(["--jobs", "0", "--prefix", str(tmp_path)]) == 2


def test_an_unparsable_lock_line_names_the_file_and_line(tmp_path: Path) -> None:
    s = _specs()
    lock = tmp_path / "tools.lock"
    lock.write_text("# fine\nGOOD=1\nnonsense\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"tools\.lock:3"):
        s.read_lock(lock)


def test_swap_in_replaces_what_is_already_there(tmp_path: Path) -> None:
    """Reinstalling must not leave a half-merged tree of old and new files."""
    inst = _installer()
    staged, prefix = tmp_path / "staged", tmp_path / "prefix"
    (staged / "bin").mkdir(parents=True)
    (staged / "bin" / "new").write_text("new")
    old = prefix / "tool" / "bin"
    old.mkdir(parents=True)
    (old / "stale").write_text("stale")
    assert inst._swap_in(staged, prefix, "tool") == ["bin"]
    assert (prefix / "tool" / "bin" / "new").read_text() == "new"
    assert not (prefix / "tool" / "bin" / "stale").exists()
