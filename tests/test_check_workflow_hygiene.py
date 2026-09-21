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

import yaml

from scripts import check_workflow_hygiene as hygiene


class WorkflowHygieneTests(unittest.TestCase):
    def test_get_workflow_call_handles_yaml_boolean_on_key(self):
        data = yaml.safe_load(
            """
on:
  workflow_call:
    inputs:
      valid-input: {}
"""
        )

        self.assertIn(True, data)
        self.assertEqual(
            hygiene.get_workflow_call(data),
            {"inputs": {"valid-input": {}}},
        )

    def test_check_naming_reports_only_non_grandfathered_names(self):
        data = {
            True: {
                "workflow_call": {
                    "inputs": {
                        "valid-input": {},
                        "invalid_input": {},
                        "ui_marker": {},
                    }
                }
            },
            "jobs": {"valid-job": {}, "invalid_job": {}},
        }
        errors = []

        hygiene.check_naming(data, errors)

        self.assertEqual(len(errors), 2)
        self.assertTrue(any("input 'invalid_input'" in error for error in errors))
        self.assertTrue(any("job id 'invalid_job'" in error for error in errors))
        self.assertFalse(any("ui_marker" in error for error in errors))

    def test_dead_input_and_secret_detection_reports_all_unused_declarations(self):
        raw = """on:
  workflow_call:
    inputs:
      used-input: {}
      unused-input: {}
    secrets:
      USED_SECRET: {}
      UNUSED_SECRET: {}
jobs:
  validate:
    steps:
      - run: echo "${{ inputs.used-input }} ${{ secrets.USED_SECRET }}"
"""
        data = yaml.safe_load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, raw, errors)

        self.assertEqual(len(errors), 2)
        self.assertTrue(any("input 'unused-input'" in error for error in errors))
        self.assertTrue(any("secret 'UNUSED_SECRET'" in error for error in errors))

    def test_bracket_input_reference_counts_as_used(self):
        raw = """on:
  workflow_call:
    inputs:
      bracket-input: {}
jobs:
  validate:
    steps:
      - run: echo "${{ inputs['bracket-input'] }}"
"""
        data = yaml.safe_load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, raw, errors)

        self.assertEqual(errors, [])

    def test_main_returns_zero_for_clean_workflow(self):
        raw = """on:
  workflow_call:
    inputs:
      valid-input: {}
    secrets:
      VALID_SECRET: {}
jobs:
  validate:
    steps:
      - run: echo "${{ inputs.valid-input }} ${{ secrets.VALID_SECRET }}"
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow = Path(temp_dir) / "workflow.yml"
            workflow.write_text(raw, encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                result = hygiene.main(["check_workflow_hygiene.py", str(workflow)])

        self.assertEqual(result, 0)
        self.assertIn("workflow hygiene check passed", stdout.getvalue())

    def test_main_returns_one_and_prints_every_error(self):
        raw = """on:
  workflow_call:
    inputs:
      invalid_input: {}
jobs:
  invalid_job: {}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow = Path(temp_dir) / "workflow.yml"
            workflow.write_text(raw, encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                result = hygiene.main(["check_workflow_hygiene.py", str(workflow)])

        self.assertEqual(result, 1)
        self.assertIn("input 'invalid_input'", stdout.getvalue())
        self.assertIn("job id 'invalid_job'", stdout.getvalue())
        self.assertIn("3 issue(s) found", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
