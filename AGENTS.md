# AGENTS.md

This repo publishes the reusable `build-test-release` GitHub Actions workflow
consumed by Splunk add-on repositories (via [addonfactory-repository-template](https://github.com/splunk/addonfactory-repository-template)).
There is no application runtime, database, or product surface here — the
"product" is CI/CD workflow YAML plus the scripts that guard its invariants.

## Where to look

- [`README.md`](README.md) — the spec: workflow inputs, secrets, and a
  per-job description (purpose, pass/fail behavior, artifacts,
  troubleshooting) for every job in
  `.github/workflows/reusable-build-test-release.yml`. Read this before
  changing any job's behavior or its inputs/secrets contract.
- [`runbooks/`](runbooks/) — operational procedures for recurring
  maintenance tasks (backporting to older TA versions, updating the
  AppInspect CLI action, rebuilding the Docker images this workflow depends
  on).
- [`scripts/`](scripts/) — invariant enforcement for the workflow YAML
  itself: `check_workflow_hygiene.py` (naming consistency, dead
  inputs/secrets) and `check_template_compat.py` (cross-repo compatibility
  against the template's caller workflow). Both run as CI-only pre-commit
  hooks (`stages: [manual]`) — see `.pre-commit-config.yaml`.

## Local validation

Run before pushing any change to `.github/workflows/reusable-build-test-release.yml`:

```bash
pre-commit run --all-files
pre-commit run --hook-stage manual --all-files
```

The first command runs formatting and lint (`actionlint`, `yamlfmt`). The
second additionally runs the CI-only hooks in `scripts/` (workflow hygiene,
template compatibility) that don't run on a local `git commit` because
`check_template_compat.py` needs network access and a `gh` token.

See the change-class → validation-depth table in
[README.md](README.md#validation-depth-by-change-class) for what depth of
validation a given change requires beyond this.

## Constraints

- This workflow is consumed by many independent add-on repos. Renaming a
  job id, input, or secret is a breaking change — `check_workflow_hygiene.py`
  grandfathers pre-existing public-API names for this reason.
- Job ids and new `workflow_call` input names must be kebab-case.
- Every declared `workflow_call` input and secret must be referenced in the
  workflow body, or the hygiene check fails.
