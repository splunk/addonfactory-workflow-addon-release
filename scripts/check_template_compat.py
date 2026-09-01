#!/usr/bin/env python3
"""Cross-repo compatibility check against addonfactory-repository-template.

Runs as a CI-only pre-commit hook (stage: manual) — it needs network access
and a `gh` token with read access to the (private) template repo, so it must
not run on a local `git commit`.

For every ref listed in .github/template-compatibility.yml, this fetches the
template's caller workflow (adjust/.github/workflows/build-test-release.yml)
at that ref, extracts the secrets/inputs it passes to this reusable workflow,
and asserts:

  1. every secret the caller passes is declared in this workflow's
     on.workflow_call.secrets (an undeclared secret makes the caller's run
     fail at workflow-call time);
  2. every input the caller passes is declared in this workflow's
     on.workflow_call.inputs;
  3. every input this workflow marks `required: true` is actually passed by
     the caller.

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

import yaml

DEFAULT_WORKFLOW = ".github/workflows/reusable-build-test-release.yml"
COMPAT_FILE = ".github/template-compatibility.yml"
CALLER_PATH = "adjust/.github/workflows/build-test-release.yml"
SYNC_PATH = "tools/sync.sh"
REUSABLE_WORKFLOW_PATH = ".github/workflows/reusable-build-test-release.yml"


class GhFetchError(RuntimeError):
    pass


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
    declared_secrets = set(workflow_call.get("secrets") or {})
    required_inputs = {
        name for name, spec in declared_inputs.items() if (spec or {}).get("required")
    }
    return set(declared_inputs), declared_secrets, required_inputs


def parse_caller(raw_yaml):
    """Extract the secrets/inputs the template's caller passes to THIS reusable
    workflow, from any job whose `uses:` references reusable-build-test-release.yml."""
    data = yaml.safe_load(raw_yaml)
    passed_secrets = set()
    passed_inputs = set()
    matched_job = False

    for job in (data.get("jobs") or {}).values():
        uses = (job or {}).get("uses", "")
        if REUSABLE_WORKFLOW_PATH not in uses:
            continue
        matched_job = True
        secrets_block = job.get("secrets")
        if secrets_block and secrets_block != "inherit":
            passed_secrets.update(secrets_block.keys())
        with_block = job.get("with") or {}
        passed_inputs.update(with_block.keys())

    return matched_job, passed_secrets, passed_inputs


def parse_sync_version(raw_sh):
    for line in raw_sh.splitlines():
        line = line.strip()
        if line.startswith("REUSABLE_WF_VERSION="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def check_ref(repo, ref, declared_inputs, declared_secrets, required_inputs):
    errors = []

    try:
        caller_raw = gh_fetch_raw(repo, CALLER_PATH, ref)
    except GhFetchError as exc:
        return [f"[{ref}] could not fetch {CALLER_PATH}: {exc}"]

    matched_job, passed_secrets, passed_inputs = parse_caller(caller_raw)
    if not matched_job:
        return [
            f"[{ref}] no job in {CALLER_PATH} calls "
            f"{REUSABLE_WORKFLOW_PATH} — cannot verify compatibility"
        ]

    undeclared_secrets = passed_secrets - declared_secrets
    for name in sorted(undeclared_secrets):
        errors.append(
            f"[{ref}] caller passes secret '{name}' which this workflow "
            "does not declare in on.workflow_call.secrets — the caller's "
            "run will fail"
        )

    undeclared_inputs = passed_inputs - declared_inputs
    for name in sorted(undeclared_inputs):
        errors.append(
            f"[{ref}] caller passes input '{name}' which this workflow "
            "does not declare in on.workflow_call.inputs — the caller's "
            "run will fail"
        )

    missing_required = required_inputs - passed_inputs
    for name in sorted(missing_required):
        errors.append(
            f"[{ref}] this workflow requires input '{name}' but the "
            f"caller in {CALLER_PATH} does not pass it — the caller's "
            "run will fail"
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

    declared_inputs, declared_secrets, required_inputs = load_reusable_workflow(
        workflow_path
    )

    all_errors = []
    for ref in refs:
        all_errors.extend(
            check_ref(repo, ref, declared_inputs, declared_secrets, required_inputs)
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
