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
"""Cross-repo compatibility check against addonfactory-repository-template.

Runs as a manual-stage pre-commit hook. It needs network access and a `gh`
token with read access to the private template repo, so repository CI exposes
its GitHub App token only on trusted push refs, never to pull-request code.

For every ref listed in .github/template-compatibility.yml, this fetches the
template's caller workflow (adjust/.github/workflows/build-test-release.yml)
at that ref, extracts the secrets/inputs it passes to this reusable workflow,
and asserts:

  1. every secret the caller passes is declared in this workflow's
     on.workflow_call.secrets (an undeclared secret makes the caller's run
     fail at workflow-call time);
  2. every input the caller passes is declared in this workflow's
     on.workflow_call.inputs;
  3. every required input and explicit secret is passed by each caller job;
  4. statically typed caller values match declared input types when their type
     can be determined without evaluating a GitHub expression.

Also fetches tools/sync.sh and reports its REUSABLE_WF_VERSION for visibility
(advisory only — the authoritative compatibility set is the declared refs
list, not sync.sh).

Requires the `gh` CLI to be authenticated (GH_TOKEN/GITHUB_TOKEN) with read
access to the template repo.

Usage:
    python scripts/check_template_compat.py [path/to/reusable-workflow.yml]
"""
import subprocess
import sys
import urllib.parse
from typing import NamedTuple

import yaml

DEFAULT_WORKFLOW = ".github/workflows/reusable-build-test-release.yml"
COMPAT_FILE = ".github/template-compatibility.yml"
CALLER_PATH = "adjust/.github/workflows/build-test-release.yml"
SYNC_PATH = "tools/sync.sh"
REUSABLE_WORKFLOW_PATH = ".github/workflows/reusable-build-test-release.yml"


class GhFetchError(RuntimeError):
    pass


class CallerInvocation(NamedTuple):
    job_id: str
    inherits_secrets: bool
    passed_secrets: frozenset
    passed_inputs: dict


def gh_fetch_raw(repo, path, ref):
    """Fetch a file's raw text content from a (possibly private) repo via gh api."""
    query = urllib.parse.urlencode({"ref": ref})
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/contents/{path}?{query}",
                "-H",
                "Accept: application/vnd.github.raw",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise GhFetchError("`gh` CLI is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise GhFetchError(
            f"failed to fetch {path}@{ref} from {repo}: "
            f"{exc.stderr.strip() or exc.stdout.strip()}"
        ) from exc
    return result.stdout


def load_reusable_workflow(path):
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    on = data.get("on") or data.get(True) or {}
    workflow_call = on.get("workflow_call") or {}
    declared_inputs = workflow_call.get("inputs") or {}
    declared_secret_specs = workflow_call.get("secrets") or {}
    declared_secrets = set(declared_secret_specs)
    required_inputs = {
        name for name, spec in declared_inputs.items() if (spec or {}).get("required")
    }
    required_secrets = {
        name
        for name, spec in declared_secret_specs.items()
        if (spec or {}).get("required")
    }
    input_types = {
        name: (spec or {}).get("type") for name, spec in declared_inputs.items()
    }
    return (
        set(declared_inputs),
        declared_secrets,
        required_inputs,
        required_secrets,
        input_types,
    )


def parse_caller(raw_yaml):
    """Extract each template job that calls this reusable workflow."""
    data = yaml.safe_load(raw_yaml)
    invocations = []

    for job_id, job in (data.get("jobs") or {}).items():
        uses = (job or {}).get("uses", "")
        if REUSABLE_WORKFLOW_PATH not in uses:
            continue
        secrets_block = job.get("secrets")
        inherits_secrets = secrets_block == "inherit"
        passed_secrets = set()
        if secrets_block and not inherits_secrets:
            passed_secrets.update(secrets_block.keys())
        with_block = job.get("with") or {}
        invocations.append(
            CallerInvocation(
                job_id=str(job_id),
                inherits_secrets=inherits_secrets,
                passed_secrets=frozenset(passed_secrets),
                passed_inputs=dict(with_block),
            )
        )

    return invocations


def input_value_matches_type(value, expected_type):
    if not expected_type or (isinstance(value, str) and "${{" in value):
        return True
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "string":
        return isinstance(value, str)
    return True


def parse_sync_version(raw_sh):
    for line in raw_sh.splitlines():
        line = line.strip()
        if line.startswith("REUSABLE_WF_VERSION="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def check_invocation_secrets(
    prefix, invocation, declared_secrets, required_secrets
):
    errors = []
    undeclared = invocation.passed_secrets - declared_secrets
    for name in sorted(undeclared):
        errors.append(
            f"{prefix} passes secret '{name}' which this workflow "
            "does not declare in on.workflow_call.secrets — the caller's "
            "run will fail"
        )

    if not invocation.inherits_secrets:
        missing = required_secrets - invocation.passed_secrets
        for name in sorted(missing):
            errors.append(
                f"{prefix}: this workflow requires secret '{name}' but "
                "the caller does not pass it — the caller's run will fail"
            )
    return errors


def check_invocation_inputs(
    prefix, invocation, declared_inputs, required_inputs, input_types
):
    errors = []
    passed_inputs = set(invocation.passed_inputs)
    for name in sorted(passed_inputs - declared_inputs):
        errors.append(
            f"{prefix} passes input '{name}' which this workflow "
            "does not declare in on.workflow_call.inputs — the caller's "
            "run will fail"
        )

    for name in sorted(required_inputs - passed_inputs):
        errors.append(
            f"{prefix}: this workflow requires input '{name}' but the "
            "caller does not pass it — the caller's run will fail"
        )

    for name, value in invocation.passed_inputs.items():
        expected_type = input_types.get(name)
        if name in declared_inputs and not input_value_matches_type(
            value, expected_type
        ):
            errors.append(
                f"{prefix} input '{name}' expects type '{expected_type}' "
                f"but receives a static {type(value).__name__} value — "
                "the caller's run will fail"
            )
    return errors


def check_ref(
    repo,
    ref,
    declared_inputs,
    declared_secrets,
    required_inputs,
    required_secrets=None,
    input_types=None,
):
    errors = []
    required_secrets = required_secrets or set()
    input_types = input_types or {}

    try:
        caller_raw = gh_fetch_raw(repo, CALLER_PATH, ref)
    except GhFetchError as exc:
        return [f"[{ref}] could not fetch {CALLER_PATH}: {exc}"]

    invocations = parse_caller(caller_raw)
    if not invocations:
        return [
            f"[{ref}] no job in {CALLER_PATH} calls "
            f"{REUSABLE_WORKFLOW_PATH} — cannot verify compatibility"
        ]

    for invocation in invocations:
        prefix = f"[{ref}] caller job '{invocation.job_id}'"
        errors.extend(
            check_invocation_secrets(
                prefix, invocation, declared_secrets, required_secrets
            )
        )
        errors.extend(
            check_invocation_inputs(
                prefix,
                invocation,
                declared_inputs,
                required_inputs,
                input_types,
            )
        )

    try:
        sync_raw = gh_fetch_raw(repo, SYNC_PATH, ref)
        version = parse_sync_version(sync_raw)
        print(f"[{ref}] {SYNC_PATH} REUSABLE_WF_VERSION={version!r} (advisory)")
    except GhFetchError as exc:
        print(f"[{ref}] warning: could not fetch {SYNC_PATH} for visibility: {exc}")

    return errors


def main(argv):
    workflow_path = argv[1] if len(argv) > 1 else DEFAULT_WORKFLOW

    with open(COMPAT_FILE, encoding="utf-8") as fh:
        compat = yaml.safe_load(fh)
    repo = compat["template_repo"]
    refs = compat["compatible_template_refs"]

    if not refs:
        print(
            "template compatibility check failed:\n\n"
            "  - compatible_template_refs must contain at least one ref"
        )
        return 1

    (
        declared_inputs,
        declared_secrets,
        required_inputs,
        required_secrets,
        input_types,
    ) = load_reusable_workflow(workflow_path)

    all_errors = []
    for ref in refs:
        all_errors.extend(
            check_ref(
                repo,
                ref,
                declared_inputs,
                declared_secrets,
                required_inputs,
                required_secrets,
                input_types,
            )
        )

    if all_errors:
        print("\ntemplate compatibility check failed:\n")
        for err in all_errors:
            print(f"  - {err}")
        print(f"\n{len(all_errors)} issue(s) found across {len(refs)} declared ref(s).")
        return 1

    print(f"\ntemplate compatibility check passed for {len(refs)} declared ref(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
