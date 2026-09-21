# Reusable workflow development guide

This repository publishes the reusable GitHub Actions workflow used to build,
test, and release Splunk technology add-ons (TAs). Read the public workflow
contract in [`README.md`](README.md) before changing behavior.

## Guardrails

- Treat job IDs and `workflow_call` inputs and secrets as public API. Rename or
  remove them only with a consumer migration plan.
- Keep every declared input and secret wired into a GitHub expression in the
  workflow body.
- Use Python and POSIX shell for repository-owned automation. Do not introduce
  JavaScript or Node tooling.
- Use kebab-case for new job IDs and `workflow_call` input names.
- Pin third-party GitHub Actions to immutable commit SHAs and retain the
  readable release tag in a trailing comment.
- Never expose TA or template repository credentials to pull-request code.

## How a TA calls the workflow

The authoritative caller template is
[`addonfactory-repository-template/adjust/.github/workflows/build-test-release.yml`](https://github.com/splunk/addonfactory-repository-template/blob/develop/adjust/.github/workflows/build-test-release.yml).
A TA caller must:

1. Trigger on the TA events it needs, normally pushes to `main` and `develop`
   plus pull requests.
2. Call this repository's `.github/workflows/reusable-build-test-release.yml`
   as a job-level `uses` target.
3. Pass every required reusable-workflow secret and any TA-specific inputs.
4. Grant the caller permissions required by the called jobs. A reusable
   workflow cannot elevate permissions granted by its caller.

Released TA callers must use the released workflow tag selected by the
repository template. During development, use the exact commit SHA of the
workflow change for reproducible validation:

```yaml
jobs:
  call-workflow:
    uses: splunk/addonfactory-workflow-addon-release/.github/workflows/reusable-build-test-release.yml@<workflow-commit-sha>
    with:
      wfe-run-on-splunk-latest: "true"
    secrets:
      GH_APP_CLIENT_ID: ${{ secrets.GH_APP_CLIENT_ID }}
      GH_APP_PRIVATE_KEY: ${{ secrets.GH_APP_PRIVATE_KEY }}
      # Pass the remaining required secrets from the template caller.
```

## Branches and release handoff

- Start workflow feature and bug-fix branches from this repository's current
  `develop` branch. Pull requests normally target `develop`.
- `main` contains released workflow code. The `develop` to `main` release pull
  request is the release boundary.
- Coordinate changes to the workflow contract with a feature branch in
  `addonfactory-repository-template`.

Use this order for cross-repository changes:

1. Push the workflow feature branch and record its exact commit SHA.
2. Update the template feature branch's caller to match the new inputs and
   secrets and temporarily reference that exact SHA.
3. Validate the template caller through a real TA run as described below.
4. Merge and release the workflow, producing the final workflow tag.
5. Replace the temporary SHA in the template caller with the released tag and
   update `REUSABLE_WF_VERSION` in the template rollout tooling.
6. Repeat the TA validation against the released tag.
7. Merge and roll out the template only after the released-tag run passes.

Do not treat the temporary branch/SHA reference as a released compatibility
claim. The E2E run is the integration evidence.

## Local validation

Use an isolated worktree when the primary checkout is dirty. Download the
`actionlint` binary to `./actionlint` as CI does, then run:

```bash
poetry install --no-root
poetry run pytest
pre-commit run --all-files
```

`pytest` enforces at least 80% line coverage for repository-owned Python and
writes `coverage.xml`. Pre-commit runs YAML formatting, actionlint, Python
complexity, the workflow unused-input/secret check, and the tracked-file size
limit of 3 MiB.

## End-to-end TA validation

Use [`splunk/test-addonfactory-repo`](https://github.com/splunk/test-addonfactory-repo)
for the standard E2E path. It is a shared fixture. Before changing it, inspect:

- the caller reference currently on `main` and `develop`;
- open validation pull requests and active branches;
- recent workflow runs and known failures; and
- whether another engineer is using the fixture.

Do not assume its default branch is a clean released baseline. Coordinate any
change to shared branches and preserve the caller reference you will restore.

### Standard PR procedure

1. Create a signed fixture branch from the intended baseline.
2. Point `.github/workflows/build-test-release.yml` at the exact workflow SHA
   under test and align its inputs and secrets with the template feature
   branch.
3. Make the smallest TA change needed to trigger the affected path.
4. Open a pull request with a valid conventional title containing the real
   `ADDON-XXXXX` ticket.
5. For a targeted PR to `main`, add `use_labels` and the relevant
   `execute_*` label. Omit `use_labels` when validating global routing,
   matrices, dependencies, or pre-publish behavior.
6. Inspect the individual called-workflow jobs. An aggregate failure is not
   evidence that the changed path failed, and an aggregate success is not
   evidence that the expected matrix ran.

The standard fixture currently exercises unit, knowledge, modinput, and UI
tests. Use a representative TA that contains the relevant suite for UCC
modinput, scripted-input, upgrade, or SPL2 changes, or add that capability to
the fixture as an intentional separate change.

### Select scenarios by affected behavior

| Change affects | Required E2E scenario |
|---|---|
| One test job | Targeted TA pull request with its `execute_*` label |
| Shared setup, routing, matrices, or dependencies | Full pull request to fixture `main` |
| Push-only behavior | Push or merge into fixture `develop` |
| Beta publishing | `develop` push; verify prerelease tag and assets |
| Stable publishing, authentication, or release assets | Coordinated merge to fixture `main`; verify the signed release, tag, and assets |
| `release/*` behavior | Temporary release branch with `execute-tests-on-push-to-release` enabled |
| Scheduled behavior | Temporarily wire the default-branch caller because schedules use the default branch workflow |
| Custom-version publishing | Temporarily add `workflow_dispatch`, pass `custom-version`, and verify the requested release |

Only run destructive publishing scenarios when the change affects them. The
fixture `main` branch is protected and stable-release validation requires the
normal reviews and required checks.

### Evidence and cleanup

Record all of the following in the workflow pull request:

- workflow commit SHA or released tag;
- template caller branch and commit;
- TA repository, head SHA, event, base branch, and run URL;
- jobs and matrix entries expected to run or skip;
- relevant JUnit reports, logs, artifacts, tags, and release URLs; and
- clearly identified unrelated or pre-existing failures.

After validation, restore the fixture caller to its prior released reference,
remove temporary schedule/dispatch changes, and close or delete throwaway
branches and pull requests. Link the cleanup commit or pull request in the
validation evidence.
