# Copyright 2026 Splunk Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import importlib.util
import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

ACTION_DIRECTORY = Path(__file__).parents[1] / ".github" / "actions" / "plan-rfct"
ACTION_YAML_PATH = ACTION_DIRECTORY / "action.yml"
SPEC = importlib.util.spec_from_file_location("plan_rfct", ACTION_DIRECTORY / "plan.py")
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "reusable-build-test-release.yml"


def release(tag, *, draft=False, prerelease=False, assets=("ta.spl",), release_id=1):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "id": release_id,
        "assets": [{"name": name, "id": 100 + index} for index, name in enumerate(assets)],
    }


class FakeApi:
    """Serve paginated GitHub responses from in-memory fixtures."""

    def __init__(self, releases=None, files=None, commits=None, tags=None):
        self.releases = releases or []
        self.files = files or []
        self.commits = commits or {}
        self.tags = tags or {}
        self.calls = []

    def __call__(self, url, token):
        self.calls.append(url)
        page = int(url.rpartition("page=")[2]) if "page=" in url else 1
        if "/releases?" in url:
            return self._paginate(self.releases, page)
        if "/compare/" in url:
            return {"files": self._paginate(self.files, page)}
        if "/git/ref/tags/" in url:
            tag = url.rpartition("/")[2]
            if tag not in self.commits:
                raise plan.PlanInputError(f"no commit for {tag}")
            target = self.commits[tag]
            return {"object": target if isinstance(target, dict) else {"sha": target, "type": "commit"}}
        if "/git/tags/" in url:
            return {"object": self.tags[url.rpartition("/")[2]]}
        raise AssertionError(f"unexpected URL {url}")

    @staticmethod
    def _paginate(items, page):
        start = (page - 1) * plan.PER_PAGE
        return items[start : start + plan.PER_PAGE]


def environment(**overrides):
    base = {
        "INPUT_REPOSITORY": "splunk/test-addonfactory-repo",
        "INPUT_TOKEN": "token",
        "INPUT_EVENT_NAME": "pull_request",
        "INPUT_BASE_BRANCH": "main",
        "INPUT_IS_FORK": "false",
        "INPUT_PR_BASE_SHA": "a" * 40,
        "INPUT_PR_HEAD_SHA": "b" * 40,
        "INPUT_BUILD_SHA": "c" * 40,
        "INPUT_PUSH_BEFORE": "",
        "INPUT_PUSH_AFTER": "",
        "INPUT_OUTPUT_PATH": "/tmp/plan.json",
    }
    base.update({key: value for key, value in overrides.items()})
    return base


class SupportedPathTests(unittest.TestCase):
    def test_recognizes_every_supported_mapping_path(self):
        for path in (
            "globalConfig.json",
            "package/default/props.conf",
            "package/default/data/models/model.json",
            "package/default/data/spl2/pipeline.spl2",
            "package/lookups/cim.csv",
            "tests/knowledge/test_props.py",
            "package/local/transforms.conf",
        ):
            self.assertTrue(plan.matches_supported_path(path), path)

    def test_ignores_unrelated_paths(self):
        for path in (
            "docs/index.md",
            "README.md",
            "package/bin/input.py",
            "mkdocs.yml",
            "tests/unit/test_unit.py",
            "globalConfig.json.bak",
            "package/default/data/ui/views/view.xml",
        ):
            self.assertFalse(plan.matches_supported_path(path), path)


class ReleaseTagTests(unittest.TestCase):
    def test_normalizes_versions_and_tags(self):
        for value in ("1.2.3", "v1.2.3", "V1.2.3", " v1.2.3 "):
            parsed = plan.parse_release_tag(value, "field")
            self.assertEqual(parsed["tag"], "v1.2.3")
            self.assertEqual(parsed["version"], "1.2.3")
            self.assertEqual(parsed["order"], (1, 2, 3))
            self.assertFalse(parsed["prerelease"])

    def test_marks_prereleases(self):
        parsed = plan.parse_release_tag("1.0.0-beta.7", "field")
        self.assertTrue(parsed["prerelease"])
        self.assertEqual(parsed["tag"], "v1.0.0-beta.7")

    def test_rejects_arbitrary_references(self):
        for value in (
            "latest",
            "1.2",
            "../../etc/passwd",
            "1.2.3; rm -rf /",
            "1.2.3+build.1",
            "https://example.com/pkg.spl",
            "$(id)",
            "",
        ):
            with self.assertRaises(plan.PlanInputError):
                plan.parse_release_tag(value, "release tag")


class EligibilityTests(unittest.TestCase):
    def test_skips_untrusted_and_fork_events_before_using_credentials(self):
        with self.assertRaises(plan.PlanSkip) as caught:
            plan.check_eligibility("pull_request_target", False, "main")
        self.assertEqual(caught.exception.reason, "untrusted-event")
        with self.assertRaises(plan.PlanSkip) as caught:
            plan.check_eligibility("pull_request", True, "main")
        self.assertEqual(caught.exception.reason, "fork-pull-request")

    def test_skips_unsupported_events_and_branches(self):
        for event in ("schedule", "workflow_dispatch", "workflow_run"):
            with self.assertRaises(plan.PlanSkip) as caught:
                plan.check_eligibility(event, False, "main")
            self.assertEqual(caught.exception.reason, "unsupported-event")
        for event in ("push", "pull_request"):
            for branch in ("develop", "release/1.2.x", "feature/x", "refs/heads/main"):
                with self.assertRaises(plan.PlanSkip) as caught:
                    plan.check_eligibility(event, False, branch)
                self.assertEqual(caught.exception.reason, "unsupported-branch")

    def test_allows_trusted_main_pull_requests_and_pushes(self):
        for event in ("pull_request", "push"):
            plan.check_eligibility(event, False, "main")


class TagResolutionTests(unittest.TestCase):
    def test_resolves_a_lightweight_tag_directly(self):
        api = FakeApi(commits={"v1.0.0": "a" * 40})
        with mock.patch.object(plan, "_request_json", api):
            self.assertEqual(plan.resolve_tag_commit("owner/repo", "token", "v1.0.0"), "a" * 40)
        self.assertEqual(len(api.calls), 1)

    def test_peels_nested_annotated_tags_to_the_commit(self):
        api = FakeApi(
            commits={"v1.0.0": {"sha": "a" * 40, "type": "tag"}},
            tags={"a" * 40: {"sha": "b" * 40, "type": "tag"},
                  "b" * 40: {"sha": "c" * 40, "type": "commit"}},
        )
        with mock.patch.object(plan, "_request_json", api):
            self.assertEqual(plan.resolve_tag_commit("owner/repo", "token", "v1.0.0"), "c" * 40)
        self.assertEqual(len(api.calls), 3)

    def test_rejects_missing_malformed_and_noncommit_targets(self):
        for target in ({}, {"sha": "x", "type": "commit"}, {"sha": "a" * 40, "type": "tree"}):
            with mock.patch.object(plan, "_request_json", return_value={"object": target}):
                with self.assertRaises(plan.PlanInputError):
                    plan.resolve_tag_commit("owner/repo", "token", "v1.0.0")

    def test_rejects_a_tag_cycle(self):
        target = {"sha": "a" * 40, "type": "tag"}
        with mock.patch.object(plan, "_request_json", return_value={"object": target}) as request:
            with self.assertRaises(plan.PlanInputError):
                plan.resolve_tag_commit("owner/repo", "token", "v1.0.0")
        self.assertEqual(request.call_count, 2)

    def test_bounds_annotated_tag_nesting(self):
        payloads = [{"object": {"sha": char * 40, "type": "tag"}} for char in "abc"]
        with mock.patch.object(plan, "MAX_TAG_DEPTH", 2), mock.patch.object(
            plan, "_request_json", side_effect=payloads
        ):
            with self.assertRaisesRegex(plan.PlanInputError, "nesting exceeded"):
                plan.resolve_tag_commit("owner/repo", "token", "v1.0.0")


class AssetTests(unittest.TestCase):
    @staticmethod
    def _asset(tag, assets):
        parsed = plan.parse_release_tag(tag, "tag")
        return plan.package_asset(release(tag, assets=assets), parsed)

    def test_picks_the_splunkbase_package_beside_deployment_partitions(self):
        # The real release layout: the splunkbase package plus search-head partitions.
        found = self._asset(
            "v0.8.7",
            (
                "cim-compliance-report.md",
                "installation-actions.json",
                "Splunk_TA_box-0.8.7-_search_heads.spl",
                "Splunk_TA_box-0.8.7.spl",
            ),
        )
        self.assertEqual(found["name"], "Splunk_TA_box-0.8.7.spl")

    def test_falls_back_to_a_single_package_with_unversioned_naming(self):
        self.assertEqual(self._asset("v1.0.0", ("ta.spl",))["name"], "ta.spl")

    def test_reports_no_package_when_none_is_identifiable(self):
        self.assertIsNone(self._asset("v1.0.0", ()))
        self.assertIsNone(self._asset("v1.0.0", ("a.spl", "b.spl")))
        self.assertIsNone(self._asset("v1.0.0", ("cim-compliance-report.md",)))

    def test_ignores_non_package_assets(self):
        found = self._asset("v1.0.0", ("ta.spl", "THIRDPARTY", "report.html"))
        self.assertEqual(found["name"], "ta.spl")

    def test_requiring_a_package_fails_when_none_is_identifiable(self):
        parsed = plan.parse_release_tag("v1.0.0", "tag")
        with self.assertRaises(plan.PlanInputError):
            plan.require_package_asset(release("v1.0.0", assets=()), parsed)


class BaselineSelectionTests(unittest.TestCase):
    def setUp(self):
        self.releases = [
            release("v1.0.0-beta.1", prerelease=True, release_id=5),
            release("v2.1.0", release_id=4),
            release("v3.0.0", draft=True, release_id=3),
            release("v1.9.0", release_id=2),
            release("not-a-version", release_id=1),
        ]

    def test_picks_the_highest_eligible_stable_release(self):
        options = plan.baseline_candidates(self.releases)
        self.assertEqual([option[0]["tag_name"] for option in options], ["v2.1.0", "v1.9.0"])

    def test_skips_when_no_baseline_exists(self):
        api = FakeApi(releases=[], commits={})
        with mock.patch.object(plan, "_request_json", api):
            with self.assertRaises(plan.PlanSkip) as caught:
                plan.select_baseline("owner/repo", "token", [], "")
        self.assertEqual(caught.exception.reason, "no-baseline")

    def test_skips_a_release_that_is_the_built_revision(self):
        releases = [release("v2.0.0", release_id=2), release("v1.0.0", release_id=1)]
        api = FakeApi(releases=releases, commits={"v2.0.0": "d" * 40, "v1.0.0": "e" * 40})
        with mock.patch.object(plan, "_request_json", api):
            described, parsed = plan.select_baseline(
                "owner/repo", "token", releases, "d" * 40
            )
        self.assertEqual(described["tag"], "v1.0.0")
        self.assertEqual(parsed["version"], "1.0.0")

    def test_same_build_exclusion_uses_the_commit_beneath_an_annotated_tag(self):
        releases = [release("v2.0.0"), release("v1.0.0")]
        api = FakeApi(
            commits={"v2.0.0": {"sha": "a" * 40, "type": "tag"}, "v1.0.0": "e" * 40},
            tags={"a" * 40: {"sha": "d" * 40, "type": "commit"}},
        )
        with mock.patch.object(plan, "_request_json", api):
            described, _ = plan.select_baseline("owner/repo", "token", releases, "d" * 40)
        self.assertEqual(described["tag"], "v1.0.0")


class ChangedPathTests(unittest.TestCase):
    def test_collects_additions_deletions_and_both_rename_sides(self):
        api = FakeApi(
            files=[
                {"filename": "package/default/props.conf", "status": "added"},
                {"filename": "package/lookups/old.csv", "status": "removed"},
                {
                    "filename": "package/lookups/new.csv",
                    "previous_filename": "package/lookups/renamed.csv",
                    "status": "renamed",
                },
            ]
        )
        with mock.patch.object(plan, "_request_json", api):
            paths = plan.changed_paths("owner/repo", "token", "base", "head")
        self.assertEqual(
            paths,
            [
                "package/default/props.conf",
                "package/lookups/new.csv",
                "package/lookups/old.csv",
                "package/lookups/renamed.csv",
            ],
        )

    def test_paginates_so_a_long_diff_is_never_truncated(self):
        files = [{"filename": f"package/lookups/file{index}.csv"} for index in range(205)]
        api = FakeApi(files=files)
        with mock.patch.object(plan, "_request_json", api):
            paths = plan.changed_paths("owner/repo", "token", "base", "head")
        self.assertEqual(len(paths), 205)
        self.assertEqual(len(api.calls), 3)

    def test_fails_when_pagination_exceeds_the_bound(self):
        files = [{"filename": f"package/lookups/f{index}.csv"} for index in range(500)]
        api = FakeApi(files=files)
        with mock.patch.object(plan, "_request_json", api), mock.patch.object(plan, "MAX_PAGES", 2):
            with self.assertRaises(plan.PlanInputError):
                plan.changed_paths("owner/repo", "token", "base", "head")


class ComparisonRangeTests(unittest.TestCase):
    def test_pull_request_compares_base_against_the_built_revision(self):
        with mock.patch.dict("os.environ", environment(), clear=True):
            self.assertEqual(plan.comparison_range("pull_request", ""), ("a" * 40, "c" * 40))

    def test_pull_request_requires_both_revisions(self):
        with mock.patch.dict("os.environ", environment(INPUT_PR_BASE_SHA=""), clear=True):
            with self.assertRaises(plan.PlanInputError):
                plan.comparison_range("pull_request", "")

    def test_push_uses_before_and_after(self):
        env = environment(
            INPUT_EVENT_NAME="push", INPUT_PUSH_BEFORE="1" * 40, INPUT_PUSH_AFTER="2" * 40
        )
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(plan.comparison_range("push", ""), ("1" * 40, "2" * 40))

    def test_created_branch_falls_back_to_the_baseline_commit(self):
        env = environment(
            INPUT_EVENT_NAME="push", INPUT_PUSH_BEFORE="0" * 40, INPUT_PUSH_AFTER="2" * 40
        )
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(plan.comparison_range("push", "9" * 40), ("9" * 40, "2" * 40))

    def test_missing_history_without_a_baseline_is_an_input_failure(self):
        env = environment(
            INPUT_EVENT_NAME="push", INPUT_PUSH_BEFORE="", INPUT_PUSH_AFTER="2" * 40
        )
        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(plan.PlanInputError):
                plan.comparison_range("push", "")


class BuildPlanTests(unittest.TestCase):
    def _run(self, env, api):
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(
            plan, "_request_json", api
        ):
            return plan.build_plan(env["INPUT_REPOSITORY"], env["INPUT_TOKEN"])

    def test_pull_request_with_a_mapping_change_proceeds(self):
        api = FakeApi(
            releases=[release("v1.0.0")],
            commits={"v1.0.0": "f" * 40},
            files=[{"filename": "package/default/props.conf"}, {"filename": "docs/index.md"}],
        )
        result = self._run(environment(), api)
        self.assertEqual(result["decision"], "proceed")
        self.assertEqual(result["reason"], "mapping-change")
        self.assertEqual(result["changed_paths"], ["package/default/props.conf"])
        self.assertEqual(result["baseline"]["tag"], "v1.0.0")
        self.assertEqual(result["candidate"], {"source": "build", "commit": "c" * 40})

    def test_docs_only_change_skips_with_a_recorded_reason(self):
        api = FakeApi(
            releases=[release("v1.0.0")],
            commits={"v1.0.0": "f" * 40},
            files=[{"filename": "docs/index.md"}],
        )
        with self.assertRaises(plan.PlanSkip) as caught:
            self._run(environment(), api)
        self.assertEqual(caught.exception.reason, "no-mapping-changes")

    def test_main_push_with_a_mapping_change_proceeds(self):
        api = FakeApi(
            releases=[release("v1.0.0", release_id=1), release("v2.0.0", release_id=2)],
            commits={"v1.0.0": "1" * 40, "v2.0.0": "2" * 40},
            files=[{"filename": "package/default/props.conf"}],
        )
        env = environment(
            INPUT_EVENT_NAME="push", INPUT_PUSH_BEFORE="a" * 40, INPUT_PUSH_AFTER="c" * 40,
        )
        result = self._run(env, api)
        self.assertEqual(result["decision"], "proceed")
        self.assertEqual(result["reason"], "mapping-change")
        self.assertEqual(result["candidate"], {"source": "build", "commit": "c" * 40})
        self.assertEqual(result["baseline"]["tag"], "v2.0.0")

    def test_first_release_without_a_baseline_skips(self):
        api = FakeApi(releases=[], files=[{"filename": "package/default/props.conf"}])
        with self.assertRaises(plan.PlanSkip) as caught:
            self._run(environment(), api)
        self.assertEqual(caught.exception.reason, "no-baseline")

    def test_automatic_selection_skips_a_tag_cut_without_a_package(self):
        # Mirrors the shared fixture, where a bare 1.0.0 tag outranks every
        # release that actually publishes a package.
        releases = [
            release("1.0.0", assets=(), release_id=9),
            release("v0.8.7", assets=("Splunk_TA_box-0.8.7.spl",), release_id=8),
        ]
        api = FakeApi(
            releases=releases,
            commits={"1.0.0": "9" * 40, "v0.8.7": "8" * 40},
            files=[{"filename": "package/default/props.conf"}],
        )
        result = self._run(environment(), api)
        self.assertEqual(result["baseline"]["tag"], "v0.8.7")
        self.assertEqual(result["baseline"]["asset_name"], "Splunk_TA_box-0.8.7.spl")

    def test_skips_when_no_release_publishes_a_package(self):
        api = FakeApi(
            releases=[release("v1.0.0", assets=())],
            commits={"v1.0.0": "f" * 40},
            files=[{"filename": "package/default/props.conf"}],
        )
        with self.assertRaises(plan.PlanSkip) as caught:
            self._run(environment(), api)
        self.assertEqual(caught.exception.reason, "no-baseline")

    def test_fork_pull_request_skips_before_any_api_call(self):
        api = FakeApi()
        with self.assertRaises(plan.PlanSkip):
            self._run(environment(INPUT_IS_FORK="true"), api)
        self.assertEqual(api.calls, [])

    def test_manual_dispatch_and_nonmain_events_skip_before_any_api_call(self):
        for overrides in ({"INPUT_EVENT_NAME": "workflow_dispatch"}, {"INPUT_BASE_BRANCH": "develop"}):
            api = FakeApi()
            with self.assertRaises(plan.PlanSkip):
                self._run(environment(**overrides), api)
            self.assertEqual(api.calls, [])


class OutputTests(unittest.TestCase):
    def test_writes_the_frozen_plan_and_step_outputs(self):
        api = FakeApi(
            releases=[release("v1.0.0")],
            commits={"v1.0.0": "f" * 40},
            files=[{"filename": "package/lookups/cim.csv"}],
        )
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            output_path = Path(directory) / "github-output"
            output_path.touch()
            env = environment(INPUT_OUTPUT_PATH=str(plan_path))
            env["GITHUB_OUTPUT"] = str(output_path)
            with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(
                plan, "_request_json", api
            ):
                plan.main()

            document = json.loads(plan_path.read_text(encoding="utf-8"))
            summary = plan_path.with_suffix(".summary.md").read_text(encoding="utf-8")
            emitted = dict(
                line.split("=", 1)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line
            )

        self.assertEqual(document["schema_version"], plan.SCHEMA_VERSION)
        self.assertEqual(document["decision"], "proceed")
        self.assertEqual(document["status"], "")
        self.assertEqual(document["changed_paths"], ["package/lookups/cim.csv"])
        self.assertEqual(document["pull_request_head_sha"], "b" * 40)
        self.assertEqual(document["build_sha"], "c" * 40)
        self.assertEqual(emitted["decision"], "proceed")
        self.assertEqual(emitted["baseline-tag"], "v1.0.0")
        self.assertEqual(emitted["baseline-commit"], "f" * 40)
        self.assertEqual(emitted["candidate-source"], "build")
        self.assertEqual(emitted["candidate-commit"], "c" * 40)
        self.assertEqual(json.loads(emitted["changed-paths"]), ["package/lookups/cim.csv"])
        self.assertEqual(json.loads(emitted["baseline-selection"]), document["baseline_selection"])
        self.assertEqual(emitted["summary-path"], str(plan_path.with_suffix(".summary.md")))
        self.assertIn("| Decision | proceed |", summary)
        self.assertIn("Current build", summary)

    def test_records_a_skip_decision_without_failing(self):
        api = FakeApi()
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            env = environment(INPUT_BASE_BRANCH="develop", INPUT_OUTPUT_PATH=str(plan_path))
            with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(
                plan, "_request_json", api
            ):
                plan.main()
            document = json.loads(plan_path.read_text(encoding="utf-8"))
            summary = plan_path.with_suffix(".summary.md").read_text(encoding="utf-8")
        self.assertEqual(document["decision"], "skip")
        self.assertEqual(document["status"], "skipped")
        self.assertEqual(document["reason"], "unsupported-branch")
        self.assertEqual(document["baseline"], {})
        self.assertIn("| Decision | skip |", summary)
        self.assertIn("unsupported\\-branch", summary)

    def test_requires_an_output_path(self):
        with mock.patch.dict("os.environ", environment(INPUT_OUTPUT_PATH=""), clear=True):
            with self.assertRaises(plan.PlanInputError):
                plan.main()


class ApiErrorTests(unittest.TestCase):
    def test_reports_malformed_api_json(self):
        class Response:
            def read(self):
                return b"{not json"

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with mock.patch.object(plan, "urlopen", return_value=Response()):
            with self.assertRaises(plan.PlanInputError):
                plan._request_json("https://api.github.com/x", "token")

    def test_reports_http_and_network_failures(self):
        from urllib.error import HTTPError, URLError

        with mock.patch.object(
            plan, "urlopen", side_effect=HTTPError("u", 404, "missing", None, None)
        ):
            with self.assertRaises(plan.PlanInputError):
                plan._request_json("https://api.github.com/x", "token")
        with mock.patch.object(plan, "urlopen", side_effect=URLError("offline")):
            with self.assertRaises(plan.PlanInputError):
                plan._request_json("https://api.github.com/x", "token")


class BaselineAuditTests(unittest.TestCase):
    @staticmethod
    def run_plan(api, **overrides):
        logs = io.StringIO()
        with mock.patch.dict("os.environ", environment(**overrides), clear=True), mock.patch.object(
            plan, "_request_json", api
        ), redirect_stderr(logs):
            result = plan.build_plan("owner/repo", "token")
        return result, logs.getvalue()

    def test_warns_only_for_higher_unusable_stable_packages(self):
        api = FakeApi(
            releases=[
                release("v8.0.0", draft=True),
                release("v7.0.0", prerelease=True),
                release("v6.0.0-beta.1"),
                release("not-a-version"),
                release("v4.0.0", assets=()),
                release("v3.0.0", assets=("a.spl", "b.spl")),
                release("v2.0.0"),
                release("v1.0.0", assets=()),
            ],
            commits={"v2.0.0": "d" * 40},
            files=[{"filename": "package/default/props.conf"}],
        )
        result, logs = self.run_plan(api)
        audit = result["baseline_selection"]
        self.assertEqual(audit["policy"], "highest-usable-stable-version")
        self.assertEqual(audit["selected"], result["baseline"])
        self.assertTrue(audit["fallback"])
        reasons = {item["tag"]: item["reason"] for item in audit["skipped"]}
        self.assertEqual(reasons, {
            "v8.0.0": "draft", "v7.0.0": "prerelease", "v6.0.0-beta.1": "prerelease",
            "not-a-version": "invalid-version", "v4.0.0": "missing-package",
            "v3.0.0": "ambiguous-package", "v1.0.0": "missing-package",
        })
        warnings = [line for line in logs.splitlines() if line.startswith("::warning::")]
        self.assertEqual(len(warnings), 2)
        self.assertIn("v4.0.0 has no .spl package", warnings[0])
        self.assertIn("v3.0.0 has ambiguous .spl packages", warnings[1])
        self.assertTrue(all("selecting older release v2.0.0" in warning for warning in warnings))
        events = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
        self.assertEqual({event["phase"] for event in events}, {"eligibility", "releases", "baseline", "paths"})

    def test_normal_filtering_and_older_unusable_releases_do_not_warn(self):
        api = FakeApi(
            releases=[release("v4.0.0", draft=True), release("v3.0.0-beta.1"),
                      release(""), release("v2.0.0"), release("v1.0.0", assets=())],
            commits={"v2.0.0": "d" * 40},
            files=[{"filename": "package/default/props.conf"}],
        )
        result, logs = self.run_plan(api)
        self.assertFalse(result["baseline_selection"]["fallback"])
        self.assertNotIn("::warning::", logs)
        self.assertIn('"skipped_counts"', logs)

    def test_same_build_exclusion_is_audited_without_a_fallback_warning(self):
        api = FakeApi(
            releases=[release("v2.0.0"), release("v1.0.0")],
            commits={"v2.0.0": {"sha": "a" * 40, "type": "tag"}, "v1.0.0": "d" * 40},
            tags={"a" * 40: {"sha": "c" * 40, "type": "commit"}},
            files=[{"filename": "package/default/props.conf"}],
        )
        result, logs = self.run_plan(api)
        self.assertEqual(result["baseline"]["tag"], "v1.0.0")
        self.assertEqual(result["baseline_selection"]["skipped"], [
            {"tag": "v2.0.0", "release_id": 1, "version": "2.0.0", "reason": "same-build-revision"},
        ])
        self.assertFalse(result["baseline_selection"]["fallback"])
        self.assertNotIn("::warning::", logs)

    def test_no_baseline_plan_retains_all_skipped_release_reasons(self):
        api = FakeApi(
            releases=[release("v3.0.0", assets=()), release("v2.0.0", assets=("a.spl", "b.spl")),
                      release("v1.0.0")],
            commits={"v1.0.0": "c" * 40},
        )
        logs, console = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            with mock.patch.dict("os.environ", environment(INPUT_OUTPUT_PATH=str(path)), clear=True), mock.patch.object(
                plan, "_request_json", api
            ), redirect_stderr(logs), redirect_stdout(console):
                plan.main()
            document = json.loads(path.read_text())
        self.assertEqual(document["reason"], "no-baseline")
        self.assertIsNone(document["baseline_selection"]["selected"])
        self.assertEqual([item["reason"] for item in document["baseline_selection"]["skipped"]],
                         ["missing-package", "ambiguous-package", "same-build-revision"])
        self.assertNotIn("::warning::", logs.getvalue())
        self.assertIn('"decision": "skip"', logs.getvalue())
        self.assertEqual(console.getvalue(), "")

    def test_no_mapping_change_retains_the_selected_baseline_audit(self):
        api = FakeApi(releases=[release("v1.0.0")], commits={"v1.0.0": "d" * 40},
                      files=[{"filename": "README.md"}])
        with self.assertRaises(plan.PlanSkip) as caught:
            self.run_plan(api)
        self.assertEqual(caught.exception.reason, "no-mapping-changes")
        self.assertEqual(caught.exception.baseline_selection["selected"]["tag"], "v1.0.0")


class SummaryRendererTests(unittest.TestCase):
    @staticmethod
    def document():
        baseline = {"tag": "v0.8.7", "version": "0.8.7", "commit": "d" * 40}
        return {
            "decision": "proceed", "reason": "mapping-change", "build_sha": "c" * 40,
            "baseline": baseline, "candidate": {"source": "build", "commit": "c" * 40},
            "baseline_selection": {"policy": "highest-usable-stable-version", "selected": baseline,
                                   "fallback": False, "skipped": []},
        }

    def test_proceed_summary_explains_the_chosen_baseline_and_current_build(self):
        summary = plan.render_summary(self.document())
        self.assertIn("| Decision | proceed |", summary)
        self.assertIn("| Reason | mapping\\-change |", summary)
        self.assertIn("v0\\.8\\.7", summary)
        self.assertIn("Current build", summary)
        self.assertIn("c" * 40, summary)
        self.assertIn("d" * 40, summary)
        self.assertIn("highest usable stable release", summary)
        self.assertNotIn("newer stable releases could not be used", summary)

    def test_fallback_summary_only_lists_higher_unusable_stable_releases(self):
        document = self.document()
        document["baseline_selection"].update(fallback=True, skipped=[
            {"tag": "1.0.0", "version": "1.0.0", "reason": "missing-package"},
            {"tag": "v0.9.0", "version": "0.9.0", "reason": "ambiguous-package"},
            {"tag": "v0.8.0", "version": "0.8.0", "reason": "missing-package"},
            {"tag": "v2.0.0-beta.1", "reason": "prerelease"},
            {"tag": "v3.0.0", "version": "3.0.0", "reason": "same-build-revision"},
        ])
        summary = plan.render_summary(document)
        self.assertIn("1\\.0\\.0: no .spl package", summary)
        self.assertIn("v0\\.9\\.0: ambiguous .spl packages", summary)
        for excluded in ("v0\\.8\\.0", "beta", "v3\\.0\\.0"):
            self.assertNotIn(excluded, summary)
        self.assertEqual(len(document["baseline_selection"]["skipped"]), 5)

    def test_no_baseline_summary_gives_a_reason_and_exclusion_counts(self):
        document = self.document()
        document.update(decision="skip", reason="no-baseline", baseline={}, candidate={})
        document["baseline_selection"].update(selected=None, skipped=[
            {"tag": "v2.0.0", "reason": "missing-package"},
            {"tag": "v1.0.0", "reason": "missing-package"},
            {"tag": "v0.9.0", "reason": "same-build-revision"},
        ])
        summary = plan.render_summary(document)
        self.assertIn("| Decision | skip |", summary)
        self.assertIn("no cluster comparison will run", summary)
        self.assertIn("missing\\-package: 2", summary)
        self.assertIn("same\\-build\\-revision: 1", summary)
        self.assertIn("c" * 40, summary)
        document["baseline_selection"]["skipped"] = []
        self.assertIn("No published releases", plan.render_summary(document))

    def test_non_mapping_skip_can_still_show_the_baseline_already_selected(self):
        document = self.document()
        document.update(decision="skip", reason="no-mapping-changes", baseline={}, candidate={})
        summary = plan.render_summary(document)
        self.assertIn("no\\-mapping\\-changes", summary)
        self.assertIn("v0\\.8\\.7", summary)

    def test_summary_escapes_html_markdown_controls_and_known_credentials(self):
        document = self.document()
        document["reason"] = "<script>secret-token</script>|[click](https://example.com)\r\n## forged\u202e::error::"
        with mock.patch.dict("os.environ", {"INPUT_TOKEN": "secret-token"}, clear=True):
            summary = plan.render_summary(document)
        self.assertNotIn("<script>", summary)
        self.assertNotIn("secret-token", summary)
        self.assertNotIn("\r", summary)
        self.assertNotIn("\u202e", summary)
        self.assertNotIn("::error::", summary)
        self.assertIn("&lt;script&gt;\\[REDACTED\\]&lt;/script&gt;", summary)
        self.assertIn("\\|\\[click\\]\\(https://example\\.com\\)", summary)
        self.assertIn("\\\\u000d\\\\u000a", summary)
        self.assertIn("\\\\u202e", summary)
        self.assertNotIn("\n## forged", summary)


class SafeLoggingTests(unittest.TestCase):
    def test_structured_logs_neutralize_inline_runner_commands_and_redact_tokens(self):
        logs = io.StringIO()
        with mock.patch.dict("os.environ", {"INPUT_TOKEN": "secret-token"}, clear=True), redirect_stderr(logs):
            plan.log_event("baseline", "filtered", tag="prefix::error::injected\n::add-mask::secret-token")
        serialized = logs.getvalue()
        self.assertEqual(len(serialized.splitlines()), 1)
        self.assertNotIn("::", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertEqual(json.loads(serialized)["tag"], "prefix::error::injected\n::add-mask::[REDACTED]")

    def test_explicit_annotations_escape_nested_commands_and_control_characters(self):
        logs = io.StringIO()
        with mock.patch.dict("os.environ", {"INPUT_TOKEN": "secret-token"}, clear=True), redirect_stderr(logs):
            plan._workflow_message("warning", "prefix::error::injected\r\n50% secret-token")
        self.assertEqual(logs.getvalue(), "::warning::prefix: :error: :injected%0D%0A50%25 [REDACTED]\n")

    def test_multiline_asset_names_cannot_inject_other_action_outputs(self):
        value = "ta-secret-token.spl\ninjected=success\nstatus=no-changes"
        document = plan.plan_document({
            "decision": "proceed", "reason": "mapping-change", "changed_paths": [],
            "baseline": {"tag": "v1.0.0", "version": "1.0.0", "asset_name": value},
        })
        with tempfile.TemporaryDirectory() as directory:
            path, github_output = Path(directory) / "plan.json", Path(directory) / "github-output"
            with mock.patch.dict("os.environ", {"INPUT_TOKEN": "secret-token", "GITHUB_OUTPUT": str(github_output)}, clear=True):
                emitted = plan.write_outputs(document, path)
            serialized = github_output.read_text()
            public_document = path.read_text()
        self.assertNotIn("secret-token", serialized + public_document)
        self.assertIn("baseline-asset-name<<rfct_", serialized)
        lines = iter(serialized.splitlines())
        parsed = {}
        for line in lines:
            if "<<" in line:
                key, delimiter = line.split("<<", 1)
                parts = []
                for item in lines:
                    if item == delimiter:
                        break
                    parts.append(item)
                parsed[key] = "\n".join(parts)
            else:
                key, parsed_value = line.split("=", 1)
                parsed[key] = parsed_value
        self.assertNotIn("injected", parsed)
        self.assertEqual(parsed["status"], "")
        self.assertEqual(parsed["baseline-asset-name"], value.replace("secret-token", "[REDACTED]"))
        self.assertEqual(json.loads(parsed["baseline-selection"]), plan.selection_audit())
        self.assertEqual(emitted["baseline-asset-name"], parsed["baseline-asset-name"])

    def test_command_line_errors_do_not_reprint_unescaped_exception_text(self):
        from urllib.error import URLError

        logs, console = io.StringIO(), io.StringIO()
        env = environment(INPUT_TOKEN="secret-token")
        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "urllib.request.urlopen", side_effect=URLError("prefix::error::injected\nsecret-token")
        ), redirect_stderr(logs), redirect_stdout(console):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(ACTION_DIRECTORY / "plan.py"), run_name="__main__")
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(console.getvalue(), "")
        self.assertNotIn("secret-token", logs.getvalue())
        self.assertEqual(logs.getvalue().count("::error::"), 1)
        self.assertIn("prefix: :error: :injected%0A[REDACTED]", logs.getvalue())

class ActionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action = yaml.safe_load(ACTION_YAML_PATH.read_text(encoding="utf-8"))
        cls.workflow = yaml.load(
            WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )

    def test_every_declared_input_reaches_the_planner(self):
        step = self.action["runs"]["steps"][0]
        wired = "\n".join(step["env"].values())
        for name in self.action["inputs"]:
            self.assertIn(f"inputs.{name}", wired, name)

    def test_every_declared_output_maps_to_the_planner_step(self):
        for name, declared in self.action["outputs"].items():
            self.assertIn("steps.plan.outputs.", declared["value"], name)

    def test_the_action_runs_its_own_bundled_helper(self):
        step = self.action["runs"]["steps"][0]
        self.assertIn("$GITHUB_ACTION_PATH/plan.py", step["run"])

    def test_the_workflow_loads_the_planner_from_this_repository(self):
        step = next(
            step
            for step in self.workflow["jobs"]["plan-rfct"]["steps"]
            if step.get("id") == "plan"
        )
        self.assertTrue(
            step["uses"].startswith(
                "splunk/addonfactory-workflow-addon-release/.github/actions/plan-rfct@"
            ),
            step["uses"],
        )

    def test_manual_and_opt_in_controls_are_not_part_of_the_action(self):
        for name in ("enabled", "baseline-release", "candidate-release", "custom-version"):
            self.assertNotIn(name, self.action["inputs"])

    def test_the_plan_job_routes_by_event_and_main_branch(self):
        condition = self.workflow["jobs"]["plan-rfct"]["if"]
        self.assertNotIn("inputs.rfct-enabled", condition)
        for context in ("github.event_name", "github.base_ref", "github.ref"):
            self.assertIn(context, condition)
        self.assertIn("main", condition)
