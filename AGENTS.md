# AGENTS.md

This repository publishes reusable GitHub Actions workflows for Splunk add-on repositories. Route to the narrowest source below before changing behavior.

## Routing: start here

- [`README.md`](README.md) — Start here for the workflow contract, inputs, secrets, job behavior, troubleshooting, and validation depth.
- [`.github/workflows/reusable-build-test-release.yml`](.github/workflows/reusable-build-test-release.yml) — Inspect the primary reusable build, test, and release workflow.
- [`.github/workflows/build-test-release.yaml`](.github/workflows/build-test-release.yaml) — Inspect this repository's own CI, unit-test, and release entrypoint.
- [`.github/workflows/reusable-publish-to-splunkbase.yml`](.github/workflows/reusable-publish-to-splunkbase.yml) — Use for Splunkbase publishing workflow changes.
- [`.github/workflows/reusable-validate-deploy-docs.yml`](.github/workflows/reusable-validate-deploy-docs.yml) — Use for deployment-document validation workflow changes.
- [`pyproject.toml`](pyproject.toml) — Use for Python compatibility and direct validation dependencies.
- [`poetry.lock`](poetry.lock) — Inspect the exact resolved Python dependency graph; regenerate it with Poetry after dependency changes.
- [`tests/`](tests/) — Run the offline unit tests for repository-owned Python validation logic.
- [`scripts/check_workflow_hygiene.py`](scripts/check_workflow_hygiene.py) — Use for workflow naming and unused input/secret enforcement.
- [`tests/test_check_workflow_hygiene.py`](tests/test_check_workflow_hygiene.py) — Update when workflow-hygiene behavior changes.
- [`scripts/check_template_compat.py`](scripts/check_template_compat.py) — Use for cross-repository caller-contract validation.
- [`tests/test_check_template_compat.py`](tests/test_check_template_compat.py) — Update when template-compatibility behavior changes.
- [`.github/template-compatibility.yml`](.github/template-compatibility.yml) — Use for the authoritative template refs covered by compatibility checks.
- [`.pre-commit-config.yaml`](.pre-commit-config.yaml) — Inspect local and CI-only validation hook definitions.
- [`renovate.json`](renovate.json) — Use for automated dependency and GitHub Action digest maintenance.
- [`runbooks/`](runbooks/) — Read the relevant operational procedure before recurring release or dependency-maintenance work.

## Guardrails

- Treat job IDs and `workflow_call` inputs and secrets as public API; rename them only with a consumer migration plan.
- Use kebab-case for new job IDs and `workflow_call` input names.
- Keep every declared `workflow_call` input and secret wired into the workflow body.
- Run `poetry run python -m unittest discover -s tests -v` plus the validation depth required by [`README.md`](README.md#validation-depth-by-change-class).
