#
# Copyright 2026 Splunk Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import check_template_compat as compat


CALLER = """jobs:
  reusable:
    uses: splunk/addonfactory-workflow-addon-release/.github/workflows/reusable-build-test-release.yml@v5
    secrets:
      REQUIRED_SECRET: ${{ secrets.REQUIRED_SECRET }}
    with:
      required-input: value
  unrelated:
    uses: example/other/.github/workflows/other.yml@v1
    with:
      ignored-input: value
"""


class TemplateCompatibilityTests(unittest.TestCase):
    def test_load_reusable_workflow_handles_yaml_boolean_on_key(self):
        raw = """on:
  workflow_call:
    inputs:
      required-input:
        required: true
      optional-input:
        required: false
    secrets:
      REQUIRED_SECRET: {}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow = Path(temp_dir) / "workflow.yml"
            workflow.write_text(raw, encoding="utf-8")

            declared_inputs, declared_secrets, required_inputs = (
                compat.load_reusable_workflow(workflow)
            )

        self.assertEqual(declared_inputs, {"required-input", "optional-input"})
        self.assertEqual(declared_secrets, {"REQUIRED_SECRET"})
        self.assertEqual(required_inputs, {"required-input"})

    def test_parse_caller_extracts_only_matching_workflow_contract(self):
        matched, passed_secrets, passed_inputs = compat.parse_caller(CALLER)

        self.assertTrue(matched)
        self.assertEqual(passed_secrets, {"REQUIRED_SECRET"})
        self.assertEqual(passed_inputs, {"required-input"})

    def test_parse_caller_accepts_inherited_secrets(self):
        raw = f"""jobs:
  reusable:
    uses: example/repo/{compat.REUSABLE_WORKFLOW_PATH}@v1
    secrets: inherit
"""

        matched, passed_secrets, passed_inputs = compat.parse_caller(raw)

        self.assertTrue(matched)
        self.assertEqual(passed_secrets, set())
        self.assertEqual(passed_inputs, set())

    def test_parse_sync_version_handles_quotes_and_missing_value(self):
        self.assertEqual(
            compat.parse_sync_version("REUSABLE_WF_VERSION='v5'\n"), "v5"
        )
        self.assertIsNone(compat.parse_sync_version("OTHER=value\n"))

    @mock.patch.object(compat, "gh_fetch_raw")
    def test_check_ref_passes_for_compatible_caller(self, fetch_raw):
        fetch_raw.side_effect = [CALLER, 'REUSABLE_WF_VERSION="v5"\n']

        with contextlib.redirect_stdout(io.StringIO()):
            errors = compat.check_ref(
                "splunk/template",
                "main",
                {"required-input"},
                {"REQUIRED_SECRET"},
                {"required-input"},
            )

        self.assertEqual(errors, [])

    @mock.patch.object(compat, "gh_fetch_raw")
    def test_check_ref_reports_undeclared_and_missing_contract_fields(self, fetch_raw):
        caller = f"""jobs:
  reusable:
    uses: example/repo/{compat.REUSABLE_WORKFLOW_PATH}@v1
    secrets:
      EXTRA_SECRET: value
    with:
      extra-input: value
"""
        fetch_raw.side_effect = [caller, "REUSABLE_WF_VERSION=v1\n"]

        with contextlib.redirect_stdout(io.StringIO()):
            errors = compat.check_ref(
                "splunk/template",
                "main",
                {"required-input"},
                {"REQUIRED_SECRET"},
                {"required-input"},
            )

        self.assertEqual(len(errors), 3)
        self.assertTrue(any("secret 'EXTRA_SECRET'" in error for error in errors))
        self.assertTrue(any("input 'extra-input'" in error for error in errors))
        self.assertTrue(any("requires input 'required-input'" in error for error in errors))

    @mock.patch.object(compat, "gh_fetch_raw")
    def test_check_ref_turns_caller_fetch_failure_into_finding(self, fetch_raw):
        fetch_raw.side_effect = compat.GhFetchError("authentication failed")

        errors = compat.check_ref(
            "splunk/template", "main", set(), set(), set()
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("could not fetch", errors[0])
        self.assertIn("authentication failed", errors[0])

    @mock.patch.object(compat, "gh_fetch_raw")
    def test_check_ref_warns_but_passes_when_sync_file_is_unavailable(self, fetch_raw):
        fetch_raw.side_effect = [CALLER, compat.GhFetchError("not found")]
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            errors = compat.check_ref(
                "splunk/template",
                "main",
                {"required-input"},
                {"REQUIRED_SECRET"},
                {"required-input"},
            )

        self.assertEqual(errors, [])
        self.assertIn("warning: could not fetch", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
