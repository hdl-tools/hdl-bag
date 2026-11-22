# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The CI workflows, Phase 5 PR 6: the nightly (nightly.yml) and the full EDA list.

What the owner settled and the tests hold:
- D55: the nightly needs no write permission; its history lives in its artifacts.
- D56: it only reports, and never files issues.
- D57: it runs at 18:00 UTC: the full golden corpus (pr.yml's interim cron moves here) plus
  50 random seeds with the date as `--seed-base`, sharded so no shard nears its time limit.
- The full EDA list (push to main, `suite=full`, the full-eda label) no longer fit 4 shards
  of 30 minutes (about 373 minutes of tests, PR 5): it runs on FULL_SHARDS.
- The nightly's Verilator cache restores what pushes to main saved and never saves (the
  10 GB limit).
- PR 6 review: the night's seed base is computed once (a rerun of failed jobs keeps it), a
  shard cut off by its limit is named in the summary as missing seeds and fails the night,
  and a dispatch of pr.yml that selects tests by name may run a deep or alone one.
"""

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
FULL_SHARDS = 12
SEEDS, REGRESS_SHARDS = 50, 10


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = YAML(typ="safe").load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def _triggers(flow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the key `on` as True.
    triggers: dict[str, Any] = flow["on"] if "on" in flow else flow[True]
    return triggers


def test_the_nightly_runs_at_1800_utc_and_on_demand() -> None:
    triggers = _triggers(_load("nightly.yml"))
    assert triggers["schedule"] == [{"cron": "0 18 * * *"}]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert {k: inputs[k]["default"] for k in ("seeds", "seed_base", "formal_budget")} == {
        "seeds": str(SEEDS), "seed_base": "", "formal_budget": "600"}  # fmt: skip


def test_the_nightly_writes_nothing_and_files_no_issues() -> None:
    """D55 and D56: every permission is read, at the top and in every job."""
    flow = _load("nightly.yml")
    grants = [flow["permissions"], *(j.get("permissions", {}) for j in flow["jobs"].values())]
    assert all(v == "read" for g in grants for v in g.values())
    assert "issues" not in str(flow)


def test_the_nightly_runs_the_corpus_with_its_deep_and_alone_tests() -> None:
    jobs = _load("nightly.yml")["jobs"]
    corpus, alone = jobs["corpus"], jobs["corpus-alone"]
    assert corpus["uses"] == alone["uses"] == "./.github/workflows/eda-shards.yml"
    assert corpus["with"]["marks"] == "eda and not alone"
    assert (corpus["with"]["shards"], corpus["with"]["workers"]) == (20, "2")
    assert alone["with"]["marks"] == "eda and alone" and alone["with"]["workers"] == "1"
    assert corpus["with"]["save-cache"] is False and alone["with"]["save-cache"] is False


def _script(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


def test_the_night_computes_its_seed_base_once() -> None:
    """A rerun of failed jobs the next day must draw the same seeds: the base is a job
    output, which a rerun reuses, not a date each shard reads for itself."""
    jobs = _load("nightly.yml")["jobs"]
    assert jobs["prep"]["outputs"] == {"base": "${{ steps.base.outputs.base }}"}
    assert "date -u +%Y%m%d" in _script(jobs["prep"])
    assert "date" not in _script(jobs["regress"])
    assert jobs["regress"]["needs"] == "prep"
    assert jobs["regress"]["env"]["SEED_BASE"] == "${{ needs.prep.outputs.base }}"


def test_the_nightly_runs_the_random_seeds_in_shards_with_the_date_as_base() -> None:
    job = _load("nightly.yml")["jobs"]["regress"]
    assert job["strategy"]["matrix"]["shard"] == list(range(1, REGRESS_SHARDS + 1))
    assert job["strategy"]["fail-fast"] is False
    assert job["timeout-minutes"] == 180
    assert job["env"]["SEEDS"] == "${{ inputs.seeds || '50' }}"
    assert job["env"]["FORMAL_BUDGET"] == "${{ inputs.formal_budget || '600' }}"
    script = _script(job)
    assert '--seed-base "$SEED_BASE"' in script
    assert f'--shard "${{{{ matrix.shard }}}}/{REGRESS_SHARDS}"' in script
    # It restores the cache and never saves it.
    assert not [s for s in job["steps"] if "cache/save" in str(s.get("uses", ""))]
    assert "bag regress --seeds" in script and "--formal-budget" in script
    uploads = [s for s in job["steps"]
               if str(s.get("uses", "")).startswith("actions/upload-artifact")]  # fmt: skip
    assert len(uploads) == 1 and uploads[0]["if"] == "always()"
    for kept in ("result.json", "*.xlsx", "*.ir.yaml"):
        assert kept in uploads[0]["with"]["path"]


def test_the_nightly_writes_one_summary_of_every_seed_and_names_the_missing() -> None:
    job = _load("nightly.yml")["jobs"]["summary"]
    assert job["needs"] == ["prep", "regress"] and job["if"] == "always()"
    script = _script(job)
    assert "write_summary" in script and "GITHUB_STEP_SUMMARY" in script
    assert "missing_seeds" in script and "sys.exit(1)" in script  # a cut-off shard fails it
    assert job["env"]["SEED_BASE"] == "${{ needs.prep.outputs.base }}"
    uploads = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload")]
    assert uploads[0]["with"]["name"] == "regress-summary" and uploads[0]["if"] == "always()"


def test_the_night_is_green_only_when_every_part_is() -> None:
    gate = _load("nightly.yml")["jobs"]["nightly"]
    parts = ["corpus", "corpus-alone", "regress", "summary"]
    assert gate["needs"] == parts and gate["if"] == "always()"
    assert [s["run"] for s in gate["steps"]] == [
        f'test "${{{{ needs.{p}.result }}}}" = success' for p in parts]  # fmt: skip


def test_the_interim_cron_left_pr_yml() -> None:
    flow = _load("pr.yml")
    assert "schedule" not in _triggers(flow)
    assert "schedule" not in str(flow["jobs"])
    assert "eda-alone" not in flow["jobs"]  # the nightly's now


def test_the_full_list_runs_on_enough_shards() -> None:
    """pr.yml's full list (push to main, suite=full) and the full-eda label's run: 12 jobs,
    with 40 minutes each (its slowest took 24 of 30, PR 6 review)."""
    with_ = _load("pr.yml")["jobs"]["eda-shard"]["with"]
    assert f"|| {FULL_SHARDS} }}}}" in str(with_["shards"])
    full = _load("full-eda.yml")["jobs"]["full-eda-shards"]["with"]
    assert (full["shards"], full["timeout-minutes"]) == (FULL_SHARDS, 40)
    timeout = str(with_["timeout-minutes"])
    assert timeout.endswith("|| 40 }}")


def test_a_dispatch_that_selects_tests_by_name_may_run_a_deep_or_alone_one() -> None:
    """PR 6 review: select runs every matching test ('eda'), so its job gets the alone
    tasks' 340 minutes; the smoke subset keeps 30."""
    with_ = _load("pr.yml")["jobs"]["eda-shard"]["with"]
    timeout = str(with_["timeout-minutes"])
    assert "inputs.select != '') && 340" in timeout
    assert "&& 30 ||" in timeout  # the smoke subset (pull requests, suite=smoke)


def test_the_manuals_docker_command_names_the_image_ci_runs() -> None:
    """The manual's container lint command (docs/manual/examples/files/docker-lint.sh) is shown
    to the reader but never replayed, because docker cannot run inside the test. Nothing else
    would notice it going stale, so pin its image to the one the EDA jobs pull."""
    script = (
        Path(__file__).parents[1] / "docs" / "manual" / "examples" / "files" / "docker-lint.sh"
    ).read_text(encoding="utf-8")
    image = _load("eda-shards.yml")["jobs"]["shard"]["container"]["image"]
    assert image in script, f"docker-lint.sh should name {image}, the image the EDA jobs pull"


def test_a_tag_publishes_a_github_release_only_after_pypi_has_published() -> None:
    """The release page needs the changelog section, which the build job checks first: a
    missing section must fail before PyPI, which cannot withdraw a release."""
    jobs = _load("release.yml")["jobs"]
    release = jobs["github-release"]
    assert release["needs"] == ["publish"]
    assert release["permissions"] == {"contents": "write"}
    assert "gh release create" in _script(release)
    assert "scripts/release_notes.py" in _script(jobs["build"])


def test_a_tag_pins_the_tools_image_it_was_validated_against_only_after_pypi_has_published() -> (
    None
):
    """:main moves, so the release's image is the manifest behind :main re-tagged with the git
    tag, server side (no rebuild). A tag that failed to publish must not pin an image."""
    pin = _load("release.yml")["jobs"]["pin-tools-image"]
    assert pin["needs"] == ["publish"]
    assert pin["permissions"] == {"contents": "read", "packages": "write"}
    assert any(s.get("uses", "").startswith("docker/login-action@") for s in pin["steps"])
    script = _script(pin)
    assert "docker buildx imagetools create" in script
    assert '--tag "$repo:${GITHUB_REF_NAME}" "$repo:main"' in script


def test_a_tag_is_not_pinned_to_a_tools_image_built_from_a_different_tools_lock() -> None:
    """:main is the image of main's tools.lock and tools/Dockerfile. A tag on a commit whose two
    files differ would be pinned to the wrong image, so the job fails before it logs in. The
    comparison is by path, so a tag on main's tip, or on a commit that differs elsewhere, passes."""
    steps = _load("release.yml")["jobs"]["pin-tools-image"]["steps"]
    assert steps[0].get("uses", "").startswith("actions/checkout@")
    guard = next(s for s in steps if "diff --quiet" in str(s.get("run", "")))
    login = next(
        i for i, s in enumerate(steps) if s.get("uses", "").startswith("docker/login-action@")
    )
    assert steps.index(guard) < login
    script = str(guard["run"])
    assert "git fetch --depth=1 origin main" in script
    assert "git diff --quiet HEAD FETCH_HEAD -- tools.lock tools/Dockerfile" in script
    assert "::error::" in script
    assert "tools-image.yml" in script
    assert "exit 1" in script
