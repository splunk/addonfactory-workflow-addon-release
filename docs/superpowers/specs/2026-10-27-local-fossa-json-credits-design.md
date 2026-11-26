# Local FOSSA JSON credits design

## Intent

Generate auditable third-party credits for a Splunk add-on from an existing FOSSA revision scan. A local operator runs one command to download FOSSA JSON and create a draft, researches unresolved entries with `resolve-fossa-credits`, then runs a second command to produce final credits. The research configuration is the source of reviewed decisions; generated credits are never hand edited.

Success for AWS TA 8.2.2 means that the exact scanned commit `03aa8ea33a87169beb2c97b1399effe280684fd0` yields a reproducible draft covering all 165 FOSSA dependencies, and existing exact-version research decisions can be applied to produce a final credits file with explicit diagnostics for any remaining `TBD` fields. A complete report exits zero. Structural completion does not certify the legal analysis recorded in the configuration.

## Scope and constraints

- Local execution only. Do not add or alter reusable workflow jobs, CI, release scanning, or publishing.
- Implement the JSON route as separate scripts. Leave `scripts/process_fossa_report.py` and its CSV behavior untouched.
- Keep scripts and the research skill in this repository; run them against a local TA checkout. The TA owns its settings and `fossa-license-resolutions.json`.
- Use the existing FOSSA attribution JSON V2 endpoint. Fetch direct and deep dependencies, copyright lists, file matches, and notice files; include unknown dependencies. Do not send the token to an intermediary or print it. Do not follow a redirect carrying authorization to another origin.
- Preserve exact component name/version identities and report order. No package is excluded based on its name or development dependency label alone.
- The exact released TA artifact may still be inspected during research when the applicability of a supporting license depends on distributed files. The draft command does not build or scan an artifact.

## Approaches considered

1. Share a resolution engine with the CSV processor. This reuses validation but refactors code that already handles the existing 47 AWS decisions and could change CSV behavior.
2. **Chosen: separate JSON-only processor.** The new draft and final commands can model FOSSA's native JSON evidence without changing the CSV route. It duplicates a small set of exact-version validation and completeness rules; parity is established through shared fixtures and tests.

The draft can either mirror final credits and send the skill to a separate raw report, or carry source evidence beside each component. **Chosen: include per-component evidence in the draft.** The raw response remains the authoritative source; the draft organizes it for review.

## Inputs and local commands

The TA checkout contains `.fossa-credits.json` with `schema_version: 1`, `fossa_project_locator` (the project locator prefix, for AWS `custom+8617/splunk-add-on-for-amazon-web-services`), `ta_name` (for filenames), and `display_name` (for the report root). The static project locator is needed because `.fossa.yml` names the project but does not identify the FOSSA organization locator. The add-on version is supplied at runtime and is separate from dependency versions. The checkout's `v{ta-version}` Git tag resolves to a commit; `--revision` can select an explicit immutable commit when the tag is unavailable. When both are present, they must agree. Read `package/default/app.conf` at the selected commit using `git show` and require `[id] version` to equal `--ta-version`, including when `--revision` is used. Validate that the selected revision and the FOSSA response's `project.revision` agree. A revision-specific response is not proof of a particular historical `revisionScanId`; do not claim one.

```sh
python /path/to/addonfactory-workflow-addon-release/scripts/generate_draft_credits.py \
  --ta-repo /path/to/splunk-add-on-for-amazon-web-services \
  --ta-version 8.2.2 \
  --token-file /path/to/fossa-api-token \
  --output-dir artifacts

python /path/to/addonfactory-workflow-addon-release/scripts/generate_credits.py \
  --draft artifacts/Splunk_TA_aws-8.2.2-credits-draft.json \
  --config /path/to/splunk-add-on-for-amazon-web-services/fossa-license-resolutions.json \
  --output-dir artifacts
```

`--ta-repo` defaults to the current directory, `--output-dir` to the current directory, and authentication can instead come from `FOSSA_API_KEY`. Never store the token in settings, reports, metadata, shell command arguments, or diagnostics. Reject invalid filename identities and paths that would overwrite inputs. The draft command uses the settings file, release tag or `--revision`, and token; the final command reads the draft's identity and requires no network access.

The draft command writes three artifacts named from `ta_name` and release version: `*-fossa-attribution.json` (raw bytes), `*-fossa-attribution.request.json` (TA identity/version, locator, selected revision, response revision, fetch time, request options, SHA-256, counts), and `*-credits-draft.json`. The final command writes `*-credits.json`. Both commands write diagnostics to stderr; callers may redirect them to a file. On invalid input or API failure, do not replace an existing successful set of artifacts with partial output.

## Draft data and automatic decisions

The draft root is `name`, `version`, `disclaimer`, `components`, and draft-only `provenance`. `provenance` contains the FOSSA project locator, exact Git revision, raw report SHA-256, and sibling raw-report and request-metadata filenames. Each draft component retains nine fields: `name`, `version`, `license`, `copyright_holder`, `license_text`, `licenses_to_verify`, `licenses_text_to_verify`, `project_url`, and `notices`. It also has a draft-only `fossa_evidence` object with its direct/deep status, source/package locator, `downloadUrl`, dependency paths, all native `licenses` and `otherLicenses` name/attribution pairs, and complete component `noticeFiles` including contents, copyright data, corrected/ignored flags, and paths. The final generator removes `provenance`, `fossa_evidence`, `licenses_to_verify`, and `licenses_text_to_verify` from published credits. It also removes review-only `reason` and `source` from supporting notices; those stay in the resolution configuration for audit.

FOSSA's JSON `licenses` and `otherLicenses` are preserved under those names. They must not be silently relabeled `declared` and `discovered`. In the JSON route, the two candidate objects use `licenses` and `otherLicenses` arrays; text candidates retain the license name with each complete text. This changes the internal candidate-object shape for JSON-derived credits while retaining the top-level field names. The legacy CSV output keeps its existing shape.

Automatically select a main license only when the union of native candidate entries contains one distinct, noncompound license identifier (trimmed, case-insensitive comparison), whether it appears in either native array or both. Preserve the spelling from `licenses` when present. Treat `AND`/`OR` expressions as ambiguous; preserve `WITH` exceptions as one identifier. If there are no candidates or multiple distinct candidates, set `license: "TBD"`. Use its complete, nonempty component attribution as `license_text` only when the chosen license's available blocks agree after newline normalization. If missing, disagreeing, or attached to ambiguous candidates, set `license_text: "TBD"`. Never fabricate text from a standard header or from the report-wide license dictionary.

For `copyright_holder`, inspect only component-local unignored notice files and the component's selected license attribution for explicit, non-template copyright statements. Prefer verbatim lines in notice contents; use a notice's structured copyrights when there is no corresponding full line. Deduplicate identical statements while retaining order. If distinct statements appear to belong to different covered materials and cannot be safely assigned to the main component, leave `TBD` for review. Do not infer a holder from `authors`, project URL, or the report-wide `copyrightsByLicense` map. Keep available candidates in `fossa_evidence`. Empty notice contents or ignored notice matches never create automatic attributions.

The draft leaves `notices` empty; the raw notice files remain in `fossa_evidence` for review. The research skill decides whether a file is a required notice and whether a separate license applies to shipped code. Every component with an unignored FOSSA notice file requires an exact-version rule with an explicit `notices` array, including `[]` when reviewed evidence establishes that no supporting notice belongs in final credits. This prevents complete main fields from bypassing notice review. A draft may contain `TBD` and still exits zero when the FOSSA report was downloaded and parsed successfully; completeness is enforced at the final stage.

Reject malformed root/dependency shapes, missing names or versions, duplicate exact name/version identities, invalid license entries, and a response from the wrong revision. Preserve report order, including direct before deep dependencies.

## Research configuration and final generation

The `resolve-fossa-credits` skill reads the draft, raw report, exact upstream version, and TA source/distribution evidence. It writes exact-version rules in the TA's `fossa-license-resolutions.json`. Existing schema-version-1 rules with `reason` and `source` remain valid. New rules add a structured `evidence` object containing each reported candidate's native source group, license identifier, disposition (`main`, `supporting`, `inapplicable`, or `unresolved`), reason, and immutable source references. It also records sources for the selected copyright and any TA distribution finding. Keep `reason` and `source` as concise human summaries for compatibility. Supporting terms stay in `notices` with full text and attribution. An unresolved candidate or unsupported applicability conclusion remains `TBD`; the skill must not manufacture a main/supporting split. An explicit empty copyright requires documented inspection of the exact upstream artifact; an unreviewed copyright remains `TBD`.

The final command accepts legacy rules and structured rules, validates configuration and draft structure, and verifies the draft's raw report SHA-256 and revision against its named sibling raw report and request metadata before applying decisions. It reconstructs the expected draft from those raw bytes and metadata and rejects any changed draft component or evidence field; the raw response is authoritative. Missing or mismatched provenance is an invalid input. It applies exact-name/version overrides and exclusions and preserves unmodified component order. Override `license`, `copyright_holder`, and `license_text` independently. Candidate lists remain in the draft for review and are omitted from final credits; a notice override replaces the entire notice list. Warn about rules not found in the draft and report exclusions with reasons. Preserve applicable supporting notices even when their license is the same as the component's main license. The final output has the established root and seven component fields, without `provenance`, `fossa_evidence`, or candidate lists.

Return `0` for complete included components, `1` when a main or supporting notice field still has `TBD`, a structured candidate disposition is `unresolved`, or an unignored notice file has no explicit reviewed `notices` rule (after writing credits and listing fields), and `2` for invalid input/configuration or processing failure. Unsupported distribution applicability is recorded as an `unresolved` candidate disposition. Structured evidence must account for every candidate in the current draft; a missing or stale candidate decision is also unresolved and exits `1`. Completeness includes notice `license`, `copyright_holder`, `license_text`, and explicit `notice_text: "TBD"`. Legacy rules retain their existing field-based completeness check plus notice-review check. A successful exit means structurally complete only; research may still need human legal review.

## Verification and boundaries

Tests cover authentication errors and secret redaction, tag and explicit revision resolution, wrong-revision response, request options, direct/deep ordering, duplicate identities, singleton/conflicting/compound licenses, empty or conflicting text, ignored/corrected notice files, Requests' NOTICE copyright, global copyright non-attribution, explicit review of unignored notice files, exact-version overrides, structured evidence validation, legacy AWS rule compatibility, exclusion diagnostics, and all exit codes. Use mocked HTTP for routine tests. Run an offline integration against the saved AWS JSON to confirm 165 draft components, including Requests, lodash, and Arrow; then apply the existing 47 rules and inspect remaining `TBD` and notice-review findings without altering researched legal conclusions.

CI wiring, a fresh FOSSA scan, and release publication are outside this change. A later CI task can run these local commands and retain artifacts, but the present acceptance is successful local execution and reproducible artifacts.

## File examples

These are abbreviated **shape examples**. Placeholder text in angle brackets is not publishable licensing content; generated files retain full original text. The examples show one component for readability, while the AWS report contains 165.

The TA owns `.fossa-credits.json` before the first command runs:

```json
{
  "schema_version": 1,
  "fossa_project_locator": "custom+8617/splunk-add-on-for-amazon-web-services",
  "ta_name": "Splunk_TA_aws",
  "display_name": "Splunk Add-on for Amazon Web Services"
}
```

The draft command saves `Splunk_TA_aws-8.2.2-fossa-attribution.json` as **unchanged FOSSA response bytes**. This abbreviated view shows its native categories:

```json
{
  "project": {
    "name": "splunk-add-on-for-amazon-web-services",
    "revision": "03aa8ea33a87169beb2c97b1399effe280684fd0"
  },
  "directDependencies": [{
    "title": "requests",
    "package": "requests",
    "source": "pip",
    "version": "2.33.1",
    "licenses": [{"name": "Apache-2.0", "attribution": "<complete Apache license text>"}],
    "otherLicenses": [],
    "noticeFiles": [{
      "path": "NOTICE",
      "contents": "Requests\nCopyright 2019 Kenneth Reitz\n",
      "copyrights": ["2019 Kenneth Reitz"],
      "ignored": false,
      "corrected": false
    }]
  }],
  "deepDependencies": []
}
```

The companion `Splunk_TA_aws-8.2.2-fossa-attribution.request.json` identifies the request and binds the raw bytes:

```json
{
  "ta_name": "Splunk_TA_aws",
  "display_name": "Splunk Add-on for Amazon Web Services",
  "ta_version": "8.2.2",
  "fossa_project_locator": "custom+8617/splunk-add-on-for-amazon-web-services",
  "selected_revision": "03aa8ea33a87169beb2c97b1399effe280684fd0",
  "response_revision": "03aa8ea33a87169beb2c97b1399effe280684fd0",
  "fetched_at": "<UTC ISO-8601 timestamp>",
  "request_options": {
    "includeDeepDependencies": true,
    "includeCopyrightList": true,
    "includeNoticeFiles": true,
    "includeFileMatches": true,
    "includeHashAndVersionData": true,
    "excludeUnknownDependencies": false
  },
  "raw_sha256": "<64-character SHA-256 hex digest>",
  "direct_dependencies": 22,
  "deep_dependencies": 143
}
```

`Splunk_TA_aws-8.2.2-credits-draft.json` keeps FOSSA evidence beside each component. Requests is automatically populated from its single license and local NOTICE; components such as lodash and Arrow retain multiple candidates and `TBD` where judgment is required:

```json
{
  "name": "Splunk Add-on for Amazon Web Services",
  "version": "8.2.2",
  "disclaimer": "<fixed disclaimer from the existing credits contract>",
  "provenance": {
    "fossa_project_locator": "custom+8617/splunk-add-on-for-amazon-web-services",
    "revision": "03aa8ea33a87169beb2c97b1399effe280684fd0",
    "raw_sha256": "<same SHA-256 hex digest as request metadata>",
    "raw_report_file": "Splunk_TA_aws-8.2.2-fossa-attribution.json",
    "request_metadata_file": "Splunk_TA_aws-8.2.2-fossa-attribution.request.json"
  },
  "components": [{
    "name": "requests",
    "version": "2.33.1",
    "license": "Apache-2.0",
    "copyright_holder": "Copyright 2019 Kenneth Reitz",
    "license_text": "<complete Apache license text>",
    "licenses_to_verify": {"licenses": [], "otherLicenses": []},
    "licenses_text_to_verify": {"licenses": [], "otherLicenses": []},
    "project_url": "https://pypi.org/project/requests/",
    "notices": [],
    "fossa_evidence": {
      "dependency_kind": "direct",
      "package": "requests",
      "source": "pip",
      "downloadUrl": "<exact Requests 2.33.1 artifact URL>",
      "dependencyPaths": ["requests"],
      "licenses": [{"name": "Apache-2.0", "attribution": "<complete Apache license text>"}],
      "otherLicenses": [],
      "noticeFiles": [{
        "path": "NOTICE",
        "contents": "Requests\nCopyright 2019 Kenneth Reitz\n",
        "copyrights": ["2019 Kenneth Reitz"],
        "ignored": false,
        "corrected": false
      }]
    }
  }]
}
```

The research skill writes or updates the TA's `fossa-license-resolutions.json`. This illustrative rule shows the structured evidence added to the existing resolution fields; actual source references and license text must be reviewed before use:

```json
{
  "schema_version": 1,
  "resolutions": [{
    "name": "example-component",
    "version": "1.2.3",
    "license": "MIT",
    "copyright_holder": "",
    "license_text": "<complete reviewed MIT text>",
    "notices": [{
      "name": "vendor/helper",
      "license": "BSD-3-Clause",
      "copyright_holder": "Copyright 2024 Helper Contributors",
      "license_text": "<complete reviewed BSD license text>"
    }],
    "reason": "The exact artifact grants MIT for the main code; checked its notice and headers for a holder. A BSD helper ships in the TA.",
    "source": "<immutable artifact URL and file path>",
    "evidence": {
      "candidates": [{
        "source_group": "licenses",
        "name": "MIT",
        "disposition": "main",
        "reason": "The package LICENSE covers the included files.",
        "references": ["<immutable artifact URL and LICENSE path>"]
      }, {
        "source_group": "otherLicenses",
        "name": "BSD-3-Clause",
        "disposition": "supporting",
        "reason": "The vendor/helper files are distributed under BSD-3-Clause.",
        "references": ["<immutable artifact URL and vendor/helper/LICENSE path>"]
      }],
      "copyright_sources": ["<exact upstream path checked>"],
      "distribution_findings": [{
        "finding": "The covered files are included in the TA distribution.",
        "references": ["<TA revision and artifact path>"]
      }]
    }
  }],
  "exclusions": []
}
```

The final command writes `Splunk_TA_aws-8.2.2-credits.json` with the established public shape. It removes draft-only provenance and FOSSA evidence and applies reviewed notices when required:

```json
{
  "name": "Splunk Add-on for Amazon Web Services",
  "version": "8.2.2",
  "disclaimer": "<fixed disclaimer from the existing credits contract>",
  "components": [{
    "name": "example-component",
    "version": "1.2.3",
    "license": "MIT",
    "copyright_holder": "",
    "license_text": "<complete reviewed MIT text>",
    "project_url": "https://example.org/project",
    "notices": [{
      "name": "vendor/helper",
      "license": "BSD-3-Clause",
      "copyright_holder": "Copyright 2024 Helper Contributors",
      "license_text": "<complete reviewed BSD license text>",
      "source": "<exact upstream file and artifact references>",
      "reason": "The covered helper files ship in the TA."
    }]
  }]
}
```
