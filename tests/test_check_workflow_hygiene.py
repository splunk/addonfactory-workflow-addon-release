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

from scripts import check_workflow_hygiene as hygiene


class WorkflowHygieneTests(unittest.TestCase):
    @staticmethod
    def load(raw):
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow = Path(temp_dir) / "workflow.yml"
            workflow.write_text(raw, encoding="utf-8")
            return hygiene.load_workflow(workflow)

    def test_load_workflow_preserves_boolean_like_identifiers(self):
        raw = """on:
  workflow_call:
    inputs:
      yes: {}
      off: {}
jobs:
  on: {}
  off: {}
"""
        data = self.load(raw)

        self.assertEqual(set(hygiene.get_workflow_call(data)["inputs"]), {"yes", "off"})
        self.assertEqual(set(data["jobs"]), {"on", "off"})

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
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

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
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

        self.assertEqual(errors, [])

    def test_bracket_secret_reference_counts_as_used(self):
        raw = """on:
  workflow_call:
    secrets:
      TOKEN: {}
jobs:
  validate:
    steps:
      - run: echo "${{ secrets['TOKEN'] }}"
"""
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

        self.assertEqual(errors, [])

    def test_hyphenated_input_does_not_mark_prefix_input_used(self):
        raw = """on:
  workflow_call:
    inputs:
      foo: {}
      foo-bar: {}
jobs:
  validate:
    steps:
      - run: echo "${{ inputs.foo-bar }}"
"""
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

        self.assertEqual(len(errors), 1)
        self.assertIn("input 'foo'", errors[0])

    def test_literal_reference_text_does_not_mark_declarations_used(self):
        raw = """on:
  workflow_call:
    inputs:
      unused-input: {}
    secrets:
      UNUSED_SECRET: {}
jobs:
  validate:
    description: "inputs.unused-input and secrets.UNUSED_SECRET"
    steps:
      - run: echo "ordinary text"
"""
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

        self.assertEqual(len(errors), 2)

    def test_wrapped_expression_fragments_mark_declarations_used(self):
        raw = """on:
  workflow_call:
    inputs:
      used-input: {}
    secrets:
      USED_SECRET: {}
jobs:
  validate:
    steps:
      - run: echo "prefix ${{ inputs.used-input }} ${{ secrets.USED_SECRET }} suffix"
"""
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

        self.assertEqual(errors, [])

    def test_bare_if_expression_marks_input_used(self):
        raw = """on:
  workflow_call:
    inputs:
      enabled: {}
jobs:
  validate:
    if: inputs.enabled == 'true'
    steps:
      - run: echo enabled
"""
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

        self.assertEqual(errors, [])

    def test_comment_only_reference_does_not_mark_input_used(self):
        raw = """on:
  workflow_call:
    inputs:
      unused-input: {}
jobs:
  validate:
    steps:
      # - run: echo "${{ inputs.unused-input }}"
      - run: echo "active step"
"""
        data = self.load(raw)
        errors = []

        hygiene.check_dead_inputs_and_secrets(data, errors)

        self.assertEqual(len(errors), 1)
        self.assertIn("input 'unused-input'", errors[0])

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
      unused-input: {}
    secrets:
      UNUSED_SECRET: {}
jobs:
  validate: {}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow = Path(temp_dir) / "workflow.yml"
            workflow.write_text(raw, encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                result = hygiene.main(["check_workflow_hygiene.py", str(workflow)])

        self.assertEqual(result, 1)
        self.assertIn("input 'unused-input'", stdout.getvalue())
        self.assertIn("secret 'UNUSED_SECRET'", stdout.getvalue())
        self.assertIn("2 issue(s) found", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
