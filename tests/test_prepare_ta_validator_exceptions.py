import importlib.util
import io
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
    def test_writes_only_active_canonical_yaml_and_comment_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "pr-exceptions.yaml"
            github_output = Path(directory) / "github-output"
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
                    "INPUT_REPOSITORY": "splunk/example-ta",
                    "INPUT_PULL_REQUEST_NUMBER": "123",
                    "INPUT_COMMENT_ID": "456",
                    "INPUT_COMMENT_BODY": comment_body,
                    "GITHUB_OUTPUT": str(github_output),
                },
                clear=True,
            ):
                prepare.main()

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "version: 1\nexceptions: []\n",
            )
            self.assertEqual(
                github_output.read_text(encoding="utf-8"),
                "comment-reference=https://github.com/splunk/example-ta/pull/123#issuecomment-456\n",
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


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.action = ACTION_YAML_PATH.read_text(encoding="utf-8")

    def test_action_uses_find_comment_and_creates_only_when_absent(self):
        self.assertIn("peter-evans/find-comment@v4", self.action)
        self.assertIn("actions/github-script@v8", self.action)
        self.assertIn("comment_author:", self.action)
        self.assertIn("comment-author: ${{ inputs.comment_author }}", self.action)
        self.assertIn("steps.find-comment.outputs.comment-id == ''", self.action)
        self.assertIn("TA_VALIDATOR_REPOSITORY: ${{ inputs.repository }}", self.action)
        self.assertIn("TA_VALIDATOR_PULL_REQUEST_NUMBER: ${{ inputs.pull_request_number }}", self.action)
        self.assertIn("Number(process.env.TA_VALIDATOR_PULL_REQUEST_NUMBER)", self.action)
        self.assertIn("process.env.TA_VALIDATOR_REPOSITORY.split('/', 2)", self.action)
        self.assertNotIn("multiple marked", self.action)
        self.assertNotIn("pin", self.action.lower())

    def test_action_exposes_comment_reference_and_category_template(self):
        self.assertIn("comment-reference:", self.action)
        self.assertIn("id: write-exception-document", self.action)
        self.assertEqual(self.action.count("category: false_positive"), 2)

    def test_shared_action_runs_validation_and_evaluation_modes(self):
        action = RUN_ACTION_YAML_PATH.read_text(encoding="utf-8")
        self.assertIn("name: Run TA Validator", action)
        self.assertIn("mode:", action)
        self.assertIn("aws-access-key-id:", action)
        self.assertIn("aws-secret-access-key:", action)
        self.assertIn("aws-region:", action)
        self.assertIn("pull-request-input-path:", action)
        self.assertIn("addon-read-only:", action)
        self.assertIn("actions/checkout@v7", action)
        self.assertIn("aws-actions/configure-aws-credentials@v6", action)
        self.assertIn("aws-actions/amazon-ecr-login@v2", action)
        self.assertIn("docker pull", action)
        self.assertIn("docker run", action)
        self.assertIn('case "$mode" in', action)
        self.assertIn('validate)', action)
        self.assertIn('evaluate)', action)
        self.assertIn("-e TA_VALIDATOR_PULL_REQUEST_INPUT=/run/ta-validator-pr-exceptions.json", action)
        self.assertNotIn("eval ", action)

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
        self.assertIn(
            "prepare-ta-validator-exceptions@25681ddfc060283d1f4196bdd599ac979d29038c",
            preparation,
        )
        self.assertIn(
            "run-ta-validator@7a46c761d99752e034ff12b1cb230c2477d29106",
            preparation,
        )
        self.assertIn("mode: validate", preparation)
        self.assertIn("addon-read-only: true", preparation)
        self.assertIn("aws-access-key-id: ${{ secrets.GSSA_AWS_ACCESS_KEY_ID }}", preparation)
        self.assertIn("aws-secret-access-key: ${{ secrets.GSSA_AWS_SECRET_ACCESS_KEY }}", preparation)
        self.assertIn("aws-region: us-west-2", preparation)
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
        self.assertIn(
            "pull-request-input-path: ${{ github.event_name == 'pull_request' && format('{0}/ta-validator-pr-exceptions.json', runner.temp) || '' }}",
            run_scorecard,
        )
        self.assertIn(
            "run-ta-validator@7a46c761d99752e034ff12b1cb230c2477d29106",
            run_scorecard,
        )
        self.assertEqual(
            run_scorecard.count("run-ta-validator@7a46c761d99752e034ff12b1cb230c2477d29106"),
            1,
        )
        self.assertIn("- name: Run TA Validator\n", run_scorecard)
        self.assertNotIn("Run TA Validator for pull request", run_scorecard)
        self.assertNotIn("Run TA Validator outside a pull request", run_scorecard)
        self.assertIn(
            "github.event_name == 'pull_request' && format('{0}/ta-validator-pr-exceptions.json', runner.temp) || ''",
            run_scorecard,
        )
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
