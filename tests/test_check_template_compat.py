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
    def run_main_with_files(self, workflow_raw, compat_raw, caller_raw):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow = root / "workflow.yml"
            config = root / "compat.yml"
            workflow.write_text(workflow_raw, encoding="utf-8")
            config.write_text(compat_raw, encoding="utf-8")
            stdout = io.StringIO()

            with (
                mock.patch.object(compat, "COMPAT_FILE", str(config)),
                mock.patch.object(compat, "gh_fetch_raw") as fetch_raw,
                contextlib.redirect_stdout(stdout),
            ):
                fetch_raw.side_effect = [
                    caller_raw,
                    'REUSABLE_WF_VERSION="v5"\n',
                ]
                result = compat.main(["check_template_compat.py", str(workflow)])

        return result, stdout.getvalue()

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

            (
                declared_inputs,
                declared_secrets,
                required_inputs,
                required_secrets,
                input_types,
            ) = compat.load_reusable_workflow(workflow)

        self.assertEqual(declared_inputs, {"required-input", "optional-input"})
        self.assertEqual(declared_secrets, {"REQUIRED_SECRET"})
        self.assertEqual(required_inputs, {"required-input"})
        self.assertEqual(required_secrets, set())
        self.assertEqual(input_types, {"required-input": None, "optional-input": None})

    def test_parse_caller_extracts_only_matching_workflow_contract(self):
        invocations = compat.parse_caller(CALLER)

        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0].job_id, "reusable")
        self.assertEqual(invocations[0].passed_secrets, {"REQUIRED_SECRET"})
        self.assertEqual(set(invocations[0].passed_inputs), {"required-input"})

    def test_parse_caller_accepts_inherited_secrets(self):
        raw = f"""jobs:
  reusable:
    uses: example/repo/{compat.REUSABLE_WORKFLOW_PATH}@v1
    secrets: inherit
"""

        invocations = compat.parse_caller(raw)

        self.assertEqual(len(invocations), 1)
        self.assertTrue(invocations[0].inherits_secrets)
        self.assertEqual(invocations[0].passed_secrets, set())
        self.assertEqual(invocations[0].passed_inputs, {})

    @mock.patch.object(compat, "gh_fetch_raw")
    def test_check_ref_validates_each_matching_job_independently(self, fetch_raw):
        caller = f"""jobs:
  complete:
    uses: example/repo/{compat.REUSABLE_WORKFLOW_PATH}@v1
    with:
      required-input: value
  incomplete:
    uses: example/repo/{compat.REUSABLE_WORKFLOW_PATH}@v1
"""
        fetch_raw.side_effect = [caller, "REUSABLE_WF_VERSION=v1\n"]

        with contextlib.redirect_stdout(io.StringIO()):
            errors = compat.check_ref(
                "splunk/template",
                "main",
                {"required-input"},
                set(),
                {"required-input"},
            )

        self.assertEqual(len(errors), 1)
        self.assertIn("job 'incomplete'", errors[0])
        self.assertIn("requires input 'required-input'", errors[0])

    def test_main_reports_missing_required_secret(self):
        workflow = """on:
  workflow_call:
    secrets:
      REQUIRED_SECRET:
        required: true
jobs: {}
"""
        caller = f"""jobs:
  reusable:
    uses: example/repo/{compat.REUSABLE_WORKFLOW_PATH}@v1
"""

        result, output = self.run_main_with_files(
            workflow,
            "template_repo: splunk/template\ncompatible_template_refs: [main]\n",
            caller,
        )

        self.assertEqual(result, 1)
        self.assertIn("requires secret 'REQUIRED_SECRET'", output)

    def test_main_reports_static_input_type_mismatch(self):
        workflow = """on:
  workflow_call:
    inputs:
      enabled:
        type: boolean
        required: true
jobs: {}
"""
        caller = f"""jobs:
  reusable:
    uses: example/repo/{compat.REUSABLE_WORKFLOW_PATH}@v1
    with:
      enabled: "true"
"""

        result, output = self.run_main_with_files(
            workflow,
            "template_repo: splunk/template\ncompatible_template_refs: [main]\n",
            caller,
        )

        self.assertEqual(result, 1)
        self.assertIn("input 'enabled' expects type 'boolean'", output)

    def test_main_rejects_empty_compatibility_ref_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow = root / "workflow.yml"
            config = root / "compat.yml"
            workflow.write_text("on:\n  workflow_call: {}\n", encoding="utf-8")
            config.write_text(
                "template_repo: splunk/template\ncompatible_template_refs: []\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with (
                mock.patch.object(compat, "COMPAT_FILE", str(config)),
                contextlib.redirect_stdout(stdout),
            ):
                result = compat.main(["check_template_compat.py", str(workflow)])

        self.assertEqual(result, 1)
        self.assertIn("at least one ref", stdout.getvalue())

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
