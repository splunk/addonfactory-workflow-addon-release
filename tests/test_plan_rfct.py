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
import json
import tempfile
import unittest
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

    def __init__(self, releases=None, files=None, commits=None):
        self.releases = releases or []
        self.files = files or []
        self.commits = commits or {}
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
            return {"object": {"sha": self.commits[tag]}}
        raise AssertionError(f"unexpected URL {url}")

    @staticmethod
    def _paginate(items, page):
        start = (page - 1) * plan.PER_PAGE
        return items[start : start + plan.PER_PAGE]


def environment(**overrides):
    base = {
        "INPUT_ENABLED": "true",
        "INPUT_REPOSITORY": "splunk/test-addonfactory-repo",
        "INPUT_TOKEN": "token",
        "INPUT_EVENT_NAME": "pull_request",
        "INPUT_BASE_BRANCH": "develop",
        "INPUT_IS_FORK": "false",
        "INPUT_PR_BASE_SHA": "a" * 40,
        "INPUT_PR_HEAD_SHA": "b" * 40,
        "INPUT_BUILD_SHA": "c" * 40,
        "INPUT_PUSH_BEFORE": "",
        "INPUT_PUSH_AFTER": "",
        "INPUT_BASELINE_RELEASE": "",
        "INPUT_CANDIDATE_RELEASE": "",
        "INPUT_CUSTOM_VERSION": "",
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
                plan.parse_release_tag(value, "rfct-baseline-release")


class OverrideValidationTests(unittest.TestCase):
    def test_rejects_release_override_with_custom_version(self):
        with self.assertRaises(plan.PlanInputError):
            plan.validate_overrides("workflow_dispatch", "1.0.0", "", "2.0.0")
        with self.assertRaises(plan.PlanInputError):
            plan.validate_overrides("workflow_dispatch", "", "1.0.0", "2.0.0")

    def test_rejects_candidate_override_outside_manual_dispatch(self):
        with self.assertRaises(plan.PlanInputError):
            plan.validate_overrides("push", "", "1.0.0", "")

    def test_accepts_baseline_override_on_automatic_events(self):
        plan.validate_overrides("pull_request", "1.0.0", "", "")


class EligibilityTests(unittest.TestCase):
    def test_skips_when_disabled(self):
        with self.assertRaises(plan.PlanSkip) as caught:
            plan.check_eligibility(False, "pull_request", False, "develop")
        self.assertEqual(caught.exception.reason, "disabled")

    def test_skips_untrusted_and_fork_events_before_using_credentials(self):
        with self.assertRaises(plan.PlanSkip) as caught:
            plan.check_eligibility(True, "pull_request_target", False, "develop")
        self.assertEqual(caught.exception.reason, "untrusted-event")
        with self.assertRaises(plan.PlanSkip) as caught:
            plan.check_eligibility(True, "pull_request", True, "develop")
        self.assertEqual(caught.exception.reason, "fork-pull-request")

    def test_skips_unsupported_events_and_branches(self):
        with self.assertRaises(plan.PlanSkip) as caught:
            plan.check_eligibility(True, "schedule", False, "develop")
        self.assertEqual(caught.exception.reason, "unsupported-event")
        with self.assertRaises(plan.PlanSkip) as caught:
            plan.check_eligibility(True, "push", False, "feature/x")
        self.assertEqual(caught.exception.reason, "unsupported-branch")

    def test_allows_supported_branches_and_manual_dispatch(self):
        for branch in ("develop", "main", "release/1.2.x"):
            plan.check_eligibility(True, "push", False, branch)
        plan.check_eligibility(True, "workflow_dispatch", False, "")


class AssetTests(unittest.TestCase):
    def test_requires_exactly_one_package_asset(self):
        self.assertEqual(plan.sole_package_asset(release("v1.0.0"), "v1.0.0")["name"], "ta.spl")
        for assets in ((), ("a.spl", "b.spl")):
            with self.assertRaises(plan.PlanInputError):
                plan.sole_package_asset(release("v1.0.0", assets=assets), "v1.0.0")

    def test_ignores_non_package_assets(self):
        found = plan.sole_package_asset(
            release("v1.0.0", assets=("ta.spl", "THIRDPARTY", "report.html")), "v1.0.0"
        )
        self.assertEqual(found["name"], "ta.spl")


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
        options = plan.baseline_candidates(self.releases, "", {"source": "build"})
        self.assertEqual([option[0]["tag_name"] for option in options], ["v2.1.0", "v1.9.0"])

    def test_excludes_the_candidate_release_and_newer_versions(self):
        candidate = {"source": "release", "tag": "v2.1.0", "order": (2, 1, 0)}
        options = plan.baseline_candidates(self.releases, "", candidate)
        self.assertEqual([option[0]["tag_name"] for option in options], ["v1.9.0"])

    def test_explicit_baseline_must_exist_and_not_be_a_draft(self):
        with self.assertRaises(plan.PlanInputError):
            plan.baseline_candidates(self.releases, "9.9.9", {"source": "build"})
        with self.assertRaises(plan.PlanInputError):
            plan.baseline_candidates(self.releases, "3.0.0", {"source": "build"})

    def test_explicit_baseline_resolves_a_single_option(self):
        options = plan.baseline_candidates(self.releases, "v1.9.0", {"source": "build"})
        self.assertEqual([option[0]["tag_name"] for option in options], ["v1.9.0"])

    def test_skips_when_no_baseline_exists(self):
        api = FakeApi(releases=[], commits={})
        with mock.patch.object(plan, "_request_json", api):
            with self.assertRaises(plan.PlanSkip) as caught:
                plan.select_baseline("owner/repo", "token", [], "", {"source": "build"}, "")
        self.assertEqual(caught.exception.reason, "no-baseline")

    def test_skips_a_release_that_is_the_built_revision(self):
        releases = [release("v2.0.0", release_id=2), release("v1.0.0", release_id=1)]
        api = FakeApi(releases=releases, commits={"v2.0.0": "d" * 40, "v1.0.0": "e" * 40})
        with mock.patch.object(plan, "_request_json", api):
            described, parsed = plan.select_baseline(
                "owner/repo", "token", releases, "", {"source": "build"}, "d" * 40
            )
        self.assertEqual(described["tag"], "v1.0.0")
        self.assertEqual(parsed["version"], "1.0.0")


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
        self.assertEqual(result["candidate"]["source"], "build")

    def test_docs_only_change_skips_with_a_recorded_reason(self):
        api = FakeApi(
            releases=[release("v1.0.0")],
            commits={"v1.0.0": "f" * 40},
            files=[{"filename": "docs/index.md"}],
        )
        with self.assertRaises(plan.PlanSkip) as caught:
            self._run(environment(), api)
        self.assertEqual(caught.exception.reason, "no-mapping-changes")

    def test_manual_dispatch_runs_without_a_changed_path_requirement(self):
        api = FakeApi(
            releases=[release("v1.0.0", release_id=1), release("v2.0.0", release_id=2)],
            commits={"v1.0.0": "1" * 40, "v2.0.0": "2" * 40},
        )
        env = environment(
            INPUT_EVENT_NAME="workflow_dispatch",
            INPUT_BASE_BRANCH="",
            INPUT_CANDIDATE_RELEASE="2.0.0",
            INPUT_BASELINE_RELEASE="1.0.0",
        )
        result = self._run(env, api)
        self.assertEqual(result["decision"], "proceed")
        self.assertEqual(result["reason"], "manual-dispatch")
        self.assertEqual(result["changed_paths"], [])
        self.assertEqual(result["candidate"]["source"], "release")
        self.assertEqual(result["candidate"]["tag"], "v2.0.0")
        self.assertEqual(result["baseline"]["tag"], "v1.0.0")

    def test_first_release_without_a_baseline_skips(self):
        api = FakeApi(releases=[], files=[{"filename": "package/default/props.conf"}])
        with self.assertRaises(plan.PlanSkip) as caught:
            self._run(environment(), api)
        self.assertEqual(caught.exception.reason, "no-baseline")

    def test_identical_baseline_and_candidate_is_an_input_failure(self):
        api = FakeApi(releases=[release("v2.0.0", release_id=2)], commits={"v2.0.0": "2" * 40})
        env = environment(
            INPUT_EVENT_NAME="workflow_dispatch",
            INPUT_CANDIDATE_RELEASE="2.0.0",
            INPUT_BASELINE_RELEASE="2.0.0",
        )
        with self.assertRaises(plan.PlanInputError):
            self._run(env, api)

    def test_baseline_newer_than_the_candidate_is_an_input_failure(self):
        api = FakeApi(
            releases=[release("v1.0.0", release_id=1), release("v3.0.0", release_id=3)],
            commits={"v1.0.0": "1" * 40, "v3.0.0": "3" * 40},
        )
        env = environment(
            INPUT_EVENT_NAME="workflow_dispatch",
            INPUT_CANDIDATE_RELEASE="1.0.0",
            INPUT_BASELINE_RELEASE="3.0.0",
        )
        with self.assertRaises(plan.PlanInputError):
            self._run(env, api)

    def test_missing_baseline_asset_is_an_input_failure(self):
        api = FakeApi(releases=[release("v1.0.0", assets=())], commits={"v1.0.0": "f" * 40})
        with self.assertRaises(plan.PlanInputError):
            self._run(environment(), api)

    def test_fork_pull_request_skips_before_any_api_call(self):
        api = FakeApi()
        with self.assertRaises(plan.PlanSkip):
            self._run(environment(INPUT_IS_FORK="true"), api)
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
        self.assertEqual(json.loads(emitted["changed-paths"]), ["package/lookups/cim.csv"])

    def test_records_a_skip_decision_without_failing(self):
        api = FakeApi()
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            env = environment(INPUT_ENABLED="false", INPUT_OUTPUT_PATH=str(plan_path))
            with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(
                plan, "_request_json", api
            ):
                plan.main()
            document = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(document["decision"], "skip")
        self.assertEqual(document["status"], "skipped")
        self.assertEqual(document["reason"], "disabled")
        self.assertEqual(document["baseline"], {})

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

    def test_the_plan_job_is_gated_on_the_opt_in_input(self):
        self.assertIn("inputs.rfct-enabled", self.workflow["jobs"]["plan-rfct"]["if"])
