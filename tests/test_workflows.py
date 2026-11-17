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
- The nightly's Verilator cache restores the PR caches and never saves (the 10 GB limit).
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


def test_the_nightly_runs_the_random_seeds_in_shards_with_the_date_as_base() -> None:
    job = _load("nightly.yml")["jobs"]["regress"]
    assert job["strategy"]["matrix"]["shard"] == list(range(1, REGRESS_SHARDS + 1))
    assert job["strategy"]["fail-fast"] is False
    script = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "date -u +%Y%m%d" in script
    assert f'--shard "${{{{ matrix.shard }}}}/{REGRESS_SHARDS}"' in script
    assert "bag regress --seeds" in script and "--formal-budget" in script
    uploads = [s for s in job["steps"]
               if str(s.get("uses", "")).startswith("actions/upload-artifact")]  # fmt: skip
    assert len(uploads) == 1 and uploads[0]["if"] == "always()"
    assert "result.json" in uploads[0]["with"]["path"]


def test_the_nightly_writes_one_summary_of_every_seed() -> None:
    job = _load("nightly.yml")["jobs"]["summary"]
    assert job["needs"] == "regress" and job["if"] == "always()"
    script = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "write_summary" in script and "GITHUB_STEP_SUMMARY" in script


def test_the_interim_cron_left_pr_yml() -> None:
    flow = _load("pr.yml")
    assert "schedule" not in _triggers(flow)
    assert "schedule" not in str(flow["jobs"])
    assert "eda-alone" not in flow["jobs"]  # the nightly's now


def test_the_full_list_runs_on_enough_shards() -> None:
    """pr.yml's full list (push to main, suite=full) and the full-eda label's run."""
    pr = _load("pr.yml")["jobs"]["eda-shard"]["with"]["shards"]
    assert f"|| {FULL_SHARDS} }}}}" in str(pr)
    assert _load("full-eda.yml")["jobs"]["full-eda-shards"]["with"]["shards"] == FULL_SHARDS
