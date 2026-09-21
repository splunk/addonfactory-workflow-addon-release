import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ACTION_PATH = (
    Path(__file__).parents[1]
    / ".github"
    / "actions"
    / "prepare-ta-validator-exceptions"
    / "prepare.py"
)
SPEC = importlib.util.spec_from_file_location("prepare_ta_validator_exceptions", ACTION_PATH)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "reusable-build-test-release.yml"


def comment(body, number=456, pin=None):
    return {
        "id": number,
        "body": body,
        "html_url": f"https://github.com/splunk/example-ta/pull/123#issuecomment-{number}",
        "user": {"login": "automation"},
        "updated_at": "2026-09-21T00:00:00Z",
        "pin": pin,
    }


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class LifecycleTests(unittest.TestCase):
    def _run(self, responses, output_path):
        requests = []

        def urlopen(api_request):
            requests.append(api_request)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return Response(response)

        with mock.patch.dict(
            os.environ,
            {
                "INPUT_TOKEN": "token",
                "INPUT_REPOSITORY": "splunk/example-ta",
                "INPUT_PULL_REQUEST_NUMBER": "123",
                "INPUT_OUTPUT_PATH": str(output_path),
            },
            clear=False,
        ), mock.patch.object(prepare.request, "urlopen", side_effect=urlopen):
            prepare.main()
        return requests

    def test_fetches_paginated_comments_at_one_hundred_per_page(self):
        requests = []

        def urlopen(api_request):
            requests.append(api_request)
            return Response([{}] * (100 if len(requests) == 1 else 1))

        with mock.patch.object(prepare.request, "urlopen", side_effect=urlopen):
            self.assertEqual(len(prepare.fetch_comments("token", "splunk/example-ta", 123)), 101)

        self.assertTrue(requests[0].full_url.endswith("per_page=100&page=1"))
        self.assertTrue(requests[1].full_url.endswith("per_page=100&page=2"))
        self.assertEqual(requests[0].get_header("X-github-api-version"), "2026-03-10")

    def test_creates_exact_template_then_pins_and_writes_private_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            created = comment(prepare.TEMPLATE, 456)
            inspected = comment(prepare.TEMPLATE, 456, None)
            requests = self._run([[], created, inspected, comment(prepare.TEMPLATE, 456, {"pinned_at": "now"})], output_path)

            self.assertEqual([request.get_method() for request in requests], ["GET", "POST", "GET", "PUT"])
            self.assertEqual(json.loads(requests[1].data.decode("utf-8")), {"body": prepare.TEMPLATE})
            self.assertTrue(requests[3].full_url.endswith("/issues/comments/456/pin"))
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            envelope = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["source"], {"provider": "github", "repository": "splunk/example-ta", "pull_request": 123})
            self.assertEqual(envelope["comment"], {"reference": inspected["html_url"], "body": prepare.TEMPLATE})
            self.assertNotIn("id", envelope["comment"])
            self.assertNotIn("author", envelope["comment"])
            self.assertNotIn("updated_at", envelope["comment"])

    def test_existing_marked_comment_is_never_overwritten_or_re_pinned(self):
        body = prepare.MARKER + "\ncustom content"
        pinned = comment(body, pin={"pinned_at": "now"})
        with tempfile.TemporaryDirectory() as directory:
            requests = self._run([[pinned], pinned], Path(directory) / "envelope.json")

        self.assertEqual([request.get_method() for request in requests], ["GET", "GET"])
        self.assertNotIn("PATCH", [request.get_method() for request in requests])
        self.assertEqual(json.loads(requests[1].data.decode("utf-8")) if requests[1].data else None, None)

    def test_re_pins_sole_unpinned_comment_without_updating_its_body(self):
        body = prepare.MARKER + "\n$(touch /tmp/not-executed)"
        listed = comment(body, 789)
        inspected = comment(body, 789, None)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            requests = self._run([[listed], inspected, comment(body, 789, {"pinned_at": "now"})], output_path)

            self.assertEqual([request.get_method() for request in requests], ["GET", "GET", "PUT"])
            self.assertEqual(json.loads(output_path.read_text())["comment"]["body"], body)
        self.assertNotIn("PATCH", [request.get_method() for request in requests])

    def test_multiple_marked_comments_fail_closed_and_remove_stale_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            output_path.write_text('{"stale": true}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "multiple marked"):
                self._run([[comment(prepare.MARKER, 1), comment(prepare.MARKER, 2)]], output_path)
            self.assertFalse(output_path.exists())

    def test_api_errors_are_explicit_and_remove_stale_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            output_path.write_text('{"stale": true}\n', encoding="utf-8")
            failure = prepare.error.HTTPError("https://example.test", 401, "Unauthorized", {}, io.BytesIO())
            with self.assertRaisesRegex(RuntimeError, "comments API returned 401"):
                self._run([failure], output_path)
            self.assertFalse(output_path.exists())

    def test_create_and_pin_errors_remain_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            create_failure = prepare.error.HTTPError("https://example.test", 403, "Forbidden", {}, io.BytesIO())
            with self.assertRaisesRegex(RuntimeError, "create API returned 403"):
                self._run([[], create_failure], output_path)

            pin_failure = prepare.error.HTTPError("https://example.test", 403, "Forbidden", {}, io.BytesIO())
            with self.assertRaisesRegex(RuntimeError, "pin API returned 403"):
                self._run([[comment(prepare.MARKER)], comment(prepare.MARKER, pin=None), pin_failure], output_path)


class FilesystemTests(unittest.TestCase):
    def test_invalid_pull_request_number_removes_stale_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            output_path.write_text('{"stale": true}\n', encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "INPUT_TOKEN": "token",
                    "INPUT_REPOSITORY": "splunk/example-ta",
                    "INPUT_PULL_REQUEST_NUMBER": "0",
                    "INPUT_OUTPUT_PATH": str(output_path),
                },
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    prepare.main()
            self.assertFalse(output_path.exists())

    def test_missing_non_output_input_removes_stale_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "envelope.json"
            output_path.write_text('{"stale": true}\n', encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"INPUT_OUTPUT_PATH": str(output_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "Missing required input token"):
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

    def test_ta_validator_pr_exception_preflight_precedes_full_evaluation(self):
        workflow = self.workflow
        preparation = workflow[
            workflow.index("  prepare-ta-validator-exceptions:") : workflow.index(
                "\n  run-gs-scorecard:", workflow.index("  prepare-ta-validator-exceptions:")
            )
        ]
        self.assertIn("prepare-ta-validator-exceptions:", workflow)
        self.assertRegex(
            workflow,
            r"prepare-ta-validator-exceptions:\n(?:.*\n)*?    if: \$\{\{ github\.event_name == 'pull_request' \}\}",
        )
        self.assertIn("permissions:\n      contents: read", preparation)
        self.assertNotIn("\n      issues: write", preparation)
        self.assertIn("permission-issues: write", preparation)
        self.assertIn("app-id: ${{ secrets.GH_APP_CLIENT_ID }}", preparation)
        self.assertNotIn("client-id: ${{ secrets.GH_APP_CLIENT_ID }}", preparation)
        self.assertIn("WORKFLOW_REF: ${{ github.workflow_ref }}", preparation)
        self.assertIn("expected_workflow_ref=", preparation)
        self.assertNotIn("github.workflow_sha", preparation)
        self.assertIn("ref: ${{ steps.resolve-workflow-ref.outputs.ref }}", preparation)
        self.assertIn("repository: splunk/addonfactory-workflow-addon-release", workflow)
        self.assertIn("path: .workflow-actions", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("uses: ./.workflow-actions/.github/actions/prepare-ta-validator-exceptions", workflow)
        self.assertIn("validate --repository /addon --pull-request-input /run/ta-validator-pr-exceptions.json", workflow)
        self.assertIn("-v \"$(pwd)\":/addon:ro", workflow)
        self.assertIn(
            "-v \"$RUNNER_TEMP/ta-validator-pr-exceptions.json\":/run/ta-validator-pr-exceptions.json:ro",
            workflow,
        )
        self.assertIn("name: ta-validator-pr-exceptions", workflow)
        self.assertIn("retention-days: 1", workflow)
        self.assertLess(
            workflow.index("prepare-ta-validator-exceptions:"),
            workflow.index("run-gs-scorecard:"),
        )

    def test_full_evaluation_consumes_only_validated_pr_envelope(self):
        workflow = self.workflow
        run_scorecard = workflow[
            workflow.index("  run-gs-scorecard:") : workflow.index(
                "\n  setup:", workflow.index("  run-gs-scorecard:")
            )
        ]
        self.assertIn("- prepare-ta-validator-exceptions", run_scorecard)
        self.assertIn("needs.prepare-ta-validator-exceptions.result == 'success'", run_scorecard)
        self.assertIn("actions/download-artifact@v8", run_scorecard)
        self.assertIn("name: ta-validator-pr-exceptions", run_scorecard)
        self.assertIn("TA_VALIDATOR_PULL_REQUEST_INPUT=/run/ta-validator-pr-exceptions.json", run_scorecard)
        self.assertNotIn("/gssa-ignore", workflow)
        self.assertNotIn("collect-gssa-suppressions", workflow)
        self.assertNotIn("GSSA_SUPPRESSIONS_FILE", workflow)
        self.assertNotRegex(workflow, r"(?i)(?:new-)?gssa.*exceptions")

    def test_pre_publish_accepts_successful_or_skipped_exception_preparation(self):
        workflow = self.workflow
        pre_publish = workflow[workflow.index("  pre-publish:") :]
        self.assertIn("- prepare-ta-validator-exceptions", pre_publish)
        self.assertIn('select(.result != "skipped" and .result != "success")', pre_publish)

    def _resolve_workflow_ref(self, workflow_ref):
        preparation = self.workflow[
            self.workflow.index("  prepare-ta-validator-exceptions:") : self.workflow.index(
                "\n  run-gs-scorecard:", self.workflow.index("  prepare-ta-validator-exceptions:")
            )
        ]
        shell = re.search(
            r"      - name: Resolve immutable reusable workflow ref\n"
            r"        id: resolve-workflow-ref\n"
            r"        env:\n"
            r"          WORKFLOW_REF: \$\{\{ github\.workflow_ref \}\}\n"
            r"        run: \|\n(?P<shell>(?:          .*\n)+?)"
            r"      - name: Checkout workflow actions",
            preparation,
        )
        self.assertIsNotNone(shell)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "github-output"
            completed = subprocess.run(
                ["sh", "-c", textwrap.dedent(shell.group("shell"))],
                check=False,
                capture_output=True,
                env={**os.environ, "WORKFLOW_REF": workflow_ref, "GITHUB_OUTPUT": str(output_path)},
                text=True,
            )
            output = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        return completed, output

    def test_workflow_ref_guard_extracts_only_the_canonical_immutable_sha(self):
        revision = "a" * 40
        completed, output = self._resolve_workflow_ref(
            "splunk/addonfactory-workflow-addon-release/.github/workflows/"
            f"reusable-build-test-release.yml@{revision}"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(output, f"ref={revision}\n")

    def test_workflow_ref_guard_rejects_mutable_or_foreign_references(self):
        for workflow_ref in (
            "splunk/addonfactory-workflow-addon-release/.github/workflows/"
            "reusable-build-test-release.yml@refs/heads/main",
            "splunk/untrusted/.github/workflows/reusable-build-test-release.yml@" + "a" * 40,
        ):
            with self.subTest(workflow_ref=workflow_ref):
                completed, output = self._resolve_workflow_ref(workflow_ref)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
