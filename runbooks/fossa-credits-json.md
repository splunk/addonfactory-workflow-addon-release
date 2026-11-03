# Local FOSSA JSON credits

Run the local tools against a TA checkout. Generate the FOSSA draft unchanged,
then prefill matching dependencies from release-local reviewed rules before an
agent researches the remaining gaps. Generate final credits with those updated
rules. Draft retrieval calls FOSSA; the gap and final scripts run offline.
These commands do not start a scan, build or publication. Arrange any needed
fresh scan separately before fetching its attribution.

## Scan provenance and inventory decision

For a release-specific scan, start from the TA release tag, select the intended
released reusable workflow version, and record the TA tag, branch head, workflow
reference, run URL, scan job result and FOSSA response revision. Check the scan
job for the selected commit; an earlier successful run or an aggregate workflow
result is not that scan's result. Local draft retrieval does not replace a scan.

State the report's inventory policy before resolving licenses. A FOSSA-only
review retains every exact name/version identity in the direct and deep arrays,
without additions, exclusions or not-shipped scope decisions. A package-presence
review may apply explicit artifact-bound exclusions while retaining entries the
available checks cannot identify. Document the selected policy in the consuming
TA's release records. Neither policy turns a repository scan into proof of
physical inclusion. Use exact distribution evidence for exclusion decisions and
license/notice applicability; never silently change the selected inventory.

## Ownership of inputs and review records

This repository owns the processors, schemas, review skill, synthetic tests and
illustrative examples. It does not own a consuming TA's licensing decisions.

Keep real project settings, resolution configurations, approved-resolution
change records, raw reports, request metadata, generated credits, exact TA
revision references and validation receipts in that TA's repository/branch.
Pass those files to the reusable commands; do not commit them here as examples
or defaults. Example settings and resolutions are fictional and cannot be used
as approval evidence.

The shared pristine cache is a cross-project dependency-license research source.
Its own source revision and hashes describe the dataset, not a TA release or an
approval. Check every cache hit against the exact consuming artifact before
accepting it.

## Set up the TA and generate a draft

The user supplies a branch with a release directory such as
`docs/thirdpartycredits/drafts/1.2.3/`. Initially it contains only
`fossa-license-resolutions.json`, copied from the latest available reviewed
configuration. This release-local copy is the input and editable configuration
for the entire run. Preserve its existing rules. Do not select or update a
repository-root configuration implicitly.

Copy [the settings example](examples/fossa-json-settings.json) to the TA's
`.fossa-credits.json`. It contains exactly `schema_version: 1`,
`fossa_project_locator` (organization/project prefix, without a revision),
`ta_name` (safe filename identity), and `display_name` (report root name).
The checked-in examples use a fictional add-on. Replace their locator, name,
release version and paths with values from the consuming TA.
Keep secrets out of settings and reports.

```sh
python /path/to/addonfactory-workflow-addon-release/scripts/generate_draft_credits.py \
  --ta-repo /path/to/example-add-on \
  --ta-version 1.2.3 \
  --token-file /path/to/private/fossa-api-token \
  --output-dir /path/to/example-add-on/docs/thirdpartycredits/drafts/1.2.3
```

Alternatively, provide the token through `FOSSA_API_KEY`; never put its value
in command arguments or diagnostics. `--token-file` takes precedence. The
command defaults `--ta-repo` and `--output-dir` to the current directory.
It fetches direct/deep dependencies, copyrights, notices, file matches and
hash/version data, including unknown dependencies. Redirects cannot forward
authorization to another origin.

The checkout's `v1.2.3` tag selects an immutable commit. If unavailable, pass
`--revision <40-hex-commit>`; if both tag and override exist they must agree.
The selected commit's `package/default/app.conf` `[id] version` must match
`--ta-version`, and the FOSSA response revision must match that commit.
Dependency versions are independent of the TA release version. No TA revision
is pinned by the reusable tooling; select it from the consuming checkout.
A revision response does not establish a historical `revisionScanId`.

The command writes these sibling artifacts (here for the fictional Example TA 1.2.3):

| File | Shape and purpose |
|---|---|
| `Example_TA-1.2.3-fossa-attribution.json` | Unchanged response bytes with `project`, `directDependencies`, `deepDependencies` and native component evidence. |
| `Example_TA-1.2.3-fossa-attribution.request.json` | TA name/display name/version, project locator, selected/response revisions, `api_url`, `fetched_at`, request options, `raw_sha256`, direct/deep counts. |
| `Example_TA-1.2.3-credits-draft.json` | `name`, `version`, `disclaimer`, `components`, plus `provenance` binding the revision, locator, raw hash and sibling raw/metadata filenames. |

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
`TBD`. Apache-2.0 Sections 1–9 determine terms completeness. Application instructions
after the terms are reported separately for public-output cleanup. A reviewed `license_text` override with an
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
reviewed decisions only to the release directory's
`fossa-license-resolutions.json`. Preserve the raw response and request metadata.
The draft is a generated working file; scripts refresh it from raw evidence
and reviewed configuration rather than accepting manual legal-field edits.

### Prefill before agent research

Run the gap-resolution script immediately after draft generation:

```sh
python /path/to/addonfactory-workflow-addon-release/scripts/resolve_credits_gaps.py \
  --draft /path/to/example-add-on/docs/thirdpartycredits/drafts/1.2.3/Example_TA-1.2.3-credits-draft.json \
  > /path/to/example-add-on/docs/thirdpartycredits/drafts/1.2.3/review-queue.txt \
  2> /path/to/example-add-on/docs/thirdpartycredits/drafts/1.2.3/credits-diagnostics.txt
```

By default it reads `fossa-license-resolutions.json` beside the draft; a missing
or invalid file fails without changing the draft. `--config` remains available
for an explicit alternate path. There is no fallback to a repository-root file.
The script applies exact dependency name/version resolution overrides to the
working draft's license, full text, copyright and notices. It preserves all
FOSSA components, candidate lists and native evidence. Exclusions and additions
are evaluated by the shared queue/final logic rather than changing the draft's
FOSSA inventory. Rules for other versions are research leads, never overrides.

Standard output lists only the remaining review queue; standard error contains
generator diagnostics and a count. Exit `1` means prefill succeeded and agent
research remains; it is not a processing failure. The agent works this queue,
checks upstream and prior decisions, and edits the same release-local file.
After each batch, rerun this command to refresh the working draft and queue.
There are no additional resolution snapshots or approval files required.

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
shipped artifact and installed path. Resolutions and additions may set private `ecosystem: "pip"` for Python
classification during artifact reconciliation. An addition may use `TBD` for an
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
After documented inspection finds no main holder and no manual report
attribution was chosen, use `No copyright holder stated in the distributed
package.`. Legacy empty overrides remain readable but fail public validation. Omitted or unreviewed copyright stays `TBD` and blocks
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
  --draft /path/to/example-add-on/docs/thirdpartycredits/drafts/1.2.3/Example_TA-1.2.3-credits-draft.json \
  --output-dir /path/to/example-add-on/docs/thirdpartycredits/drafts/1.2.3 \
  2>/path/to/example-add-on/docs/thirdpartycredits/drafts/1.2.3/credits-diagnostics.txt
```

The final command verifies raw SHA-256, request metadata, revision and release
identity, then rebuilds the FOSSA-only baseline from the raw bytes and applies
the latest release-local configuration. It does not require the working draft
to equal the original FOSSA draft. Prefilled or manually changed draft fields
cannot leave stale values in the final credits when a rule is changed or removed.
Keep all three sibling files together. The command writes `Example_TA-1.2.3-credits.json`, retaining the four public
root fields and seven component fields: `name`, `version`, `license`,
`copyright_holder`, `license_text`, `project_url`, and `notices`. Draft
provenance, FOSSA evidence, `licenses_to_verify`, and
`licenses_text_to_verify` are removed. Included components preserve order.
Unmatched rules warn and exclusions report reasons. `--output-dir` defaults
to the current directory.

For a review that explicitly selects a FOSSA-only inventory, compare the raw
and final name/version multisets and require no missing, extra or duplicate
identities. Normalize case only for known display-name differences. A selected
package-presence policy instead records the reviewed exclusions and retains
unidentifiable entries. Generator status 0 covers the implemented checks, not
an unstated inventory policy. Keep the configuration and diagnostics with the
raw, draft and final outputs.

## Run another TA release with the latest reviewed rules

Each release directory owns its editable `fossa-license-resolutions.json`.
Seed a new release directory with the latest available reviewed file supplied
by the user, retaining historical exact dependency versions and unmatched rules.
The tools do not copy files between releases, fetch another branch's rules, or
modify earlier release directories. Record the committed configuration revision
with the final review evidence.

For a directory already seeded with that file, the local sequence is:

```sh
TA_REPO=/path/to/example-add-on
TOOLS=/path/to/addonfactory-workflow-addon-release
VERSION=1.2.3
OUT="$TA_REPO/docs/thirdpartycredits/drafts/$VERSION"
DRAFT="$OUT/Example_TA-$VERSION-credits-draft.json"
python3.13 "$TOOLS/scripts/generate_draft_credits.py" \
  --ta-repo "$TA_REPO" --ta-version "$VERSION" \
  --token-file /path/to/private/fossa-api-token --output-dir "$OUT"
python3.13 "$TOOLS/scripts/resolve_credits_gaps.py" --draft "$DRAFT" \
  >"$OUT/review-queue.txt" 2>"$OUT/credits-diagnostics.txt"
# Agent researches the queue and updates "$OUT/fossa-license-resolutions.json".
# Rerun gap resolution after each batch, then generate final credits:
python3.13 "$TOOLS/scripts/generate_credits.py" --draft "$DRAFT" \
  --output-dir "$OUT" 2>"$OUT/credits-diagnostics.txt"
```

The queue script also defaults to the same sibling configuration:
`list_review_queue.py --draft <release-draft.json>`. Both it and final generation
always apply the latest editable configuration to the raw baseline, even if
the working draft was last refreshed before an agent's configuration edit.

An exact dependency-version match reuses upstream facts such as license text
and copyright statements. Check each rule's distribution findings, supporting
notices and exclusions against the new TA release. The matcher does not enforce
that release-scope review automatically; final exit `0` is structural completion,
not cross-release legal approval. Unmatched historical rules warn and remain in
the configuration. An uncommitted configuration is suitable for iteration;
commit the reviewed configuration with the TA's release evidence for handoff.

## Exit codes and validation

| Command | Code | Meaning |
|---|---|---|
| Draft | `0` | Valid report parsed and three artifacts written; review may remain incomplete. |
| Gaps | `0` | Working draft updated; the current configuration has no unresolved generator diagnostics. |
| Gaps | `1` | Working draft updated; the printed queue needs agent review. |
| Final | `0` | All included components structurally complete. This does not certify legal analysis. |
| Final | `1` | Credits written; diagnostics list main/notice `TBD` (including explicit `notice_text: "TBD"`), unresolved notice applicability, unfilled operative copyright slots, unresolved/uncovered candidates or missing notice review. |
| Any | `2` | Invalid input/configuration, API or processing failure. Diagnose before interpreting outputs. |

Invalid data is validated before output replacement; successful outputs are
not replaced with invalid/partial data. Files are replaced individually,
not as a global filesystem transaction. Output paths cannot overwrite inputs.
Retain stderr and inspect the generated final report after each config edit.

For an offline exercise, call `write_draft_artifacts` from
`scripts.generate_draft_credits` with saved raw **bytes**, validated settings,
version, exact revision, an output directory, attribution URL and a UTC ISO
recording time. This consumes saved evidence without proving a new fetch;
record the saved source and exercise time honestly. Then run gap resolution
and the normal final CLI. Remaining fields and native notice reviews can still produce exit
`1`. Keep real scan outputs, reviewed resolutions and run-specific validation
records in the consuming TA repository. Do not alter legal decisions to force
exit `0`. Synthetic fixtures in `tests/test_fossa_json_final.py` and
`tests/test_resolve_credits_gaps.py` exercise the full local sequence,
complete/incomplete reviews, and invalid inputs. Routine verification uses mocked HTTP. The legacy CSV processor and
its runbook remain separate.


## Validate an existing release without generating output

```sh
python scripts/generate_credits.py validate \
  --draft /path/to/TA/docs/thirdpartycredits/drafts/1.2.3/Example_TA-1.2.3-credits-draft.json \
  --artifact /path/to/Example_TA-1.2.3.spl --json > validation.json
```

The generator reads the existing SPL in memory. It does not extract files,
execute code, rebuild the TA, run a scan or produce a build-time inventory.
It verifies the artifact application/release identity and computes its SHA-256.
The draft and matching raw/request artifacts remain unchanged. Without `--json`,
diagnostics go to stderr; with it, evidence also goes to stdout. The shell,
rather than the validator, writes the optional receipt in the example above.
`validate` rejects `--output-dir` and never writes credits or modifies inputs.

Use `generate` with the same inputs plus `--output-dir` to write credits. The
legacy invocation without a subcommand remains supported. Status 1 writes an
inspectable generation result; status 2 leaves previous output untouched.
Without `--artifact`, only structural/legal-field checks run and stderr explicitly
warns that artifact scope was not checked. Such a run does not establish scope.

Python distribution membership follows the complete set of packaged
`.dist-info/METADATA` name/version identities. A FOSSA Python row or reviewed
Python addition stays only when that exact identity is present; otherwise it is
omitted, with a non-blocking `not_shipped` decision whose `policy` is
`python-dist-info`. Decisions retain matching paths, other observed versions,
and the artifact checksum. This is the selected report-membership policy, not
a claim about vendored code without distribution metadata. Distinct versions
found anywhere in the artifact remain separate entries. Python project names
are compared case-insensitively with runs of `-`, `_` and `.` treated equally;
a leading `v` on a version is ignored for matching. Published input spellings
are preserved.

FOSSA's `source: pip` identifies Python rows. A resolution or addition can set
private `ecosystem: pip` explicitly, including Git-sourced Python distributions.
Legacy additions with a PyPI project URL are also recognized. Other explicit
FOSSA ecosystems are not reclassified by a coincidentally equal package name.
Missing or duplicate Name/Version headers in a dist-info METADATA file are
invalid input (status 2); they cannot silently cause report omissions.

Python distributions found in metadata but missing from input rows are added
once per normalized name/version. Exact-version resolution facts can populate
their legal fields; unknown fields stay `TBD`, making the result an incomplete
review report (status 1). The generator never borrows another version's license
or notices, or applies a resolution explicitly assigned to another ecosystem.
If a Python distribution has the same public name/version as a non-Python
entry, generation writes an incomplete review report (status 1). Resolve that
collision with a Python-specific `ecosystem: pip` resolution and a distinct
`public_name`; the other ecosystem's fields remain unchanged. Use an exact resolution or reviewed addition to complete the row.
Raw/request/working-draft inputs are not rewritten. Both generation and
validation receipts include these added rows. Metadata membership supersedes
old Python scope decisions and exclusions when an artifact is supplied.

For other ecosystems, the scope check still runs from report entries to the
existing package. Mapped package sources or packaged attribution identify a
package without requiring exact version or code provenance. Keep the input
version unchanged; there is no reverse-inventory check for these ecosystems.
Unidentifiable entries stay included as non-blocking `unverified` observations.
Only actionable stale/contradictory reviewed scope configuration produces
blocking `unknown`.

Never exclude Babel/compiler/type names by prefix. Reviewed decisions
may be stored in the release-local exact-version rule:

```json
{
  "name": "example-parser",
  "version": "1.0",
  "reason": "Reviewed output identity and scope",
  "source": "Exact upstream and release evidence",
  "scope": {
    "disposition": "not_shipped",
    "artifact_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "reason": "Verified build entry points and packaged files; fictional example",
    "references": ["Immutable release-specific review record"]
  }
}
```

The zero digest above is fictional. A real decision must match the exact SPL.
A stale decision becomes unknown. Contradictory package identity evidence
prevents an absence decision from hiding the component. Legacy exclusions remain
readable, but an exclusion without artifact binding cannot suppress a component
when `--artifact` is supplied. An exclusion may add `artifact_sha256` and
`references`, or scope can be kept with its existing resolution to preserve
previous legal-field research.

An exact resolution can also set `public_name` and `project_url` while its
`name`/`version` continue matching the original FOSSA identity. Public names may
not be Git transport locators; URLs must be HTTP(S), and aliases may not collide.
Private scope/reason/source fields do not enter public credits.

Public holders cannot be empty, even when an old empty override records completed
research. Preserve that research and use actual applicable attribution or the
reviewed statement `No copyright holder stated in the distributed package.`. Apply the same
rule to supporting notices. Apache body checks compare normalized Sections 1–9,
not character count or appendix presence. Application instructions are a separate
cleanup finding. Other license families currently receive identifier, field and
URL-only checks, not a claim of full text-equivalence verification. Multiple
versions cause disclosure warnings, not automatic deduplication or rejection.
