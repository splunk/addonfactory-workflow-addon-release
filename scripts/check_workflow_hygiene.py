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
"""Detect unused inputs and secrets in the reusable workflow.

Runs as an offline pre-commit hook. Every declared ``workflow_call`` input and
secret must be referenced by a GitHub expression in the workflow body.

Usage:
    python scripts/check_workflow_hygiene.py [path/to/workflow.yml ...]

Exits non-zero and prints every violation found (not just the first) when any
check fails.
"""
import re
import sys

import yaml

DEFAULT_TARGET = ".github/workflows/reusable-build-test-release.yml"
EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def load_workflow(path):
    with open(path, encoding="utf-8") as fh:
        # GitHub Actions treats workflow identifiers as strings. BaseLoader
        # keeps YAML 1.1 boolean-like keys such as on, off, yes, and no as
        # strings too.
        return yaml.load(fh, Loader=yaml.BaseLoader)


def get_workflow_call(data):
    on = data.get("on") or {}
    if not isinstance(on, dict):
        return {}
    return on.get("workflow_call") or {}


def expression_strings(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "if" and isinstance(nested, str):
                # GitHub permits ``if`` expressions without ${{ }} wrapping.
                yield nested
            else:
                yield from expression_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from expression_strings(nested)
    elif isinstance(value, str):
        yield from EXPRESSION_RE.findall(value)


def reference_pattern(context, name):
    escaped_name = re.escape(name)
    return re.compile(
        rf"(?:{context}\.{escaped_name}(?![A-Za-z0-9_-])|"
        rf"{context}\[\s*['\"]{escaped_name}['\"]\s*\])"
    )


def check_dead_inputs_and_secrets(data, errors):
    workflow_call = get_workflow_call(data)
    inputs = set(workflow_call.get("inputs") or {})
    secrets = set(workflow_call.get("secrets") or {})

    # Search expression values outside the declaration block. This excludes
    # YAML comments, inert prose, and the declarations themselves.
    body_data = {key: value for key, value in data.items() if key != "on"}
    body = "\n".join(expression_strings(body_data))

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
        data = load_workflow(target)
        errors = []
        check_dead_inputs_and_secrets(data, errors)
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
