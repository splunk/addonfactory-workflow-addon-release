import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ACTION_DIRECTORY = (
    Path(__file__).parents[1] / ".github" / "actions" / "prepare-ta-validator-exceptions"
)
ACTION_PATH = ACTION_DIRECTORY / "prepare.py"
ACTION_YAML_PATH = ACTION_DIRECTORY / "action.yml"
SPEC = importlib.util.spec_from_file_location("prepare_ta_validator_exceptions", ACTION_PATH)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "reusable-build-test-release.yml"


class EnvelopeWriterTests(unittest.TestCase):
    def test_writes_envelope_from_find_comment_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            with mock.patch.dict(
                os.environ,
                {
                    "INPUT_OUTPUT_PATH": str(output_path),
                    "INPUT_REPOSITORY": "splunk/example-ta",
                    "INPUT_PULL_REQUEST_NUMBER": "123",
                    "INPUT_COMMENT_ID": "456",
                    "INPUT_COMMENT_BODY": prepare.MARKER + "\ncustom content",
                },
                clear=True,
            ):
                prepare.main()

            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                {
                    "schema_version": 1,
                    "source": {
                        "provider": "github",
                        "repository": "splunk/example-ta",
                        "pull_request": 123,
                    },
                    "comment": {
                        "reference": "https://github.com/splunk/example-ta/pull/123#issuecomment-456",
                        "body": prepare.MARKER + "\ncustom content",
                    },
                },
            )

    def test_missing_comment_id_removes_stale_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            output_path.write_text('{"stale": true}\n', encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "INPUT_OUTPUT_PATH": str(output_path),
                    "INPUT_REPOSITORY": "splunk/example-ta",
                    "INPUT_PULL_REQUEST_NUMBER": "123",
                    "INPUT_COMMENT_BODY": prepare.MARKER,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "Missing required input comment_id"):
                    prepare.main()
            self.assertFalse(output_path.exists())

    def test_failed_write_preserves_complete_old_envelope_and_cleans_tempfile(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            old_content = b'{"old": "complete"}\n'
            output_path.write_bytes(old_content)
            with mock.patch.object(prepare.json, "dump", side_effect=OSError("write failed")):
                with self.assertRaisesRegex(OSError, "write failed"):
                    prepare._write_envelope(output_path, {"replacement": True})
            self.assertEqual(output_path.read_bytes(), old_content)
            self.assertEqual(list(output_path.parent.glob(".envelope.json.*")), [])

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
        self.assertIn("steps.find-comment.outputs.comment-id == ''", self.action)
        self.assertIn("TA_VALIDATOR_REPOSITORY: ${{ inputs.repository }}", self.action)
        self.assertIn("TA_VALIDATOR_PULL_REQUEST_NUMBER: ${{ inputs.pull_request_number }}", self.action)
        self.assertIn("Number(process.env.TA_VALIDATOR_PULL_REQUEST_NUMBER)", self.action)
        self.assertIn("process.env.TA_VALIDATOR_REPOSITORY.split('/', 2)", self.action)
        self.assertNotIn("multiple marked", self.action)
        self.assertNotIn("pin", self.action.lower())

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
        self.assertRegex(
            preparation,
            r"uses: splunk/addonfactory-workflow-addon-release/\.github/actions/"
            r"prepare-ta-validator-exceptions@[0-9a-f]{40}",
        )
        self.assertIn("validate --repository /addon --pull-request-input /run/ta-validator-pr-exceptions.json", workflow)
        self.assertLess(
            workflow.index("prepare-ta-validator-exceptions:"),
            workflow.index("run-gs-scorecard:"),
        )

    def test_full_evaluation_consumes_only_validated_pr_envelope(self):
        run_scorecard = self.workflow[
            self.workflow.index("  run-gs-scorecard:") : self.workflow.index(
                "\n  setup:", self.workflow.index("  run-gs-scorecard:")
            )
        ]
        self.assertIn("- prepare-ta-validator-exceptions", run_scorecard)
        self.assertIn("needs.prepare-ta-validator-exceptions.result == 'success'", run_scorecard)
        self.assertIn("actions/download-artifact@v8", run_scorecard)
        self.assertIn("TA_VALIDATOR_PULL_REQUEST_INPUT=/run/ta-validator-pr-exceptions.json", run_scorecard)


if __name__ == "__main__":
    unittest.main()
