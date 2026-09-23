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
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ACTION_DIRECTORY = (
    Path(__file__).parents[1] / ".github" / "actions" / "prepare-ta-validator-exceptions"
)
ACTION_PATH = ACTION_DIRECTORY / "prepare.py"
ACTION_YAML_PATH = ACTION_DIRECTORY / "action.yml"
RUN_ACTION_YAML_PATH = Path(__file__).parents[1] / ".github" / "actions" / "run-ta-validator" / "action.yml"
SPEC = importlib.util.spec_from_file_location("prepare_ta_validator_exceptions", ACTION_PATH)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "reusable-build-test-release.yml"


class PullRequestExceptionDocumentTests(unittest.TestCase):
    def test_writes_only_active_canonical_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "pr-exceptions.yaml"
            comment_body = """<!-- ta-validator-exceptions:v1 -->
<!-- ta-validator-exceptions-config:start -->
```yaml
version: 1
exceptions: []
```
<!-- ta-validator-exceptions-config:end -->
```yaml
version: 1
exceptions: [not-active]
```
"""
            with mock.patch.dict(
                os.environ,
                {
                    "INPUT_OUTPUT_PATH": str(output_path),
                    "INPUT_COMMENT_BODY": comment_body,
                },
                clear=True,
            ):
                prepare.main()

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "version: 1\nexceptions: []\n",
            )

    def test_rejects_duplicate_or_misordered_markers(self):
        valid_fence = "```yaml\nversion: 1\nexceptions: []\n```"
        invalid_bodies = (
            f"{prepare.MARKER}\n{prepare.CONFIG_START_MARKER}\n{valid_fence}\n"
            f"{prepare.CONFIG_END_MARKER}\n{prepare.CONFIG_START_MARKER}",
            f"{prepare.MARKER}\n{prepare.MARKER}\n{prepare.CONFIG_START_MARKER}\n"
            f"{valid_fence}\n{prepare.CONFIG_END_MARKER}",
            f"{prepare.CONFIG_START_MARKER}\n{valid_fence}\n{prepare.CONFIG_END_MARKER}\n"
            f"{prepare.MARKER}",
        )

        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "marker"):
                    prepare.extract_pull_request_exception_document(body)

    def test_rejects_multiple_or_malformed_active_fences(self):
        invalid_fences = (
            "```yaml\nversion: 1\nexceptions: []\n```\n"
            "```yaml\nversion: 1\nexceptions: []\n```",
            "```yml\nversion: 1\nexceptions: []\n```",
            "```yaml\nversion: 1\nexceptions: []\n````",
            "version: 1\nexceptions: []",
        )

        for fence in invalid_fences:
            body = (
                f"{prepare.MARKER}\n{prepare.CONFIG_START_MARKER}\n{fence}\n"
                f"{prepare.CONFIG_END_MARKER}"
            )
            with self.subTest(fence=fence):
                with self.assertRaisesRegex(ValueError, "fence"):
                    prepare.extract_pull_request_exception_document(body)

    def test_write_pull_request_exception_document_replaces_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "input.yaml"
            output_path.write_text("old: true\n", encoding="utf-8")
            body = (
                f"{prepare.MARKER}\n{prepare.CONFIG_START_MARKER}\n```yaml\n"
                "version: 1\nexceptions: []\n```\n"
                f"{prepare.CONFIG_END_MARKER}"
            )
            prepare.write_pull_request_exception_document(output_path, body)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "version: 1\nexceptions: []\n")

    def test_writes_crlf_document_without_newline_translation(self):
        def translating_write_text(path, data, encoding):
            with io.open(path, "w", encoding=encoding) as output_file:
                output_file.write(data.replace("\n", "\r\n"))

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "input.yaml"
            body = (
                f"{prepare.MARKER}\r\n{prepare.CONFIG_START_MARKER}\r\n```yaml\r\n"
                "version: 1\r\nexceptions: []\r\n```\r\n"
                f"{prepare.CONFIG_END_MARKER}"
            )
            with mock.patch.object(Path, "write_text", new=translating_write_text):
                prepare.write_pull_request_exception_document(output_path, body)

            self.assertEqual(
                output_path.read_bytes(), b"version: 1\r\nexceptions: []\r\n"
            )

    def test_annotations_escape_workflow_control_characters(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stream:
            prepare._workflow_message("error", "bad%\r\nmessage")
        self.assertEqual(stream.getvalue(), "::error::bad%25%0D%0Amessage\n")

    def test_creates_missing_comment_with_python_standard_library_client(self):
        creator = getattr(prepare, "create_pull_request_exception_comment", None)
        self.assertIsNotNone(creator)
        if creator is None:
            return

        response = mock.MagicMock()
        response.read.return_value = json.dumps(
            {"id": 456, "body": prepare.COMMENT_TEMPLATE}
        ).encode()
        response.__enter__.return_value = response
        with mock.patch.object(prepare, "urlopen", return_value=response) as urlopen:
            comment_body = creator(
                token="app-token",
                repository="splunk/example-ta",
                pull_request_number=123,
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/splunk/example-ta/issues/123/comments")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"body": prepare.COMMENT_TEMPLATE})
        self.assertEqual(request.get_header("Authorization"), "Bearer app-token")
        self.assertEqual(comment_body, prepare.COMMENT_TEMPLATE)


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.action = ACTION_YAML_PATH.read_text(encoding="utf-8")

    def test_action_uses_pinned_find_comment_and_python_for_creation(self):
        self.assertIn(
            "peter-evans/find-comment@b30e6a3c0ed37e7c023ccd3f1db5c6c0b0c23aad",
            self.action,
        )
        self.assertNotIn("actions/github-script", self.action)
        self.assertNotIn("createComment", self.action)
        self.assertNotIn("script: |", self.action)
        self.assertIn("comment_author:", self.action)
        self.assertIn("comment-author: ${{ inputs.comment_author }}", self.action)
        self.assertIn("INPUT_TOKEN: ${{ inputs.token }}", self.action)
        self.assertIn("INPUT_REPOSITORY: ${{ inputs.repository }}", self.action)
        self.assertIn("INPUT_PULL_REQUEST_NUMBER: ${{ inputs.pull_request_number }}", self.action)
        self.assertNotIn("multiple marked", self.action)
        self.assertNotIn("pin", self.action.lower())

    def test_action_passes_only_comment_body_and_category_template(self):
        self.assertNotIn("outputs:", self.action)
        self.assertNotIn("id: write-exception-document", self.action)
        self.assertNotIn("INPUT_COMMENT_ID", self.action)
        self.assertIn("INPUT_COMMENT_BODY: ${{ steps.find-comment.outputs.comment-body }}", self.action)
        template = getattr(prepare, "COMMENT_TEMPLATE", None)
        self.assertIsNotNone(template)
        if template is not None:
            self.assertEqual(template.count("category: false_positive"), 2)
        self.assertNotIn("category: false_positive", self.action)

    def test_shared_action_runs_merge_and_evaluation_modes(self):
        action = RUN_ACTION_YAML_PATH.read_text(encoding="utf-8")
        self.assertIn("name: Run TA Validator", action)
        self.assertIn("mode:", action)
        self.assertIn("aws-access-key-id:", action)
        self.assertIn("aws-secret-access-key:", action)
        self.assertIn("aws-region:", action)
        self.assertNotIn("additional-exceptions-path:", action)
        self.assertNotIn("output-exceptions-path:", action)
        self.assertNotIn("effective-exceptions-path:", action)
        self.assertNotIn("addon-read-only:", action)
        self.assertIn("actions/checkout@v7", action)
        self.assertIn("aws-actions/configure-aws-credentials@v6", action)
        self.assertIn("aws-actions/amazon-ecr-login@v2", action)
        self.assertIn("docker pull", action)
        self.assertIn("docker run", action)
        self.assertIn('case "$mode" in', action)
        self.assertIn('merge)', action)
        self.assertIn('evaluate)', action)
        self.assertIn(
            "merge --repository /addon --additional-file /run/additional.yaml --output /output/.ta-validator-exceptions.yaml",
            action,
        )
        self.assertIn('--user "$(id -u):$(id -g)"', action)
        self.assertIn(
            'comment_exceptions_path="$RUNNER_TEMP/ta-validator-comment-exceptions.yaml"',
            action,
        )
        self.assertIn(
            'effective_exceptions_path="$RUNNER_TEMP/.ta-validator-exceptions.yaml"',
            action,
        )
        self.assertIn(
            'cp "$effective_exceptions_path" "$GITHUB_WORKSPACE/.ta-validator-exceptions.yaml"',
            action,
        )
        stale_interfaces = (
            "--pull-request-" + "exceptions",
            "--pull-request-" + "exception-reference",
            "TA_VALIDATOR_PULL_REQUEST_" + "EXCEPTIONS",
            "TA_VALIDATOR_PULL_REQUEST_" + "EXCEPTION_REFERENCE",
        )
        for stale_interface in stale_interfaces:
            self.assertNotIn(stale_interface, action)
        self.assertNotIn("eval ", action)

    def test_default_image_implements_the_effective_file_interface(self):
        self.assertIn(
            'default: "mr-140-3fee426db3a8-amd64"',
            self.workflow,
        )

    def test_ta_validator_pr_exception_preflight_precedes_full_evaluation(self):
        workflow = self.workflow
        preparation = workflow[
            workflow.index("  prepare-ta-validator-exceptions:") : workflow.index(
                "\n  run-gs-scorecard:", workflow.index("  prepare-ta-validator-exceptions:")
            )
        ]
        self.assertIn("permissions:\n      contents: read", preparation)
        self.assertNotIn("\n      issues: write", preparation)
        self.assertIn("permission-issues: write", preparation)
        self.assertIn("permission-pull-requests: write", preparation)
        self.assertIn("client-id: ${{ secrets.GH_APP_CLIENT_ID }}", preparation)
        self.assertNotIn("owner: ${{ github.repository_owner }}", preparation)
        self.assertIn(
            "comment_author: ${{ steps.app-token.outputs.app-slug }}[bot]", preparation
        )
        self.assertRegex(
            preparation,
            r"uses: splunk/addonfactory-workflow-addon-release/\.github/actions/"
            r"prepare-ta-validator-exceptions@[0-9a-f]{40}",
        )
        action_sha = "cf4924a67026afd773aa5371c1944f6cbdde2241"
        self.assertIn(f"prepare-ta-validator-exceptions@{action_sha}", preparation)
        self.assertIn(f"run-ta-validator@{action_sha}", preparation)
        self.assertNotIn("outputs:", preparation)
        self.assertNotIn("comment-" + "reference", preparation)
        self.assertNotIn("id: prepare-ta-validator-exceptions", preparation)
        self.assertIn("mode: merge", preparation)
        self.assertNotIn("addon-read-only:", preparation)
        self.assertIn("aws-access-key-id: ${{ secrets.GSSA_AWS_ACCESS_KEY_ID }}", preparation)
        self.assertIn("aws-secret-access-key: ${{ secrets.GSSA_AWS_SECRET_ACCESS_KEY }}", preparation)
        self.assertIn("aws-region: us-west-2", preparation)
        self.assertIn(
            "output_path: ${{ runner.temp }}/ta-validator-comment-exceptions.yaml",
            preparation,
        )
        self.assertNotIn("additional-exceptions-path:", preparation)
        self.assertNotIn("output-exceptions-path:", preparation)
        self.assertIn("name: ta-validator-exceptions", preparation)
        self.assertIn("path: ${{ runner.temp }}/.ta-validator-exceptions.yaml", preparation)
        self.assertIn("include-hidden-files: true", preparation)
        self.assertIn("retention-days: 1", preparation)
        self.assertNotIn("actions/checkout@v7", preparation)
        self.assertNotIn("aws-actions/configure-aws-credentials@v6", preparation)
        self.assertNotIn("aws-actions/amazon-ecr-login@v2", preparation)
        self.assertNotIn("docker pull", preparation)
        self.assertNotIn("docker run", preparation)
        self.assertLess(
            workflow.index("prepare-ta-validator-exceptions:"),
            workflow.index("run-gs-scorecard:"),
        )

    def test_full_evaluation_consumes_only_validated_exception_input(self):
        run_scorecard = self.workflow[
            self.workflow.index("  run-gs-scorecard:") : self.workflow.index(
                "\n  setup:", self.workflow.index("  run-gs-scorecard:")
            )
        ]
        self.assertIn("- prepare-ta-validator-exceptions", run_scorecard)
        self.assertIn("needs.prepare-ta-validator-exceptions.result == 'success'", run_scorecard)
        self.assertIn("actions/download-artifact@v8", run_scorecard)
        self.assertIn("name: ta-validator-exceptions", run_scorecard)
        self.assertIn(
            "run-ta-validator@cf4924a67026afd773aa5371c1944f6cbdde2241",
            run_scorecard,
        )
        self.assertEqual(
            run_scorecard.count("run-ta-validator@cf4924a67026afd773aa5371c1944f6cbdde2241"),
            1,
        )
        self.assertIn("- name: Run TA Validator\n", run_scorecard)
        self.assertNotIn("Run TA Validator for pull request", run_scorecard)
        self.assertNotIn("Run TA Validator outside a pull request", run_scorecard)
        self.assertNotIn("effective-exceptions-path:", run_scorecard)
        self.assertNotIn("comment-" + "reference", run_scorecard)
        self.assertIn("mode: evaluate", run_scorecard)
        self.assertIn("aws-access-key-id: ${{ secrets.GSSA_AWS_ACCESS_KEY_ID }}", run_scorecard)
        self.assertIn("aws-secret-access-key: ${{ secrets.GSSA_AWS_SECRET_ACCESS_KEY }}", run_scorecard)
        self.assertIn("aws-region: us-west-2", run_scorecard)
        self.assertNotIn("actions/checkout@v7", run_scorecard)
        self.assertNotIn("aws-actions/configure-aws-credentials@v6", run_scorecard)
        self.assertNotIn("aws-actions/amazon-ecr-login@v2", run_scorecard)
        self.assertNotIn("docker pull", run_scorecard)
        self.assertNotIn("docker run", run_scorecard)


if __name__ == "__main__":
    unittest.main()
