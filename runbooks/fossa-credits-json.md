# Local FOSSA JSON credits

Run two local commands from this repository against a TA checkout. The first
fetches the exact revision's FOSSA attribution JSON V2 and writes a reviewable
draft; the second applies TA-owned reviewed rules without network access.
These commands do not start a scan, build, or publication; arrange the fresh
scan separately before fetching its attribution.

## Fresh release scan and inventory decision

For a release-specific report, branch from the TA release tag, point its caller
at the intended released reusable workflow version, and trigger CI on that
branch. For an AWS TA 8.2.1 review, branch from `v8.2.1`, call
`reusable-build-test-release.yml@v5.6`, and use its fresh `fossa-scan` job.
Record the TA tag, branch head, workflow reference, run URL, scan job result,
and FOSSA response revision. Check the **scan job** for the selected commit;
an earlier successful run or an aggregate workflow result is not its result.
The local draft command below consumes the resulting FOSSA attribution; it
does not replace the CI scan.

State the report's inventory policy before resolving licenses. The AWS TA
8.2.1 review uses every exact name/version identity in FOSSA's direct and deep
dependency arrays as the public credits inventory, with no `additions` or
`exclusions`. This is an explicit report policy, not a claim that a repository
scan proves each component is physically present in the published SPL. Use
the published SPL, source maps, wheels, and binary build evidence to decide
license and notice applicability. If another review chooses a package-scoped
inventory, document that separately and apply its additions/exclusions only
with exact distribution evidence.

## Set up the TA and generate a draft

Copy [the settings example](examples/fossa-json-settings.json) to the TA's
`.fossa-credits.json`. It contains exactly `schema_version: 1`,
`fossa_project_locator` (organization/project prefix, without a revision),
`ta_name` (safe filename identity), and `display_name` (report root name).
The AWS example is for `custom+8617/splunk-add-on-for-amazon-web-services`.
Keep secrets out of settings and reports.

```sh
python /path/to/addonfactory-workflow-addon-release/scripts/generate_draft_credits.py \
  --ta-repo /path/to/splunk-add-on-for-amazon-web-services \
  --ta-version 8.2.2 \
  --token-file /path/to/private/fossa-api-token \
  --output-dir /path/to/artifacts
```

Alternatively, provide the token through `FOSSA_API_KEY`; never put its value
in command arguments or diagnostics. `--token-file` takes precedence. The
command defaults `--ta-repo` and `--output-dir` to the current directory.
It fetches direct/deep dependencies, copyrights, notices, file matches and
hash/version data, including unknown dependencies. Redirects cannot forward
authorization to another origin.

The checkout's `v8.2.2` tag selects an immutable commit. If unavailable, pass
`--revision <40-hex-commit>`; if both tag and override exist they must agree.
The selected commit's `package/default/app.conf` `[id] version` must match
`--ta-version`, and the FOSSA response revision must match that commit.
Dependency versions are independent of the TA release version. AWS 8.2.2's
selected revision is `03aa8ea33a87169beb2c97b1399effe280684fd0`.
A revision response does not establish a historical `revisionScanId`.

The command writes these sibling artifacts (here for AWS 8.2.2):

| File | Shape and purpose |
|---|---|
| `Splunk_TA_aws-8.2.2-fossa-attribution.json` | Unchanged response bytes with `project`, `directDependencies`, `deepDependencies` and native component evidence. |
| `Splunk_TA_aws-8.2.2-fossa-attribution.request.json` | TA name/display name/version, project locator, selected/response revisions, `api_url`, `fetched_at`, request options, `raw_sha256`, direct/deep counts. |
| `Splunk_TA_aws-8.2.2-credits-draft.json` | `name`, `version`, `disclaimer`, `components`, plus `provenance` binding the revision, locator, raw hash and sibling raw/metadata filenames. |

Each draft component retains `name`, `version`, `license`, `copyright_holder`,
`license_text`, `licenses_to_verify`, `licenses_text_to_verify`, `project_url`,
and `notices`. Its additional `fossa_evidence` retains direct/deep kind,
package/source locators, `downloadUrl`, dependency paths, all `licenses` and
`otherLicenses` name/attribution pairs and complete notice-file objects.
Candidate objects use these native categories; text entries carry `name` and
`text`. The JSON route never calls them `declared` or `discovered`.

A single distinct noncompound candidate may populate the main license.
Text must be complete, nonempty and agree across its attribution blocks.
If its operative copyright line still contains an unfilled holder slot,
the draft keeps that FOSSA text as a candidate and sets `license_text` to
`TBD`. The Apache-2.0 appendix's instructional placeholder is retained as
part of the canonical license. A reviewed `license_text` override with an
unfilled operative holder slot is rejected. This check applies even if a
separate copyright field has been resolved.
Component-local explicit copyright statements may populate the automatically
extracted draft holder; authors or report-wide maps cannot establish upstream
copyright. Missing or ambiguous draft fields remain `TBD`. Common license
boilerplate about copyright notices or liability is not a holder statement
and is rejected from reviewed copyright overrides. A later reviewed rule may
carry an explicit **manual report attribution**, provided its `reason`
identifies the decision and distinguishes it from upstream declarations.
Draft `notices` stays empty for review; source notice files stay in
`fossa_evidence`. Direct dependencies precede deep dependencies in source
order. No dependency is excluded by name or development label.

## Research and generate final credits

Use the repository's `resolve-fossa-credits` skill to inspect the draft,
raw evidence, exact upstream artifact and TA source/distribution. Write
reviewed decisions only to the TA's `fossa-license-resolutions.json`.
Preserve the raw, metadata and draft files as generated inputs.

The skill first queries its pinned snapshot of Henry Ou's
`license-credits-generator/data/pristine-cache.json` for the exact package
name and version:

```sh
python .agents/skills/resolve-fossa-credits/scripts/lookup_pristine_cache.py \
  --name certifi --version 2025.10.5
```

The snapshot source commit and SHA-256 hashes are recorded in
`.agents/skills/resolve-fossa-credits/data/pristine-cache-source.json`.
Exit `0` returns a cache entry, `1` means no exact entry, and `2` means a
damaged cache or invalid input. Use `--full` to inspect its complete license
text. A hit is a starting point for the reviewed rule, not an automatic
override: check the cache provenance and the exact upstream artifact,
especially when the cache copyright was derived from curated license text.
For a complete credits audit, compare every final component with the pinned
cache: exact version, nearby version of the same name, or no match. Compare
SPDX, legal text, and holder/notice coverage only where those cache fields
exist. Inspect both exact upstream versions to explain a nearby-version
difference; do not copy the nearby holder, and do not silently replace a
documented manual attribution. Record the cache commit and the disposition
of each material difference beside the report.
To update the snapshot after checking the generator's current `main` head,
clone it to an isolated local directory and run
`python .agents/skills/resolve-fossa-credits/scripts/refresh_pristine_cache.py
--checkout /path/to/license-credits-generator`. The refresh reads the committed
Git object, then writes deterministic compressed bytes and a manifest.

The [resolution example](examples/fossa-json-license-resolutions.json) is a
validator-checked shape example, **not publishable licensing research**.
Its example.org references are illustrative, and its secondary candidate
remains explicitly unresolved. Never copy it as a real legal decision.

Configuration contains `schema_version: 1`, `resolutions`, and `exclusions`,
plus optional `additions` for shipped components omitted by FOSSA. All three
arrays live in the same Git-tracked file. Rules match exact component
`name`/`version`. Resolution rules require nonempty `reason` and `source`
and override any of `license`,
`copyright_holder`, `license_text`, `notices`; unknown primary fields are
omitted, not overridden with `TBD`. Candidate lists stay in the draft and
are omitted from final credits. A notices override replaces the whole array.
Exclusions contain exactly name/version/reason and require evidence of absence
from the distributed TA. Additions contain all seven public component fields
(`name`, `version`, `license`, `copyright_holder`, `license_text`, `project_url`,
`notices`) plus `reason` and `source`. Their provenance must identify the exact
shipped artifact and installed path. An addition may use `TBD` for an
unreviewed primary field; this keeps the shipped component in the output and
the completeness check red. Additions appear after FOSSA components, in rule
order. An addition must not overlap another rule or a FOSSA component.

Reviewed exact-version rules override FOSSA values, including `TBD`. If the
configuration knows the same library under another version but has no rule
for the current scanned version, generation exits `1` with a version-review
diagnostic. Use the earlier sources and notice decisions to guide research
against the current exact upstream artifact and TA package. Add an exact-version
rule after checking the license, full text, copyright and embedded notices;
do not copy another version automatically. Preserve prior reviewed versions.

Legacy schema-version-1 rules remain accepted. New rules add structured
`evidence` with these fields:

- `candidates`: every native group/name pair, with `source_group`, `name`,
  `disposition` (`main`, `supporting`, `inapplicable`, or `unresolved`),
  `reason`, and nonempty immutable `references`.
- `copyright_sources`: exact upstream artifact/path references checked.
- `distribution_findings`: objects containing `finding` and nonempty
  `references` identifying TA revision/artifact paths.

Unsupported applicability, missing/stale candidate decisions, and unresolved
dispositions block completeness. Do not manufacture a main/supporting split.
Use an explicit `"copyright_holder": ""` override after documented inspection
of the exact upstream artifact finds no main holder and no manual report
attribution was chosen. Omitted or unreviewed copyright stays `TBD` and blocks
completeness. A manual attribution such as `Splunk Inc.` is allowed when the
exact-version rule records that deliberate choice, its scope, and the upstream
files checked; an npm `author` field alone is not a copyright declaration.
Do not replace a documented manual choice with a nearby-version cache holder.
Complete supporting terms and
attribution go in `notices`, even when their license matches the main license.
Each notice requires name/license/copyright_holder/license_text/source/reason;
optional `notice_text` holds the complete separate upstream notice.
For JSON rules, optional `applicability` separates an identified license from
the decision that its covered code ships: `confirmed` (the default for legacy
rules), `unresolved`, or `not_shipped`. Record the known license and full text
even when applicability is unresolved. An unresolved notice remains visible in
the generated incomplete credits and makes final generation exit `1` with a named
diagnostic. A `not_shipped` notice stays in the auditable configuration but is
omitted from public credits; use it only with exact TA distribution evidence
in `source` and `reason`. The final credits keep the legal notice fields and
omit `applicability`, `reason`, and `source`; those review decisions remain in
the resolution configuration. Never use `confirmed` solely to clear CI.

Every component with an unignored FOSSA notice file needs an explicit
exact-version `notices` rule, including `[]` when evidence establishes that
no supporting notice applies. Complete main fields do not bypass review.
Queue main/notice `TBD`, unresolved notice applicability, unresolved or
uncovered candidates and unreviewed notice files. Preserve unrelated
existing rules and all applicable notices.
Inspect the exact upstream artifact for every placeholder-bearing FOSSA
attribution. The structural check catches recognizable generic templates;
it cannot prove that otherwise plausible FOSSA text or copyright is correct.
Exact-artifact review remains necessary before approving the legal credits.

```sh
python /path/to/addonfactory-workflow-addon-release/scripts/generate_credits.py \
  --draft /path/to/artifacts/Splunk_TA_aws-8.2.2-credits-draft.json \
  --config /path/to/splunk-add-on-for-amazon-web-services/fossa-license-resolutions.json \
  --output-dir /path/to/artifacts \
  2>/path/to/artifacts/credits-diagnostics.txt
```

The final command verifies raw SHA-256, metadata and revision, then rebuilds
the expected draft from the authoritative raw bytes. Changed draft fields or
evidence are invalid input. Keep all three sibling files together. The
command writes `Splunk_TA_aws-8.2.2-credits.json`, retaining the four public
root fields and seven component fields: `name`, `version`, `license`,
`copyright_holder`, `license_text`, `project_url`, and `notices`. Draft
provenance, FOSSA evidence, `licenses_to_verify`, and
`licenses_text_to_verify` are removed. Included components preserve order.
Unmatched rules warn and exclusions report reasons. `--output-dir` defaults
to the current directory.

For a FOSSA-only inventory such as the AWS TA 8.2.1 report, compare the raw
direct/deep dependency name/version multiset with the final components and
require no missing, extra, or duplicate identities. Normalize case only for
known display-name differences. The generator checks structure and unresolved
fields; its exit `0` does not itself assert this inventory policy. Run the
`resolve-fossa-credits` review queue and require no current unresolved
components. Keep its diagnostics and the exact configuration snapshot with
the raw, draft, and final outputs.

## Run another TA release with the same reviewed rules

Keep one Git-tracked TA-owned `fossa-license-resolutions.json` with both
`resolutions` and `exclusions`. Neither generator writes it. Do not create
release-numbered configuration files or separate removal lists. Rules match
**exact dependency name and dependency version**. Rules absent from a report
produce warnings and are not removed. Add reviewed decisions to this same
file, then commit them. The Git commit identifies the configuration revision
used for a particular credits run.

Use a separate output directory for each TA release, but pass the tracked
configuration directly to the final generator:

```sh
TA_REPO=/path/to/splunk-add-on-for-amazon-web-services
TOOLS=/path/to/addonfactory-workflow-addon-release
VERSION=8.2.1
OUT=/path/to/artifacts/$VERSION
mkdir -p "$OUT"
python3.13 "$TOOLS/scripts/generate_draft_credits.py" \
  --ta-repo "$TA_REPO" --ta-version "$VERSION" \
  --token-file /path/to/private/fossa-api-token --output-dir "$OUT"
python3.13 "$TOOLS/scripts/generate_credits.py" \
  --draft "$OUT/Splunk_TA_aws-$VERSION-credits-draft.json" \
  --config "$TA_REPO/fossa-license-resolutions.json" \
  --output-dir "$OUT" 2>"$OUT/credits-diagnostics.txt"
```

Set `VERSION=8.1.2` and a distinct `OUT` and repeat. Exit `1` still writes
credits and diagnostics; resolve those findings in the editable configuration,
then regenerate and commit the reviewed file. Do not delete rules that are
unmatched in one release. Record the TA checkout's `git rev-parse HEAD` in the
review record after committing, so the configuration used by the run can be
retrieved from Git history. An uncommitted configuration is suitable for local
iteration but is not a reproducible final input.

An exact dependency-version match reuses upstream facts such as license text
and named copyright statements. It does **not** prove that the same vendored
files, notices, or exclusions apply to another TA release. Review each matched
rule's distribution evidence against that release's source and artifact before
accepting the credits. The current exact-name/version matcher does not enforce
this release-scope review automatically, so a final exit `0` alone is not
cross-release legal approval.

## Exit codes and validation

| Command | Code | Meaning |
|---|---|---|
| Draft | `0` | Valid report parsed and three artifacts written; review may remain incomplete. |
| Final | `0` | All included components structurally complete. This does not certify legal analysis. |
| Final | `1` | Credits written; diagnostics list main/notice `TBD` (including explicit `notice_text: "TBD"`), unresolved notice applicability, unfilled operative copyright slots, unresolved/uncovered candidates or missing notice review. |
| Either | `2` | Invalid input/configuration, API or processing failure. Diagnose before interpreting outputs. |

Invalid data is validated before output replacement; successful outputs are
not replaced with invalid/partial data. Files are replaced individually,
not as a global filesystem transaction. Output paths cannot overwrite inputs.
Retain stderr and inspect the generated final report after each config edit.

For an offline exercise, call `write_draft_artifacts` from
`scripts.generate_draft_credits` with saved raw **bytes**, validated settings,
version, exact revision, an output directory, attribution URL and a UTC ISO
recording time. This consumes saved evidence without proving a new fetch;
record the saved source and exercise time honestly. Then run the normal
final CLI. AWS's saved report has 165 components; the existing 47 rules are
compatible, but remaining fields and native notice reviews can still produce
exit `1`. Do not alter legal decisions to force exit `0`. Synthetic fixtures
in `tests/test_fossa_json_final.py` exercise complete, incomplete and invalid
cases. Routine verification uses mocked HTTP. The legacy CSV processor and
its runbook remain separate.
