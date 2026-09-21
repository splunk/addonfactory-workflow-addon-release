#!/usr/bin/env python3
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
"""Workflow hygiene check for the reusable build-test-release workflow.

Runs as a CI-only pre-commit hook (stage: manual). Offline — no network access
required. Enforces two agent-readiness Level 2 criteria that no off-the-shelf
tool covers for GitHub Actions workflow YAML:

  * naming_consistency  — job ids and workflow_call input names must be
    kebab-case, unless explicitly grandfathered.
  * dead_code_detection — every declared workflow_call input and secret must
    be referenced somewhere in the workflow body.

Usage:
    python scripts/check_workflow_hygiene.py [path/to/workflow.yml ...]

Exits non-zero and prints every violation found (not just the first) when any
check fails.
"""
import re
import sys

import yaml

DEFAULT_TARGET = ".github/workflows/reusable-build-test-release.yml"

KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Public-API names that predate this check. Renaming them is a breaking change
# for consumers (job ids referenced by branch-protection required checks, or
# workflow_call inputs referenced by callers) and is intentionally out of
# scope here. New names must not be added to this list without a deprecation
# plan.
GRANDFATHERED_INPUTS = {"ui_marker"}
GRANDFATHERED_JOBS = {"review_secrets", "UI-tests-report", "Modinput-tests-report"}


def load_workflow(path):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    # GitHub Actions treats workflow identifiers as strings. BaseLoader keeps
    # YAML 1.1 boolean-like keys such as on, off, yes, and no as strings too.
    data = yaml.load(raw, Loader=yaml.BaseLoader)
    return data, raw


def get_workflow_call(data):
    on = data.get("on") or data.get(True) or {}
    if not isinstance(on, dict):
        return {}
    return on.get("workflow_call") or {}


def check_naming(data, errors):
    workflow_call = get_workflow_call(data)

    for input_name in (workflow_call.get("inputs") or {}):
        if input_name in GRANDFATHERED_INPUTS:
            continue
        if not KEBAB_CASE_RE.match(input_name):
            errors.append(
                f"input '{input_name}' is not kebab-case "
                "(expected e.g. 'my-input'); add to GRANDFATHERED_INPUTS "
                "only if renaming would break a public API consumer"
            )

    for job_id in (data.get("jobs") or {}):
        if job_id in GRANDFATHERED_JOBS:
            continue
        if not KEBAB_CASE_RE.match(job_id):
            errors.append(
                f"job id '{job_id}' is not kebab-case "
                "(expected e.g. 'my-job')"
            )


def scalar_strings(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from scalar_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from scalar_strings(nested)
    elif isinstance(value, str):
        yield value


def reference_pattern(context, name):
    escaped_name = re.escape(name)
    return re.compile(
        rf"(?:{context}\.{escaped_name}(?![A-Za-z0-9_-])|"
        rf"{context}\[\s*['\"]{escaped_name}['\"]\s*\])"
    )


def check_dead_inputs_and_secrets(data, raw, errors):
    workflow_call = get_workflow_call(data)
    inputs = set(workflow_call.get("inputs") or {})
    secrets = set(workflow_call.get("secrets") or {})

    # Search parsed scalar values outside the declaration block. This excludes
    # YAML comments and prevents declarations from counting as references.
    del raw
    body_data = {
        key: value for key, value in data.items() if key not in {"on", True}
    }
    body = "\n".join(scalar_strings(body_data))

    for name in sorted(inputs):
        pattern = reference_pattern("inputs", name)
        if not pattern.search(body):
            errors.append(
                f"input '{name}' is declared but never referenced "
                f"(inputs.{name}) in the workflow body — remove it or wire it up"
            )

    for name in sorted(secrets):
        pattern = reference_pattern("secrets", name)
        if not pattern.search(body):
            errors.append(
                f"secret '{name}' is declared but never referenced "
                f"(secrets.{name}) in the workflow body — remove it or wire it up"
            )


def main(argv):
    targets = argv[1:] or [DEFAULT_TARGET]
    all_errors = []

    for target in targets:
        data, raw = load_workflow(target)
        errors = []
        check_naming(data, errors)
        check_dead_inputs_and_secrets(data, raw, errors)
        for err in errors:
            all_errors.append(f"{target}: {err}")

    if all_errors:
        print("workflow hygiene check failed:\n")
        for err in all_errors:
            print(f"  - {err}")
        print(f"\n{len(all_errors)} issue(s) found.")
        return 1

    print("workflow hygiene check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
