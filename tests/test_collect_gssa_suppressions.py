import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


COLLECTOR_PATH = (
    Path(__file__).parents[1]
    / ".github"
    / "actions"
    / "collect-gssa-suppressions"
    / "collect.py"
)
SPEC = importlib.util.spec_from_file_location("collect_gssa_suppressions", COLLECTOR_PATH)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


CONTEXT = {"repository": "splunk/example-ta", "pullRequest": 123}
WORKFLOW_PATH = (
    Path(__file__).parents[1] / ".github" / "workflows" / "reusable-build-test-release.yml"
)


def comment(body, number=1, author="author"):
    return {
        "body": body,
        "user": {"login": author},
        "html_url": f"https://github.com/splunk/example-ta/pull/123#issuecomment-{number}",
    }


class ParseCommentTests(unittest.TestCase):
    def test_combines_valid_comments_from_any_author(self):
        result = collector.build_manifest(
            [
                comment("/gssa-ignore\ncheck: kvstore-state\nreason: Accepted legacy limitation."),
                comment(
                    "/gssa-ignore\ndetection: sensitive-data/credential-exposure\n"
                    "reason: Tracked in ADDON-12345.",
                    2,
                    "reviewer",
                ),
            ],
            CONTEXT,
        )

        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            [
                (entry["type"], entry["check_slug"], entry.get("detection_slug"))
                for entry in result["manifest"]["suppressions"]
            ],
            [
                ("check", "kvstore-state", None),
                ("detection", "sensitive-data", "credential-exposure"),
            ],
        )
        self.assertEqual(result["manifest"]["suppressions"][1]["declaration"]["author"], "reviewer")
        self.assertNotIn("comment_id", result["manifest"]["suppressions"][1]["declaration"])

    def test_rejects_entire_malformed_directive_and_keeps_valid_siblings(self):
        result = collector.build_manifest(
            [
                comment("/gssa-ignore\ncheck: valid\nowner: author\nreason: Accepted."),
                comment("/gssa-ignore\ncheck: unit-tests\nreason: Accepted.", 2),
            ],
            CONTEXT,
        )

        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(
            [entry["check_slug"] for entry in result["manifest"]["suppressions"]],
            ["unit-tests"],
        )

    def test_requires_reason_author_reference_and_valid_detection(self):
        invalid_comments = [
            comment("/gssa-ignore\ncheck: kvstore-state"),
            comment("/gssa-ignore\ncheck: \nreason: Accepted.", 2),
            comment("/gssa-ignore\ndetection: sensitive-data/\nreason: Accepted.", 3),
            comment("/gssa-ignore\ncheck: a\nreason: One.\nreason: Two.", 4),
            {"body": "/gssa-ignore\ncheck: a\nreason: Accepted.", "html_url": "https://x"},
            {"body": "/gssa-ignore\ncheck: a\nreason: Accepted.", "user": {"login": "a"}},
        ]
        result = collector.build_manifest(invalid_comments, CONTEXT)

        self.assertIsNone(result["manifest"])
        self.assertEqual(len(result["warnings"]), len(invalid_comments))

    def test_warns_when_a_directive_has_no_current_github_user(self):
        result = collector.build_manifest(
            [
                {
                    "body": "/gssa-ignore\ncheck: kvstore-state\nreason: Accepted.",
                    "html_url": "https://github.com/splunk/example-ta/pull/123#issuecomment-1",
                    "user": None,
                }
            ],
            CONTEXT,
        )

        self.assertIsNone(result["manifest"])
        self.assertEqual(
            result["warnings"],
            [
                "/gssa-ignore requires an author and reference in "
                "https://github.com/splunk/example-ta/pull/123#issuecomment-1"
            ],
        )

    def test_preserves_duplicates_slashes_and_shell_text_as_literal_data(self):
        unsafe_path = Path("/tmp/unsafe")
        self.assertFalse(unsafe_path.exists(), "test fixture requires /tmp/unsafe to be absent")
        result = collector.build_manifest(
            [
                comment(
                    "\n/gssa-ignore\ncheck: $(touch /tmp/unsafe)\n"
                    "detection: sensitive-data/credential/exposure\n"
                    "check: $(touch /tmp/unsafe)\nreason: Kept literally."
                )
            ],
            CONTEXT,
        )

        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            [entry["check_slug"] for entry in result["manifest"]["suppressions"]],
            ["$(touch /tmp/unsafe)", "sensitive-data", "$(touch /tmp/unsafe)"],
        )
        self.assertEqual(
            result["manifest"]["suppressions"][1]["detection_slug"], "credential/exposure"
        )
        self.assertFalse(unsafe_path.exists())

    def test_ignores_ordinary_comments(self):
        result = collector.build_manifest([comment("ordinary review comment")], CONTEXT)
        self.assertIsNone(result["manifest"])
        self.assertEqual(result["warnings"], [])

    def test_requires_the_exact_first_nonblank_directive_line(self):
        result = collector.build_manifest(
            [comment(" /gssa-ignore\ncheck: kvstore-state\nreason: Accepted.")], CONTEXT
        )
        self.assertIsNone(result["manifest"])
        self.assertEqual(result["warnings"], [])


class FetchCommentsTests(unittest.TestCase):
    def test_requests_every_page_with_required_headers(self):
        requests = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def urlopen(api_request):
            requests.append(api_request)
            count = 100 if len(requests) == 1 else 1
            return Response([{}] * count)

        with mock.patch.object(collector.request, "urlopen", side_effect=urlopen):
            comments = collector.fetch_comments("token", "splunk/example-ta", 123)

        self.assertEqual(len(comments), 101)
        self.assertTrue(requests[0].full_url.endswith("page=1"))
        self.assertTrue(requests[1].full_url.endswith("page=2"))
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer token")
        self.assertEqual(requests[0].get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(requests[0].get_header("X-github-api-version"), "2022-11-28")

    def test_rejects_non_array_and_http_errors(self):
        with mock.patch.object(
            collector.request,
            "urlopen",
            return_value=mock.MagicMock(
                __enter__=lambda response: response,
                __exit__=lambda *unused: False,
                getcode=lambda: 200,
                read=lambda: b'{}',
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "non-array JSON"):
                collector.fetch_comments("token", "splunk/example-ta", 123)

        http_error = collector.error.HTTPError("https://example.test", 403, "Forbidden", {}, io.BytesIO())
        with mock.patch.object(collector.request, "urlopen", side_effect=http_error):
            with self.assertRaisesRegex(RuntimeError, "returned 403"):
                collector.fetch_comments("token", "splunk/example-ta", 123)


class MainTests(unittest.TestCase):
    def test_writes_indented_private_manifest_only_when_entries_exist(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "nested" / "manifest.json"
            with mock.patch.dict(
                os.environ,
                {
                    "INPUT_TOKEN": "token",
                    "INPUT_REPOSITORY": "splunk/example-ta",
                    "INPUT_PULL_REQUEST_NUMBER": "123",
                    "INPUT_OUTPUT_PATH": str(output_path),
                },
                clear=False,
            ), mock.patch.object(
                collector,
                "fetch_comments",
                return_value=[comment("/gssa-ignore\ncheck: kvstore\nreason: Accepted.")],
            ):
                collector.main()

            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertTrue(output_path.read_text(encoding="utf-8").endswith("\n"))
            self.assertIn('\n  "schema_version": 1,', output_path.read_text(encoding="utf-8"))

    def test_removes_stale_manifest_when_no_valid_declarations_exist(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "manifest.json"
            output_path.write_text('{"stale": true}\n', encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {
                    "INPUT_TOKEN": "token",
                    "INPUT_REPOSITORY": "splunk/example-ta",
                    "INPUT_PULL_REQUEST_NUMBER": "123",
                    "INPUT_OUTPUT_PATH": str(output_path),
                },
                clear=False,
            ), mock.patch.object(collector, "fetch_comments", return_value=[]):
                collector.main()

            self.assertFalse(output_path.exists())

    def test_failed_serialization_preserves_existing_manifest(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "manifest.json"
            original_content = b'{"previous": "complete manifest"}\n'
            output_path.write_bytes(original_content)

            with mock.patch.object(collector.json, "dump", side_effect=OSError("write failed")):
                with self.assertRaisesRegex(OSError, "write failed"):
                    collector._write_manifest(output_path, {"replacement": "manifest"})

            self.assertEqual(output_path.read_bytes(), original_content)
            self.assertEqual(list(output_path.parent.glob(f".{output_path.name}.*")), [])


class WorkflowStructureTests(unittest.TestCase):
    def test_collector_action_is_checked_out_at_the_workflow_revision(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        collector_checkout = (
            "      - name: Checkout workflow actions\n"
            "        uses: actions/checkout@v7\n"
            "        with:\n"
            "          repository: splunk/addonfactory-workflow-addon-release\n"
            "          ref: ${{ github.workflow_sha }}\n"
            "          path: .workflow-actions\n"
            "          token: ${{ steps.app-token.outputs.token }}\n"
            "          persist-credentials: false\n"
        )
        collector_use = (
            "      - name: Collect PR-scoped GSSA suppressions\n"
            "        if: github.event_name == 'pull_request'\n"
            "        uses: ./.workflow-actions/.github/actions/collect-gssa-suppressions\n"
        )
        caller_checkout = "      - uses: actions/checkout@v7\n      - name: Configure AWS credentials\n"

        self.assertIn(collector_checkout, workflow)
        self.assertIn(collector_use, workflow)
        self.assertIn(caller_checkout, workflow)
        self.assertLess(workflow.index("id: app-token", workflow.index("run-gs-scorecard:")), workflow.index(collector_checkout))
        self.assertLess(workflow.index(collector_checkout), workflow.index(collector_use))
        self.assertLess(workflow.index(collector_use), workflow.index(caller_checkout))


if __name__ == "__main__":
    unittest.main()
