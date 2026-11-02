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
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPOSITORY_ROOT = Path(__file__).parents[1]
ACTION_DIRECTORIES = (".github/actions/plan-rfct", ".github/actions/run-rfct")
PINNED_REF_RE = re.compile(
    r"addonfactory-workflow-addon-release/\.github/actions/(?:plan|run)-rfct@(\S+)"
)

ACTION_DIRECTORY = Path(__file__).parents[1] / ".github" / "actions" / "run-rfct"
ACTION_YAML_PATH = ACTION_DIRECTORY / "action.yml"
SPEC = importlib.util.spec_from_file_location("run_rfct", ACTION_DIRECTORY / "run_rfct.py")
rfct = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rfct)

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "reusable-build-test-release.yml"

APPROVED_IMAGE = (
    "956110764581.dkr.ecr.us-west-2.amazonaws.com/ta-automation/rfct@sha256:"
    + "4d8cbe68865c6ac7d2b035aaab0a46d1bf09828acaea9f134bb883ad1fc5e9c2"
)
COMPATIBLE_HELP = "usage: format-report docs-markdown [--ta-repo-path PATH] [--output DIR]"
BETA7_HELP = "usage: format-report docs-markdown [--output DIR]"

CHANGED_REPORT = {
    "candidate": {"version": "2.3.0"},
    "summary": {"has_changes": True},
    "changes": [{"eventtype": "web", "field": "action"}],
}
NO_CHANGE_REPORT = {"candidate": {"version": "2.3.0"}, "summary": {"has_changes": False}, "changes": []}


class FakeDocker:
    """Record docker invocations and emulate the phases the action drives."""

    def __init__(self, help_text=COMPATIBLE_HELP, compare_code=0, format_code=0, report=None):
        self.help_text = help_text
        self.compare_code = compare_code
        self.format_code = format_code
        self.report = CHANGED_REPORT if report is None else report
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:3] == ["docker", "rm", "--force"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "--help" in argv:
            return subprocess.CompletedProcess(argv, 0, self.help_text, "")
        if "compare" in argv:
            if self.compare_code == 0 and self.report is not None:
                output = Path(argv[argv.index("--output") + 1])
                local = self._to_local(output, argv)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(json.dumps(self.report), encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.compare_code, "compared", "compare stderr")
        return subprocess.CompletedProcess(argv, self.format_code, "formatted", "format stderr")

    @staticmethod
    def _to_local(container_path, argv):
        """Map a container output path back to its bind-mounted host path."""
        for index, item in enumerate(argv):
            if item == "-v" and argv[index + 1].endswith(rfct.CONTAINER_OUTPUT):
                host = argv[index + 1].split(":")[0]
                return Path(host) / str(container_path).removeprefix(rfct.CONTAINER_OUTPUT + "/")
        raise AssertionError("no output mount found")

    @property
    def removed_containers(self):
        return [argv[3] for argv in self.calls if argv[:3] == ["docker", "rm", "--force"]]


class Workspace:
    """A validated set of prepared action inputs on disk."""

    def __init__(self, directory):
        self.root = Path(directory)
        self.config = self.root / "config.json"
        self.config.write_text('{"baseline": {}, "candidate": {}}', encoding="utf-8")
        self.source = self.root / "source"
        (self.source / "package" / "default").mkdir(parents=True)
        self.output = self.root / "output"
        self.github_output = self.root / "github-output"
        self.github_output.touch()

    def environment(self, **overrides):
        base = {
            "INPUT_CONFIG_PATH": str(self.config),
            "INPUT_CANDIDATE_SOURCE": str(self.source),
            "INPUT_APP_CODE_NAME": "Splunk_TA_example",
            "INPUT_RFCT_IMAGE": APPROVED_IMAGE,
            "INPUT_OUTPUT_DIRECTORY": str(self.output),
            "GITHUB_OUTPUT": str(self.github_output),
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "2",
        }
        base.update(overrides)
        return base

    def emitted_outputs(self):
        return dict(
            line.split("=", 1)
            for line in self.github_output.read_text(encoding="utf-8").splitlines()
            if line
        )

    def execution(self):
        return json.loads((self.output / "execution.json").read_text(encoding="utf-8"))


class SanitizeTests(unittest.TestCase):
    def test_redacts_credential_shaped_values(self):
        redacted = rfct.sanitize(
            "password: hunter2\nHEC token=abcd1234\nAuthorization: Bearer xyz\nsplunk_host=localhost"
        )
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("abcd1234", redacted)
        self.assertNotIn("Bearer", redacted)
        self.assertIn("splunk_host=localhost", redacted)

    def test_truncates_long_diagnostics(self):
        redacted = rfct.sanitize("x" * (rfct.MAX_DIAGNOSTIC_CHARS + 500))
        self.assertTrue(redacted.endswith("[truncated]"))
        self.assertLess(len(redacted), rfct.MAX_DIAGNOSTIC_CHARS + 50)

    def test_handles_empty_input(self):
        self.assertEqual(rfct.sanitize(""), "")


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Workspace(self.directory.name)

    def _validate(self, **overrides):
        with mock.patch.dict("os.environ", self.workspace.environment(**overrides), clear=True):
            return rfct.validate_inputs()

    def test_accepts_prepared_inputs(self):
        settings = self._validate()
        self.assertEqual(settings["image"], APPROVED_IMAGE)
        self.assertEqual(settings["app_code_name"], "Splunk_TA_example")

    def test_requires_a_digest_pinned_approved_image(self):
        for image in (
            "956110764581.dkr.ecr.us-west-2.amazonaws.com/ta-automation/rfct:1.0.0-beta.7",
            "956110764581.dkr.ecr.us-west-2.amazonaws.com/ta-automation/rfct@sha256:short",
            "ghcr.io/evil/rfct@sha256:" + "a" * 64,
            "956110764581.dkr.ecr.us-west-2.amazonaws.com/ta-automation/other@sha256:" + "a" * 64,
            "",
        ):
            with self.assertRaises(rfct.ExecutionError) as caught:
                self._validate(INPUT_RFCT_IMAGE=image)
            self.assertEqual(caught.exception.reason, "invalid-input")

    def test_rejects_invalid_app_code_names(self):
        for name in ("", "../escape", "has space", "-leading", "a" * 200):
            with self.assertRaises(rfct.ExecutionError):
                self._validate(INPUT_APP_CODE_NAME=name)

    def test_requires_absolute_existing_local_paths(self):
        with self.assertRaises(rfct.ExecutionError):
            self._validate(INPUT_CONFIG_PATH="relative/config.json")
        with self.assertRaises(rfct.ExecutionError):
            self._validate(INPUT_CONFIG_PATH=str(self.workspace.root / "missing.json"))
        with self.assertRaises(rfct.ExecutionError):
            self._validate(INPUT_CANDIDATE_SOURCE=str(self.workspace.config))
        with self.assertRaises(rfct.ExecutionError):
            self._validate(INPUT_OUTPUT_DIRECTORY="output")

    def test_rejects_symlinked_inputs(self):
        link = self.workspace.root / "config-link.json"
        link.symlink_to(self.workspace.config)
        with self.assertRaises(rfct.ExecutionError):
            self._validate(INPUT_CONFIG_PATH=str(link))

    def test_rejects_a_candidate_source_that_still_has_git_history(self):
        (self.workspace.source / ".git").mkdir()
        with self.assertRaises(rfct.ExecutionError) as caught:
            self._validate()
        self.assertIn(".git", str(caught.exception))

    def test_requires_docker_on_the_runner(self):
        with mock.patch.object(rfct.shutil, "which", return_value=None):
            with self.assertRaises(rfct.ExecutionError):
                rfct.docker_available()


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report = Path(self.directory.name) / "report.json"

    def _write(self, payload):
        self.report.write_text(
            payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
        )
        return self.report

    def test_reports_changes_and_no_changes(self):
        self.assertEqual(rfct.read_outcome(self._write(CHANGED_REPORT)), ("changes", "2.3.0"))
        self.assertEqual(rfct.read_outcome(self._write(NO_CHANGE_REPORT)), ("no-changes", "2.3.0"))

    def test_derives_the_outcome_from_a_changes_collection(self):
        self._write({"candidate_version": "1.0.0", "changes": []})
        self.assertEqual(rfct.read_outcome(self.report), ("no-changes", "1.0.0"))
        self._write({"candidate_version": "1.0.0", "changes": {"web": ["action"]}})
        self.assertEqual(rfct.read_outcome(self.report), ("changes", "1.0.0"))

    def test_missing_output_never_implies_no_change(self):
        with self.assertRaises(rfct.ExecutionError) as caught:
            rfct.read_outcome(Path(self.directory.name) / "absent.json")
        self.assertEqual(caught.exception.reason, "invalid-report")

    def test_rejects_malformed_json(self):
        with self.assertRaises(rfct.ExecutionError) as caught:
            rfct.read_outcome(self._write("{not json"))
        self.assertEqual(caught.exception.reason, "invalid-report")

    def test_rejects_a_non_object_report(self):
        with self.assertRaises(rfct.ExecutionError):
            rfct.read_outcome(self._write([1, 2, 3]))

    def test_rejects_a_report_without_a_candidate_version(self):
        with self.assertRaises(rfct.ExecutionError):
            rfct.read_outcome(self._write({"summary": {"has_changes": False}}))

    def test_rejects_an_empty_result_with_no_declared_outcome(self):
        with self.assertRaises(rfct.ExecutionError) as caught:
            rfct.read_outcome(self._write({"candidate": {"version": "1.0.0"}}))
        self.assertEqual(caught.exception.reason, "invalid-report")


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Workspace(self.directory.name)

    def _execute(self, docker, **overrides):
        with mock.patch.dict(
            "os.environ", self.workspace.environment(**overrides), clear=True
        ), mock.patch.object(rfct.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            rfct.subprocess, "run", docker
        ):
            return rfct.execute()

    def test_successful_comparison_records_changes_and_maps_outputs(self):
        docker = FakeDocker()
        execution = self._execute(docker)

        self.assertEqual(execution["status"], "changes")
        self.assertEqual(execution["candidate_version"], "2.3.0")
        self.assertEqual(execution["phases"], {"preflight": 0, "compare": 0, "format": 0})
        self.assertEqual(execution["image_digest"], APPROVED_IMAGE.partition("@")[2])

        emitted = self.workspace.emitted_outputs()
        self.assertEqual(emitted["status"], "changes")
        self.assertEqual(emitted["candidate-version"], "2.3.0")
        self.assertEqual(emitted["image-digest"], APPROVED_IMAGE.partition("@")[2])
        self.assertEqual(emitted["artifact-directory"], str(self.workspace.output))
        self.assertEqual(
            emitted["execution-result-path"], str(self.workspace.output / "execution.json")
        )

    def test_no_change_result_is_an_explicit_success(self):
        execution = self._execute(FakeDocker(report=NO_CHANGE_REPORT))
        self.assertEqual(execution["status"], "no-changes")
        self.assertEqual(self.workspace.emitted_outputs()["status"], "no-changes")

    def test_rejects_an_incompatible_formatter_before_comparing(self):
        docker = FakeDocker(help_text=BETA7_HELP)
        with self.assertRaises(rfct.ExecutionError) as caught:
            self._execute(docker)
        self.assertEqual(caught.exception.reason, "incompatible-formatter")

        phases = [argv for argv in docker.calls if "compare" in argv]
        self.assertEqual(phases, [], "comparison must not run against an incompatible image")
        execution = self.workspace.execution()
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(execution["reason"], "incompatible-formatter")
        self.assertTrue(execution["diagnostics"])

    def test_records_evidence_when_a_phase_fails(self):
        docker = FakeDocker(compare_code=3)
        with self.assertRaises(rfct.ExecutionError) as caught:
            self._execute(docker)
        self.assertEqual(caught.exception.reason, "comparison-failed")
        self.assertEqual(caught.exception.exit_code, 3)

        execution = self.workspace.execution()
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(execution["phases"], {"preflight": 0, "compare": 3})
        self.assertNotIn("format", execution["phases"])
        self.assertTrue((self.workspace.output / "logs" / "compare.log").is_file())
        self.assertEqual(self.workspace.emitted_outputs()["status"], "failed")

    def test_formatter_failure_does_not_produce_a_success_page(self):
        docker = FakeDocker(format_code=4)
        with self.assertRaises(rfct.ExecutionError) as caught:
            self._execute(docker)
        self.assertEqual(caught.exception.reason, "formatter-failed")
        self.assertEqual(self.workspace.execution()["status"], "failed")

    def test_cleans_up_only_its_own_invocation_containers(self):
        docker = FakeDocker()
        self._execute(docker)
        self.assertEqual(docker.removed_containers, ["rfct-compare-42-2", "rfct-format-42-2"])

    def test_cleans_up_after_a_failure(self):
        docker = FakeDocker(compare_code=1)
        with self.assertRaises(rfct.ExecutionError):
            self._execute(docker)
        self.assertEqual(docker.removed_containers, ["rfct-compare-42-2", "rfct-format-42-2"])

    def test_initializes_execution_evidence_before_comparing(self):
        recorded = {}

        class Recorder(FakeDocker):
            def __call__(self, argv, **kwargs):
                if "compare" in argv:
                    recorded["before_compare"] = json.loads(
                        (Path(self.output_directory) / "execution.json").read_text(encoding="utf-8")
                    )
                return super().__call__(argv, **kwargs)

        docker = Recorder()
        docker.output_directory = self.workspace.output
        self._execute(docker)
        self.assertEqual(recorded["before_compare"]["status"], "running")

    def test_never_passes_credentials_or_the_docker_socket_into_the_container(self):
        docker = FakeDocker()
        self._execute(docker)
        for argv in docker.calls:
            flat = " ".join(argv)
            self.assertNotIn("/var/run/docker.sock", flat)
            self.assertNotIn("AWS_", flat)
            self.assertNotIn("GITHUB_TOKEN", flat)
            self.assertNotIn("-e", argv)
            self.assertNotIn(str(Path.home()) + ":", flat)

    def test_mounts_the_configuration_and_source_read_only(self):
        docker = FakeDocker()
        self._execute(docker)
        compare = next(argv for argv in docker.calls if "compare" in argv)
        self.assertIn(f"{self.workspace.config}:{rfct.CONTAINER_CONFIG}:ro", compare)
        formatter = next(argv for argv in docker.calls if "format-report" in argv and "--help" not in argv)
        self.assertIn(f"{self.workspace.source}:{rfct.CONTAINER_CANDIDATE}:ro", formatter)
        self.assertIn(rfct.REQUIRED_FORMATTER_FLAG, formatter)

    def test_renders_identical_evidence_from_identical_fixture_inputs(self):
        self._execute(FakeDocker())
        first = (self.workspace.output / "execution.json").read_bytes()
        self._execute(FakeDocker())
        second = (self.workspace.output / "execution.json").read_bytes()
        self.assertEqual(first, second)

    def test_main_prints_the_execution_document(self):
        with mock.patch.object(rfct, "execute", return_value={"status": "changes"}):
            rfct.main()


class ActionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action = yaml.safe_load(ACTION_YAML_PATH.read_text(encoding="utf-8"))

    def test_declares_exactly_the_agreed_inputs(self):
        self.assertEqual(
            sorted(self.action["inputs"]),
            ["app-code-name", "candidate-source", "config-path", "output-directory", "rfct-image"],
        )

    def test_declares_exactly_the_agreed_outputs(self):
        self.assertEqual(
            sorted(self.action["outputs"]),
            [
                "artifact-directory",
                "candidate-version",
                "execution-result-path",
                "image-digest",
                "status",
            ],
        )

    def test_accepts_no_credential_inputs(self):
        for name in self.action["inputs"]:
            for forbidden in ("aws", "token", "secret", "password", "key"):
                self.assertNotIn(forbidden, name.lower(), name)

    def test_every_input_reaches_the_bundled_helper(self):
        step = self.action["runs"]["steps"][0]
        wired = "\n".join(step["env"].values())
        for name in self.action["inputs"]:
            self.assertIn(f"inputs.{name}", wired, name)
        self.assertIn("$GITHUB_ACTION_PATH/run_rfct.py", step["run"])

    def test_every_output_maps_to_the_invocation_step(self):
        for name, declared in self.action["outputs"].items():
            self.assertIn("steps.invoke.outputs.", declared["value"], name)


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(
            WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        cls.jobs = cls.workflow["jobs"]
        cls.run_rfct = cls.jobs["run-rfct"]
        cls.steps = cls.run_rfct["steps"]

    def _step(self, name):
        return next(step for step in self.steps if step.get("name") == name)

    def test_declares_the_opt_in_inputs_and_optional_secrets(self):
        workflow_call = self.workflow["on"]["workflow_call"]
        self.assertEqual(workflow_call["inputs"]["rfct-enabled"]["default"], "false")
        self.assertEqual(workflow_call["inputs"]["rfct-enabled"]["type"], "boolean")
        self.assertIn("@sha256:", workflow_call["inputs"]["rfct-image"]["default"])
        for name in ("rfct-baseline-release", "rfct-candidate-release"):
            self.assertEqual(workflow_call["inputs"][name]["default"], "")
        for name in ("RFCT_ECR_AWS_ACCESS_KEY_ID", "RFCT_ECR_AWS_SECRET_ACCESS_KEY"):
            self.assertEqual(workflow_call["secrets"][name]["required"], "false")

    def test_depends_on_the_plan_build_and_meta_jobs(self):
        self.assertEqual(sorted(self.run_rfct["needs"]), ["build", "meta", "plan-rfct"])

    def test_requires_a_successful_build_unless_a_release_candidate_is_used(self):
        condition = " ".join(self.run_rfct["if"].split())
        self.assertIn("needs.plan-rfct.outputs.decision == 'proceed'", condition)
        self.assertIn(
            "needs.plan-rfct.outputs.candidate-source == 'release' "
            "|| needs.build.result == 'success'",
            condition,
        )

    def test_build_exposes_the_package_version_and_source_revision(self):
        outputs = self.jobs["build"]["outputs"]
        self.assertIn("steps.BuildVersion.outputs.VERSION", outputs["package-version"])
        self.assertIn("github.sha", outputs["source-sha"])

    def test_loads_the_action_from_this_repository_not_the_addon_checkout(self):
        step = self._step("Run RFCT comparison")
        self.assertTrue(
            step["uses"].startswith(
                "splunk/addonfactory-workflow-addon-release/.github/actions/run-rfct@"
            ),
            step["uses"],
        )
        self.assertNotIn("./", step["uses"])

    def test_the_action_is_invoked_only_after_environments_are_prepared(self):
        self.assertIn(
            "steps.prepare-environments.outputs.prepared == 'true'",
            self._step("Run RFCT comparison")["if"],
        )

    def test_environment_preparation_fails_until_a_backend_is_selected(self):
        step = self._step("Prepare paired Splunk environments")
        self.assertIn("backend-not-selected", step["run"])
        self.assertIn("exit 1", step["run"])

    def test_both_backend_stubs_document_the_pending_decision(self):
        body = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertEqual(body.count("TODO: backend TBD (ADDON-90824)"), 2)

    def test_rejects_an_unresolvable_image_digest(self):
        step = self._step("Pull the RFCT image and pin it by digest")
        self.assertIn("Could not resolve a digest", step["run"])
        self.assertIn("ta-automation/rfct", step["run"])

    def test_rejects_an_incompatible_image_before_provisioning(self):
        names = [step.get("name") for step in self.steps]
        self.assertLess(
            names.index("Check RFCT formatter compatibility"),
            names.index("Prepare paired Splunk environments"),
        )
        self.assertIn("--ta-repo-path", self._step("Check RFCT formatter compatibility")["run"])

    def test_removes_registry_credentials_before_add_on_code_runs(self):
        names = [step.get("name") for step in self.steps]
        self.assertLess(
            names.index("Remove registry credentials"),
            names.index("Prepare paired Splunk environments"),
        )

    def test_uploads_evidence_and_tears_down_even_after_a_failure(self):
        self.assertIn("!cancelled()", self._step("Upload RFCT evidence")["if"])
        self.assertIn("always()", self._step("Tear down paired Splunk environments")["if"])
        self.assertIn(
            "always()", self._step("Remove temporary RFCT credentials and configuration")["if"]
        )

    def test_never_uploads_the_live_configuration(self):
        uploaded = self._step("Upload RFCT evidence")["with"]["path"]
        self.assertNotIn("config.json", uploaded)
        for expected in ("result.json", "execution.json", "comparison", "docs", "logs"):
            self.assertIn(expected, uploaded)

    def test_uses_a_unique_artifact_name_per_run_and_attempt(self):
        name = self.run_rfct["env"]["RFCT_ARTIFACT_NAME"]
        self.assertIn("github.run_id", name)
        self.assertIn("github.run_attempt", name)

    def test_does_not_use_continue_on_error_to_mask_a_failed_comparison(self):
        for step in self.steps:
            self.assertNotIn("continue-on-error", step)

    def test_checks_out_the_candidate_source_without_persisting_credentials(self):
        step = self._step("Checkout candidate source for formatter attribution")
        self.assertEqual(step["with"]["persist-credentials"], "false")
        self.assertIn(".git", self._step("Export candidate source without history")["run"])

    def test_remains_advisory_until_the_publication_policy_is_decided(self):
        self.assertNotIn("run-rfct", self.jobs["pre-publish"]["needs"])
        self.assertNotIn("plan-rfct", self.jobs["pre-publish"]["needs"])

    def test_pins_both_actions_to_the_same_immutable_commit(self):
        refs = set(PINNED_REF_RE.findall(WORKFLOW_PATH.read_text(encoding="utf-8")))
        self.assertEqual(len(refs), 1, f"both actions must share one ref, got {refs}")
        ref = refs.pop()
        self.assertRegex(ref, r"\A[0-9a-f]{40}\Z", "a branch or tag ref is mutable")

    def test_the_pinned_commit_is_not_stale(self):
        """A pin that predates an action edit silently runs the old helper.

        The first fixture run failed this way: the workflow pinned the commit
        before a planner fix, so CI executed the superseded code.
        """
        completed = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", *ACTION_DIRECTORIES],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        latest = completed.stdout.strip()
        if completed.returncode != 0 or not latest:
            self.skipTest("git history for the action directories is unavailable")
        ref = PINNED_REF_RE.findall(WORKFLOW_PATH.read_text(encoding="utf-8"))[0]
        self.assertEqual(
            ref,
            latest,
            "re-pin the actions to the commit that last changed them before pushing",
        )

    def test_passes_untrusted_values_through_the_environment_not_the_shell_body(self):
        for job in ("plan-rfct", "run-rfct"):
            for step in self.jobs[job]["steps"]:
                self.assertNotIn("${{", step.get("run", ""), f"{job}: {step.get('name')}")

    def test_verifies_app_identity_across_both_packages(self):
        step = self._step("Verify package identity and record checksums")
        self.assertIn("App identity mismatch", step["run"])
        self.assertIn("Expected exactly one .spl package", step["run"])
        self.assertIn("sha256sum", step["run"])
