#!/usr/bin/env python3
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
GRANDFATHERED_JOBS = set()


def load_workflow(path):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    # PyYAML parses the bare `on:` key as the boolean True. Reload with the
    # raw text available so we can still report readable line context, but
    # rely on the parsed structure (keyed by True) for `on.workflow_call`.
    data = yaml.safe_load(raw)
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


def check_dead_inputs_and_secrets(data, raw, errors):
    workflow_call = get_workflow_call(data)
    inputs = set(workflow_call.get("inputs") or {})
    secrets = set(workflow_call.get("secrets") or {})

    # Strip the `on:` block itself before searching for usages, so an input's
    # own declaration doesn't count as a "reference".
    body = raw
    on_match = re.search(r"^on:\n(?:[ \t].*\n|\n)*", raw, re.MULTILINE)
    if on_match:
        body = raw[: on_match.start()] + raw[on_match.end() :]

    for name in sorted(inputs):
        pattern = re.compile(r"inputs(?:\.|\[['\"])" + re.escape(name) + r"(?:['\"]\])?\b")
        if not pattern.search(body):
            errors.append(
                f"input '{name}' is declared but never referenced "
                f"(inputs.{name}) in the workflow body — remove it or wire it up"
            )

    for name in sorted(secrets):
        pattern = re.compile(r"secrets\." + re.escape(name) + r"\b")
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
