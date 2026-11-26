# Resolution contract

## Files and identities

The reusable workflow repository owns `scripts/process_fossa_report.py` and
`runbooks/fossa-credits.md`. Each TA repository owns its reviewed configuration.
The JSON root has `name`, `version`, `disclaimer`, and `components`. The
filename uses the TA identifier and release version, not component versions.

For the CSV route, every component has `name`, `version`, `license`,
`copyright_holder`, `license_text`, `licenses_to_verify`,
`licenses_text_to_verify`, `project_url`, and `notices`. Both candidate objects
contain `declared` and `discovered` arrays. Text candidates are complete
reassembled source blocks; a block can contain several licenses and is not
necessarily one license per array item.

## Configuration

```json
{
  "schema_version": 1,
  "resolutions": [
    {
      "name": "example-component",
      "version": "1.2.3",
      "license": "Apache-2.0",
      "copyright_holder": "",
      "license_text": "Complete main license text from the exact artifact",
      "notices": [
        {
          "name": "vendor/helper",
          "license": "MIT",
          "copyright_holder": "Copyright 2024 Helper Contributors",
          "license_text": "Complete supporting license text from vendor/helper/LICENSE",
          "notice_text": "Verbatim upstream notice, if present",
          "source": "Immutable dependency artifact/path and TA build evidence references",
          "reason": "Covered vendor/helper files ship in the TA artifact; retain their MIT terms"
        }
      ],
      "reason": "Exact artifact LICENSE establishes Apache-2.0. Checked NOTICE and relevant headers: no main holder declared. MIT applies to shipped vendor/helper files. Other detected candidates cover unshipped examples; record exact paths and build proof here.",
      "source": "Exact dependency artifact checksum/URLs/paths and scanned TA commit/artifact references"
    }
  ],
  "exclusions": []
}
```

Example text is illustrative; replace it with full original license/notice
text. Use JSON escaping to preserve newlines. Source/reason are strings and
may contain several explicit references. JSON rules additionally support
structured `evidence` as specified below; legacy CSV rules keep this shape.

Match exact name/version; no wildcards or ranges. Resolution rules require
`name`, `version`, nonempty `reason`, nonempty `source`, and at least one of
`license`, `copyright_holder`, `license_text`, `notices`. Primary overrides
cannot be blank, unknown, or `TBD`, except that the JSON final processor
accepts exactly `"copyright_holder": ""` after documented review finds no
upstream holder declaration. Omit a copyright override that is still
unresolved; the draft `TBD` then blocks completeness. The legacy CSV
processor retains its separate validation contract.

The JSON `copyright_holder` field is the public report attribution. It can be
an exact upstream copyright statement or an explicitly chosen manual
attribution. When a manual value is used, `reason` must say it is a manual
decision, identify its scope and decision provenance, and state separately
whether the exact upstream artifact declares a holder. `source` identifies the
exact artifacts checked. An npm `author` field can inform that decision but
must not be presented as a copyright declaration. Preserve such a reviewed
exact-version rule unless its decision is explicitly revised; a nearby-version
cache entry cannot override it.

Exact-version reviewed overrides take precedence over FOSSA results, including
`TBD`. A rule for another version of the same library is a research lead, not
an override. For the JSON route, a scanned component with same-name rules but
no exact-version rule generates a blocking version-review diagnostic, even
when FOSSA supplied complete fields. Inspect the new version upstream and its
TA shipping evidence, then add an exact-version rule without deleting the old
research. Recheck supporting notices and exclusions as well as the main grant.

`notices` is an array replacing the component's entire reviewed notices
array. It defaults to `[]`; preserve existing applicable notices when editing.
Each notice requires `name`, `license`, `copyright_holder`, `license_text`,
`source`, `reason`. `notice_text` is optional, a verbatim string when supplied.
`source` and `reason` document the review in configuration; the final credits
omit them from each notice while retaining the legal text and attribution.
Names must be unique within the component; separate covered file groups with
different terms/attribution. Each license is a single identifier, including
any `WITH` exception. The JSON final processor also accepts an empty notice
copyright after complete upstream review. Supporting license/copyright/text
can be `TBD` while research continues; these values block completeness. Use `notice_text: "TBD"`
only for a known required notice whose full text has not yet been obtained;
it also blocks completeness. Omit it when no separate notice applies.

A license override clears license candidates, and a text override clears
text candidates. They act independently. Record rejected candidates in the
configuration's reason before regeneration clears them. The original CSV
remains the raw evidence. Notices do not implicitly resolve primary fields.

Exclusion rules require exactly `name`, `version`, and `reason`. Duplicate or
overlapping rules, unknown fields, and invalid values fail validation. Rules
for versions absent from this CSV warn without applying to other versions.

## Regeneration

```sh
python /path/to/addonfactory-workflow-addon-release/scripts/process_fossa_report.py \
  --csv /path/to/report.csv \
  --config /path/to/ta/fossa-license-resolutions.json \
  --ta-name Example_TA \
  --ta-version 1.2.3 \
  --ta-display-name 'Example Add-on' \
  --output-dir /path/to/artifacts
```

Preserve stderr to an agreed diagnostics file. Exit `0` means structurally
complete; `1` writes credits and lists unresolved paths; `2` means invalid
inputs/processing failure. A successful structural check does not independently
verify the research recorded in configuration.

## Local JSON route

The repository also owns `scripts/generate_draft_credits.py`,
`scripts/generate_credits.py`, and [the JSON runbook](../../../../runbooks/fossa-credits-json.md).
The TA owns four-field `.fossa-credits.json` settings. For the JSON flow, the
user seeds `docs/thirdpartycredits/drafts/<release>/fossa-license-resolutions.json`
with the latest available reviewed rules. This copy is the editable source of
review decisions for that release, with no implicit repository-root fallback.
Draft generation stays unchanged. Run `scripts/resolve_credits_gaps.py --draft
<release-draft.json>` before agent research to prefill exact dependency
name/version matches and print the remaining review queue. Rerun after edits.
The raw FOSSA response and request metadata remain unchanged; the working
draft is refreshed by the gap script. Final generation checks raw provenance,
rebuilds the FOSSA baseline and applies the latest configuration. It accepts
prefilled drafts without requiring equality to the original draft. Preserve
all three sibling artifacts. The final and queue commands default to the
same sibling configuration; explicit `--config` paths remain supported.

JSON candidate objects use native `licenses` and `otherLicenses` arrays,
never CSV's `declared`/`discovered`. Text candidates are objects with `name`
and complete `text`. JSON final credits omit draft-only `provenance`,
component `fossa_evidence`, and both candidate fields. Each final
component has exactly seven fields: `name`, `version`, `license`,
`copyright_holder`, `license_text`, `project_url`, and `notices`. The raw
response remains the authoritative evidence.

Existing schema-version-1 rules with `reason`/`source` remain valid. New JSON
rules add `evidence` containing exactly `candidates`, `copyright_sources`,
and `distribution_findings`. Every candidate needs `source_group`
(`licenses` or `otherLicenses`), exact `name`, `disposition` (`main`,
`supporting`, `inapplicable`, `unresolved`), nonempty `reason`, and nonempty
`references` array of immutable source strings. Include every native
group/name pair from the draft, even when the same name occurs in both
groups. Missing, stale, or unresolved decisions block completion.
`copyright_sources` is an array of exact upstream references. Each
`distribution_findings` entry has a nonempty `finding` and nonempty
`references` array identifying TA revision/artifact paths. Unsupported
applicability remains `unresolved`; never invent a main/supporting split.

Every component with an unignored FOSSA notice file requires an explicit
exact-version `notices` override. Use `[]` only after reviewing why no
supporting notice applies; omitting the field does not record review.
Preserve supporting obligations even if their license matches the main
license. Notices-only rules are valid. Legacy rules retain field completeness
checks and acquire this independent notice-review gate for JSON input.

JSON notice rules may also include `applicability`: `confirmed`, `unresolved`,
or `not_shipped`. An omitted value means `confirmed` for legacy compatibility;
write it explicitly in new reviewed rules. Preserve a known license identifier
and full text with `unresolved` when distribution or code provenance is still
unclear. The generator returns `1` and lists that notice despite the absence
of a `TBD` field. Use `not_shipped` only with exact TA distribution evidence;
the notice remains in configuration for audit but is omitted from public
credits. `applicability`, `reason`, and `source` are not public notice fields.

Run `python /path/to/repo/scripts/generate_credits.py --draft <draft.json>
--output-dir <release-dir>` (configuration defaults beside the draft).
Exit `0` means included components are structurally complete; `1` writes
credits and diagnostics for `TBD` main/notice fields, explicit
`notice_text: "TBD"`, unresolved/uncovered candidates, or unreviewed notice
files; `2` means invalid source/configuration or processing failure.
Structural completion does not certify the recorded legal research.

For the public JSON validation path, a legacy empty holder override remains
readable but produces a publication finding. After a completed absence review,
use `No copyright holder stated in the distributed package.` unless an explicit
manual report attribution already applies. Preserve the recorded manual
attribution decision and distinguish it from upstream declarations.
