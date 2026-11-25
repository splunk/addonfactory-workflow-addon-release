# FOSSA credits in the reusable workflow

## Enablement contract

A consuming TA enables credits by adding `.fossa-credits.json` with:

```json
{
  "schema_version": 1,
  "ta_name": "Splunk_TA_example",
  "display_name": "Splunk Add-on for Example",
  "docs_index": "docs/ReleaseNotes/index.md"
}
```

`ta_name` is the safe filename identity. `display_name` is used in the documentation link. `docs_index` is an optional repository-relative Markdown path; when omitted, the publisher checks the standard ReleaseNotes and ReleaseHistory index paths. `docs_section_heading` can override the default `Third-party software attributions` heading when a TA's index uses a different heading. The FOSSA project locator comes from the successful `fossa analyze --json` metadata, not from a branch-name lookup or a second scan.

The caller must pass `fossa-credits-tooling-sha` to the reusable workflow. It must be a reviewed, immutable 40-character commit SHA from `splunk/addonfactory-workflow-addon-release`. The tooling checkout is separate from the TA checkout and from the scan revision.

The GitHub App used by the publish job must be installed for the TA repository with `Contents: write` and `Pull requests: write` so it can create the release-docs PR. The workflow's `GITHUB_TOKEN` remains read-only for pull requests; the PR action uses the scoped App token.

Reviewed exact-version decisions belong in one TA-owned root file, `.fossa-license-resolutions.json`:

```json
{
  "schema_version": 1,
  "resolutions": [],
  "exclusions": []
}
```

An empty file is a valid starting point, but does not approve any component. CI produces a review queue and fails when the generated credits remain incomplete. Do not add legal decisions without upstream evidence and human review.

The CI contract currently accepts exact-version `resolutions` only. Nonempty `exclusions` or `additions` fail validation because applying them would require package-presence/inventory decisions, which this Jira excludes.

## CI behavior

For a pull request targeting `main`, the workflow uses the SHA checked out by the scan job, passes it explicitly to FOSSA, checks the returned revision, and retrieves attribution JSON for that exact locator/revision. The same-run revision must also match the checkout in the credits job. The job applies the TA's resolutions and uploads raw attribution, request metadata, draft, generated JSON, queue, and diagnostics. An unresolved report fails the credits check while retaining those artifacts. The artifact is named `fossa-credits-<run-id>-<run-attempt>` so reruns remain distinct.

For a release run on `main`, the workflow uses the build job's actual release version and the scan performed for that run. A complete result is prepared as `docs/thirdpartycredits/<ta-name>-<version>-credits.json`; the publisher updates the configured docs index and opens a bot PR. An identical rerun produces no file changes. A different file already present for the same TA/version is a hard conflict and is not overwritten.

The release docs update is a separate pull request because the release is already published when the final documentation artifact is prepared. This preserves a reviewable repository change and avoids a direct write to a protected branch.

## Local review

Download the `fossa-credits-<run-id>-<run-attempt>` artifact and keep the raw response, request metadata, and draft together. Copy them to a local work directory and review `credits-review-queue.txt` plus `credits-diagnostics.txt`. Edit `.fossa-license-resolutions.json` in the TA checkout, then regenerate from the downloaded draft and its sibling provenance files:

```sh
python3 scripts/generate_credits.py validate \
  --draft /path/to/artifacts/<ta>-<version>-credits-draft.json \
  --config .fossa-license-resolutions.json

python3 scripts/generate_credits.py generate \
  --draft /path/to/artifacts/<ta>-<version>-credits-draft.json \
  --config .fossa-license-resolutions.json \
  --output-dir /path/to/local-preview
```

The CI flow does not compare package inventories or use an agent to make licensing decisions. It applies reviewed TA configuration and reports unresolved items for a developer to investigate.

The public-format validator checks Apache-2.0 Sections 1–9 against its pinned terms. It does not reject the official application-instructions appendix merely because it appears in the complete upstream license text; the previous POC's blanket appendix rejection made a complete official license fail validation.
