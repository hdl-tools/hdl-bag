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
- Owner, 2026-11-25: every fixed random seed is shrunk and joins the corpus; shrink.yml shrinks
  one seed at the commit it failed on.
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
    skip = ' || test "${{ inputs.regress_only }}" = true'
    assert [s["run"] for s in gate["steps"]] == [
        f'test "${{{{ needs.{p}.result }}}}" = success{skip if p.startswith("corpus") else ""}'
        for p in parts]  # fmt: skip


def test_a_dispatch_can_run_only_the_random_seeds() -> None:
    """Diagnosing a seed failure on a branch should not cost the corpus's hours. The schedule
    has no inputs, so a scheduled night always runs the corpus."""
    flow = _load("nightly.yml")
    box = _triggers(flow)["workflow_dispatch"]["inputs"]["regress_only"]
    assert (box["type"], box["default"]) == ("boolean", False)
    for job in ("corpus", "corpus-alone"):
        assert flow["jobs"][job]["if"] == "${{ !inputs.regress_only }}"
    assert "if" not in flow["jobs"]["regress"]


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


def test_the_wheel_smoke_test_checks_the_licensing_docs_reached_the_install() -> None:
    """pyproject.toml lists docs/bom.md and docs/licensing.md in `license-files`, so PEP 639
    puts them under dist-info/licenses/docs/. Only the release build's smoke test installs the
    wheel, so it is the one place that proves they shipped; this pins that it checks both. It
    does not build a wheel itself."""
    root = Path(__file__).parents[1]
    steps = _load("release.yml")["jobs"]["build"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Smoke-test the wheel")
    for doc in ("docs/bom.md", "docs/licensing.md"):
        assert f'{doc}"' in script, f"the smoke test should check the installed {doc}"
        assert (root / doc).is_file(), f"{doc} is listed for the wheel but is missing"
        assert f'"{doc}"' in (root / "pyproject.toml").read_text(encoding="utf-8")


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


def test_a_dispatch_shrinks_one_seed_at_the_commit_it_failed_on() -> None:
    """Owner, 2026-11-25: every fixed random seed's shrunk config joins the corpus. shrink.yml
    checks out the commit the seed failed on (a dispatch runs on a branch or tag, so the commit
    is an input), redraws the seed (the draw is deterministic, D97), shrinks it on the failing
    step, and keeps the shrunk IR as an artifact. It only reads, like the nightly. Review of
    #211: actions/checkout takes a ref shorter than 40 characters as a branch or tag, so the
    job refuses one; it says so where bag shrink is missing (commits before 51c66b7); and no
    input reaches a script except through the environment."""
    from hdl_bag.regress.generate import random_config

    flow = _load("shrink.yml")
    inputs = _triggers(flow)["workflow_dispatch"]["inputs"]
    assert {k for k, v in inputs.items() if v.get("required")} == {"seed", "commit", "step"}
    assert inputs["budget"]["default"] == "60" and inputs["image"]["default"] == "main"
    assert flow["permissions"] == {"contents": "read"}
    (job,) = flow["jobs"].values()
    assert job["permissions"] == {"contents": "read", "packages": "read"}
    assert job["container"]["image"] == "ghcr.io/hdl-tools/hdl-bag-tools:${{ inputs.image }}"
    # Where bag regress writes the seed's IR (regress/run.py: <seed>/<config name>.ir.yaml).
    assert random_config(7).name == "rnd7"
    assert job["env"]["IR"] == "out/regress/${{ inputs.seed }}/rnd${{ inputs.seed }}.ir.yaml"
    steps = job["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "${{ inputs.commit }}"
    assert steps.index(checkout) > 0 and '"${#COMMIT}" -eq 40' in steps[0]["run"]
    runs = [s["run"] for s in steps if "run" in s]
    assert not any("${{" in r for r in runs)
    script = "\n".join(runs)
    assert "uv run bag shrink --help" in script
    assert 'bag regress --seed "$SEED" --no-formal' in script
    assert 'bag shrink "$IR" --step "$STEP"' in script
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["with"]["name"] == "shrink-${{ inputs.seed }}"
    assert "out/regress/*/*.ir.yaml" in upload["with"]["path"]


def test_a_shrink_uses_the_current_shrinker_on_the_failing_commits_code() -> None:
    """#213: a shrinker older than its fix lets a shrink drift to another failure of the same
    test, and every commit a fixed seed fails on is older than that. So the job takes
    src/hdl_bag/shrink from the `shrinker` ref (main by default) over the checked-out commit:
    the step it judges is the failing commit's, the judging is the current one's."""
    flow = _load("shrink.yml")
    inputs = _triggers(flow)["workflow_dispatch"]["inputs"]
    assert inputs["shrinker"]["default"] == "main"
    (job,) = flow["jobs"].values()
    assert job["env"]["SHRINKER"] == "${{ inputs.shrinker }}"
    steps = job["steps"]
    runs = [s.get("run", "") for s in steps]
    overlay = next(i for i, r in enumerate(runs) if "-- src/hdl_bag/shrink" in r)
    assert 'git config --global --add safe.directory "$PWD"' in runs[overlay]  # a container job
    assert 'git fetch --depth 1 origin -- "$SHRINKER"' in runs[overlay]
    assert "git checkout FETCH_HEAD -- src/hdl_bag/shrink" in runs[overlay]
    # Which shrinker judged is kept with the result (main moves on).
    assert "git rev-parse FETCH_HEAD | tee out/shrink/shrinker.txt" in runs[overlay]
    uses = [s.get("uses", "") for s in steps]
    checkout = next(i for i, u in enumerate(uses) if u.startswith("actions/checkout"))
    first_use = next(i for i, r in enumerate(runs) if "bag shrink" in r)
    assert checkout < overlay < first_use


def test_the_manuals_pypi_docker_command_names_this_release() -> None:
    """The manual's container command for PyPI users (docs/manual/examples/files/docker-pypi.sh)
    runs this release's BAG in the image the release tags (release.yml tags the validated
    :main image with the version tag). It is never replayed, as docker cannot run inside the
    test, so pin both to the package version: a release bump must update it."""
    from hdl_bag import __version__

    script = (
        Path(__file__).parents[1] / "docs" / "manual" / "examples" / "files" / "docker-pypi.sh"
    ).read_text(encoding="utf-8")
    assert f"ghcr.io/hdl-tools/hdl-bag-tools:v{__version__} " in script, script
    assert f"--from 'hdl-bag[sim]=={__version__}' " in script, script
    runs = [str(st.get("run", "")) for job in _load("release.yml")["jobs"].values()
            for st in job.get("steps", [])]  # fmt: skip
    # The release tags this image, from the :main image the tag was validated against.
    tag = '--tag "$repo:${GITHUB_REF_NAME}" "$repo:main"'
    assert any("repo=ghcr.io/hdl-tools/hdl-bag-tools" in r and tag in r for r in runs), (
        "release.yml no longer tags the tools image the manual names"
    )


def test_the_nightly_writes_its_report_from_the_artifacts_and_only_reports() -> None:
    """#59 (plan PR 7): the report job reads the earlier nights' regress-summary artifacts (D55),
    so it may read actions and nothing more, renders nightly_report, publishes it, and the
    night's result does not wait for it (D56)."""
    jobs = _load("nightly.yml")["jobs"]
    job = jobs["report"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "gh run download" in run and "-n regress-summary" in run
    assert "--event schedule --branch main" in run  # only the nights, not branch dispatches
    assert "nightly_report(nights, night, results, gone)" in run  # tonight's missing seeds too
    assert job["if"] == "${{ !cancelled() }}" and "SEEDS" in job["env"]
    upload = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload"))
    assert upload["with"]["name"] == "nightly-report" and upload["if"] == "always()"
    assert "report" not in jobs["nightly"]["needs"]
def test_the_nightly_runs_mutmut_on_the_checks_and_only_reports() -> None:
    """D112: mutation testing of src/hdl_bag/checks/ against tests/checks runs every night with a
    pinned mutmut, inside a limit that still lets it report; it reports and never gates: the
    night's result does not wait for it, and its run cannot fail the step."""
    import tomllib

    jobs = _load("nightly.yml")["jobs"]
    job = jobs["mutmut"]
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "uv sync --locked --group mutation" in run  # the whole closure is locked
    assert "timeout -k 5m 5h uv run --no-sync mutmut run" in run and "|| status=$?" in run
    assert "exit $status" not in run  # report only: the status is shown, never raised
    assert job["timeout-minutes"] * 60 > 5 * 3600 + 5 * 60  # the inner limit ends it first
    assert job["continue-on-error"] is True and "mutmut" not in jobs["nightly"]["needs"]
    upload = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload"))
    assert upload["if"] == "always()"
    assert {"out/mutmut/results.txt", "mutants/mutmut-stats.json"} <= set(
        upload["with"]["path"].split()
    )
    with open(Path(__file__).parents[1] / "pyproject.toml", "rb") as f:
        project = tomllib.load(f)
    config = project["tool"]["mutmut"]
    assert project["dependency-groups"]["mutation"] == ["mutmut==3.8.1"]
    assert config["source_paths"] == ["src/hdl_bag/checks/"]
    assert config["pytest_add_cli_args_test_selection"] == ["tests/checks/"]
