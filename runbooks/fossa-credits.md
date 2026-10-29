# FOSSA credits review

`scripts/process_fossa_report.py` converts a FOSSA Licensing CSV into reviewed
component JSON. It runs offline using Python 3.9–3.13 and the standard library.
The script and configuration contract are available for local review; the
reusable CI workflow does not invoke this check yet.

## Run the processor

Commit `fossa-license-resolutions.json` in the consuming add-on repository.
Start with [the empty configuration](examples/fossa-license-resolutions.json),
then add reviewed resolutions and exclusions as needed.

```sh
python scripts/process_fossa_report.py \
  --csv report.csv \
  --config fossa-license-resolutions.json \
  --ta-name Splunk_TA_aws \
  --ta-version 8.2.2 \
  --ta-display-name 'Splunk Add-on for Amazon Web Services' \
  --output-dir artifacts
```

The output is `artifacts/Splunk_TA_aws-8.2.2-credits.json`. Both identity
arguments are required: `--ta-version` identifies the add-on release, while
each component retains its own version. Their spelling is preserved; path
separators and control characters are rejected. The output directory defaults
to the current directory. The CSV and configuration cannot be overwritten by
the output.

`--ta-display-name` provides the human-readable report name and defaults to
`--ta-name` when omitted. It does not affect the generated filename.

| Exit code | Meaning |
|---|---|
| `0` | Every included component and supporting notice has resolved license, copyright, and license text; no required notice text is `TBD`. |
| `1` | A primary or supporting field is `TBD`; the JSON is still written for review. |
| `2` | Invalid inputs/configuration, file access failure, or processing error. |

Diagnostics go to stderr. They list unresolved components and their affected
fields, exclusions with reasons, unmatched rules, and the output path. An
invalid run does not produce a newly validated report; do not reuse an older
output file after exit `2`.

## CSV contract

Required columns are `Title`, `Version`, `Declared License`, and
`Discovered License(s)`. Optional metadata includes `Copyrights` and
`Package Homepage`. A missing optional value remains `TBD` for copyright or
`null` for the URL. Other columns are ignored except the text columns below.

The parser handles UTF-8 BOMs, quoted multiline values, section headings,
introductory “No ... found.” lines, and repeated headers. Direct and Deep
(Transitive) dependency sections are both included. Each section's header is
read independently: its columns can differ or appear in a different order.
Component order is preserved. Missing names/versions, incorrect row widths,
duplicate headers or name/version identities, and reports containing no
components are rejected. Duplicate identities also fail when packages belong
to different ecosystems; this contract cannot distinguish them safely.

### License and text selection

Semicolons separate detected licenses. Values are trimmed and deduplicated
ignoring case. A `Multi-license:` prefix is removed from candidate values;
SPDX aliases are not equated. Automatic selection succeeds only when one
source has a single known license and the other is empty, or both sources
contain the same single license. Declared spelling wins when both agree.
`AND`/`OR` expressions require review; a `WITH` exception remains part of its
license identifier. Missing/unknown values never satisfy completeness.

Read text from `Declared License Text` / `Discovered License Text`, or their
numbered forms such as `Declared License Text (1)`. FOSSA can split a long
block into 32,000-character columns. Nonempty chunks are concatenated in
numeric order **without added separators**, preserving their contents.
Missing intermediate chunks and duplicate chunk numbers are errors. An
unnumbered column is chunk 1 and cannot coexist with a nonempty numbered
chunk 1. License Header columns do not substitute for license text.

Text is selected automatically only when the license is unambiguous, each
text source identifies that license, and all available blocks agree after
normalizing line endings and surrounding whitespace for comparison. The
selected text itself retains its original contents. If the license is
ambiguous, text differs, a text source lacks a matching license, or text is
absent, `license_text` is `TBD`.

### Copyright selection

Preserve statements from `Copyrights`, including years. If supplied, `Notice
Text`, `Notice File Text`, `Notice Files`, or `Notice File(s)` columns (also
numbered) contribute statements beginning with `Copyright`, `(c)`, `©`, or
`Portions Copyright`. Conditions such as “The above copyright notice shall
be included” are not holder statements.
Distinct statements are joined by a newline. Generic placeholders such as
`<year>`, `<copyright holders>`, `[yyyy]`, and `[name of copyright owner]` do not
identify a holder. The processor does not infer holders from Authors,
license text, or License Header columns. When notice attribution wraps across
lines or needs interpretation, provide the reviewed complete statement in
configuration.

After reviewing the exact upstream artifact, a reviewer may supply
`Copyright holder not declared by upstream` if no holder is declared there.
Record the version and files checked in the rule's reason/source. Missing
FOSSA copyright data alone does not establish absence upstream; the offline
parser leaves such entries `TBD` until reviewed. Inaccessible or incomplete
upstream evidence also remains `TBD`.

## Output contract

The JSON root contains the add-on display name, release version, disclaimer,
and components array, in this order:

```json
{
  "name": "Splunk Add-on for Amazon Web Services",
  "version": "8.2.2",
  "disclaimer": "Third-party open source copyright and notices are provided for informational purposes only. Splunk makes no warranties, express or implied, regarding the accuracy or completeness of this information and disclaims liability for any errors or omissions.",
  "components": []
}
```

The disclaimer is emitted verbatim by the processor. Each component in
`components` has exactly nine fields:

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Component title. |
| `version` | string | Component version, preserving prefixes such as `v`. |
| `license` | string | Single selected/reviewed license or `TBD`. |
| `copyright_holder` | string | Copyright statement(s) or `TBD`. |
| `license_text` | string | Complete selected/reviewed text or `TBD`. |
| `licenses_to_verify` | object | `declared` and `discovered` arrays of candidate license strings. |
| `licenses_text_to_verify` | object | `declared` and `discovered` arrays of reassembled candidate text blocks. |
| `project_url` | string or null | Package Homepage; no URL is inferred. |
| `notices` | array | Reviewed supporting license/notice entries; defaults to `[]`. |

Resolved review fields contain `{"declared": [], "discovered": []}`. A missing
license or missing text also has empty candidates when no evidence was
reported. Text candidates preserve bundles from each source; the parser does
not claim it can map individual licenses to text fragments inside a bundle.

## Resolution configuration, schema version 1

The root requires exactly `schema_version`, `resolutions`, and `exclusions`.
Both rule collections are arrays. `schema_version` must be the integer `1`.

This is an illustrative configuration; its example text is not a real license:

```json
{
  "schema_version": 1,
  "resolutions": [
    {
      "name": "example-component",
      "version": "1.2.3",
      "license": "MIT",
      "copyright_holder": "Copyright 2026 Example Contributors",
      "license_text": "Complete reviewed license text",
      "reason": "Explanation of the reviewed decision",
      "source": "URL or repository reference supporting the decision"
    }
  ],
  "exclusions": [
    {
      "name": "example-build-dependency",
      "version": "2.0.0",
      "reason": "Confirmed absent from the distributed add-on"
    }
  ]
}
```

Resolutions require `name`, `version`, `reason`, `source`, and at least one of
`license`, `copyright_holder`, `license_text`, or `notices`. String overrides are nonempty
strings; `TBD`, unknown values, and placeholder copyright are rejected. The
license override must name a single license; text is supplied inline in JSON
with newline escapes as needed. Exclusions require exactly `name`, `version`,
and `reason`.

Matching uses exact component names and versions after trimming surrounding
whitespace. Wildcards and version ranges are unsupported. A new component
version needs its own reviewed decision. Unmatched historical rules produce
warnings and are never applied to another version. Duplicate JSON keys,
unknown fields, duplicate rules, and resolution/exclusion overlaps are errors.

A license override clears `licenses_to_verify`. A text override clears
`licenses_text_to_verify`. They act independently: choosing the main license
does not select text from an ambiguous bundle. Copyright-only decisions do
not change either candidate field. Excluded components are omitted before
completeness checks. Reviewers must confirm exclusions against the released
add-on; the processor never automatically excludes `@types/*`, `@babel/*`,
internal code, or any other category.

### Supporting licenses and notices

The processor does not infer supporting obligations from a list of detected
licenses. Review exact upstream files and the TA release artifact before
moving a candidate into `notices`. Selecting a main license preserves all
genuinely applicable supporting obligations. Multiple coequal licenses
covering the entire component need a reviewed decision rather than an
invented main/supporting distinction.

A resolution may supply `notices` alone or with primary overrides. This
replaces the entire notices array; preserve existing applicable notices when
updating the rule. Empty arrays explicitly record no supporting notices.
Configuration schema version 1 remains compatible with earlier configurations
that omit `notices`.

```json
{
  "name": "example-component",
  "version": "1.2.3",
  "notices": [
    {
      "name": "vendor/helper",
      "license": "BSD-3-Clause",
      "copyright_holder": "Copyright 2024 Helper Contributors",
      "license_text": "Complete supporting license text",
      "notice_text": "Verbatim upstream notice, when required",
      "source": "Exact upstream artifact/path and TA release build references",
      "reason": "These covered files ship inside the TA release artifact"
    }
  ],
  "source": "Exact dependency version and TA artifact references",
  "reason": "Supporting code is distributed under its own terms"
}
```

Each notice requires `name`, `license`, `copyright_holder`, `license_text`,
`source`, and `reason`; `notice_text` is an optional string. Notice names must
be unique within a component. Unknown fields, blank review fields, compound
license overrides, and template copyright holders are rejected. Full text
is preserved verbatim. Supporting review fields may be exactly `TBD` while
research continues; each blocks completeness, including `notice_text: "TBD"`
for a known required notice whose text is missing. Omit optional notice text
when no separate notice applies. The reviewed upstream copyright fallback is
also accepted within notices. Adding notices does not clear primary candidates.

Use the [resolve-fossa-credits skill](../.agents/skills/resolve-fossa-credits/SKILL.md)
to research entries, record exact-version reasons/sources, and regenerate
the report. It checks upstream license coverage and the TA distribution,
and leaves incomplete decisions unresolved.

## Manual review and later CI integration

1. Export a FOSSA Licensing CSV for the exact scanned release revision,
   including direct/transitive dependencies, declared/discovered licenses,
   full license text, copyright, homepage, and available notice text.
2. Run the processor with the add-on's committed configuration and release
   identity. Inspect the resulting credits JSON and stderr diagnostics.
3. Review unresolved data against the matching upstream artifact and the
   distributed add-on. Add exact-version overrides with reasons/sources, or
   exclusions with evidence recorded in the reason. Rerun until exit `0`.
4. When CI integration is implemented, make this exit status blocking and
   upload the generated credits JSON and diagnostics even for exit `1`.
   Configure artifact upload separately from the check so a failed check
   does not hide the information needed for review.

The eventual CI export settings must be checked against an actual CSV: HTML
report options do not guarantee that notice text appears as a CSV column.
If it is absent, copyright still needs a reviewed resolution. This command
does not update FOSSA data or legal conclusions and does not generate a final
publication layout.

## Validation

```sh
poetry install --no-root
poetry run pytest
pre-commit run --all-files
```

The tests exercise complete and unresolved runs, exact-version overrides,
exclusions, malformed inputs, candidate preservation, text chunk boundaries,
and output filenames. Real report exports remain local validation inputs;
tests use small synthetic fixtures instead of committing customer reports.
