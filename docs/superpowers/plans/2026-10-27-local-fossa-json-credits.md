# Local FOSSA JSON Credits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate auditable draft and final add-on credits locally from an existing FOSSA JSON attribution report, with reviewed exact-version decisions in the TA repository.

**Architecture:** A new JSON-only path has a downloader/CLI, a pure draft converter, a pure final resolver, and a final CLI. The CSV processor remains untouched. The draft carries FOSSA evidence and a binding to the saved raw response; the final command applies reviewed rules and strips draft-only evidence.

**Tech Stack:** Python 3.9–3.13 standard library, pytest 8.4.2, pytest-cov 7.1.0; local Git and the FOSSA attribution JSON V2 API.

**Spec:** `docs/superpowers/specs/2026-10-27-local-fossa-json-credits-design.md`

## Global Constraints

- Local scripts and skill only; no reusable workflow, CI, FOSSA scanning, release build, or publication changes.
- Leave `scripts/process_fossa_report.py` and its CSV tests unchanged.
- Never print/store a FOSSA token or forward it through redirects; authenticate only to `app.fossa.com`.
- Use the TA's `v{version}` tag or explicit 40-hex `--revision`; verify `[id] version` in `package/default/app.conf` at the selected commit and verify the FOSSA response revision.
- Preserve all direct and deep dependencies in report order, without automatic exclusions. Match reviewed rules by exact name and component version.
- Preserve complete original license and notice text. Unknown, conflicting, or unsupported judgments stay unresolved; do not use report-wide copyright lists as component attribution.
- The AWS report and 47-rule configuration are local validation inputs, not fixtures to commit. The protected primary checkouts must remain untouched.
- If implementation is later committed for a remote branch, commit only task-owned paths using `git commit -S --only ...` and verify each commit with `git verify-commit HEAD`; stop before push if signing fails. Existing staged CSV work must not be included accidentally.

## File Structure

| Path | Responsibility |
|---|---|
| `scripts/generate_draft_credits.py` | Local settings, Git release identity, authenticated FOSSA download, CLI, and three-artifact write. |
| `scripts/fossa_json_draft.py` | Pure native JSON validation, automatic decisions, and draft construction. |
| `scripts/fossa_json_final.py` | Draft/raw provenance, configuration validation, exact-version application, and completeness diagnostics. |
| `scripts/generate_credits.py` | Offline final CLI and output write. |
| `tests/test_fossa_json_draft.py` | Downloader, conversion, and draft CLI tests. |
| `tests/test_fossa_json_final.py` | Provenance, configuration, resolution, and final CLI tests. |
| `runbooks/fossa-credits-json.md`, `runbooks/examples/fossa-json-*.json`, and `.agents/skills/resolve-fossa-credits/` | Local usage, validated examples, and revised research instructions. |
| TA checkout `.fossa-credits.json` | Static project locator, TA filename identity, and display name. |

## Review Focus

1. A release tag and `--revision` disagree: reject before API access (Task 1 test).
2. An HTTP redirect or error risks revealing the token: reject without echoing auth/header/body and leave outputs unchanged (Tasks 1 and 3 tests).
3. The same component occurs in direct and deep sections: fail instead of merging or losing a row (Task 2 test).
4. A license name and attribution text lose their pairing or an ignored NOTICE affects copyright: preserve pairs and ignore the NOTICE (Task 2 tests).
5. A native unignored notice file has no reviewed `notices` decision: final output is written with exit `1`, even if main fields are complete (Task 5 test).

---

### Task 1: Local settings, release identity, and safe FOSSA fetch

**Files:** Create `scripts/generate_draft_credits.py`; create `tests/test_fossa_json_draft.py`.

**Interfaces:**
- `load_settings(path: Path) -> dict`: exact `schema_version: 1`, `fossa_project_locator`, `ta_name`, `display_name`; reject unknown/blank fields and invalid filename identity.
- `resolve_revision(repo: Path, version: str, override: Optional[str]) -> str`: return a 40-hex commit SHA; use `v{version}` when available, require an override to agree with an existing tag, verify committed `package/default/app.conf` `[id] version`.
- `fetch_attribution(project_locator: str, revision: str, token: str, opener=None) -> bytes`: GET `/api/v2/revisions/{encoded locator$revision}/attribution/json` with the six design-specified options and Bearer auth; reject redirects and non-200 responses, timeout after 120 seconds.

- [ ] **Step 1: Write failing tests** `test_settings_contract`, `test_tag_and_override_must_agree`, `test_revision_checks_committed_app_version`, and `test_fetch_options_and_redirect_redaction`. Assert AWS-style settings load, `v8.2.2` resolves to its SHA, mismatched tag/override or app version raises, the URL includes deep dependencies and notice files, and redirect/error diagnostics contain no token.
- [ ] **Step 2: Run** `poetry run pytest tests/test_fossa_json_draft.py -q`; expect failures because the module/functions do not exist.
- [ ] **Step 3: Implement the three interfaces** in `scripts/generate_draft_credits.py`. Use `subprocess.run([...], cwd=repo, check=True)` without a shell, `configparser` for committed app.conf, `urllib.parse.quote(locator + '$' + revision, safe='')`, and an opener that refuses redirects. Use `FOSSA_API_KEY` or `--token-file` only in Task 3's CLI. Do not add a runnable `main` yet.
- [ ] **Step 4: Run** `poetry run pytest tests/test_fossa_json_draft.py -q`; the four tests pass.
- [ ] **Step 5: Commit only task-owned files** with a signed commit and verify it, or leave them staged if signing is unavailable; never include the preexisting staged CSV files.

### Task 2: Pure native JSON-to-draft conversion

**Files:** Create `scripts/fossa_json_draft.py`; extend `tests/test_fossa_json_draft.py`.

**Interfaces:**
- `build_draft(report: dict, settings: dict, version: str, revision: str, raw_sha256: str, raw_filename: str, metadata_filename: str) -> dict`: validate the report and return the five-key draft root from the spec.
- `build_component(dependency: dict, kind: str) -> dict`: return the nine draft fields plus `fossa_evidence`; preserve native name/attribution pairs and full component notice files.
- Candidate objects use `licenses` and `otherLicenses`. `licenses_to_verify` contains name strings when the main license is `TBD`; `licenses_text_to_verify` contains `{name, text}` pairs when text is `TBD`. Otherwise both have empty arrays under both keys. Draft `notices` is always `[]`.

- [ ] **Step 1: Write failing tests** `test_requests_notice_sets_holder`, `test_lodash_and_arrow_keep_named_candidates`, `test_singleton_and_with_exception`, `test_conflicting_or_missing_attribution_text`, `test_ignored_notice_and_global_copyright_not_assigned`, and `test_duplicate_across_sections_is_invalid`. Assert Requests gets Apache-2.0 and `Copyright 2019 Kenneth Reitz`; lodash and Arrow remain `TBD` with every complete native candidate pair; `WITH` remains one identifier; duplicate identities and malformed rows raise.
- [ ] **Step 2: Run** the new tests via `poetry run pytest tests/test_fossa_json_draft.py -q`; expect failures for missing conversion interfaces.
- [ ] **Step 3: Implement** the two interfaces. Deduplicate license IDs after trimming/casefolding, but preserve FOSSA spelling and source group. Treat `AND`/`OR` as ambiguous, compare duplicate text blocks after newline normalization, and keep the first complete original block. For copyright, accept only one unambiguous component-local explicit statement after removing template placeholders; prefer the verbatim NOTICE line and ignore report-wide `copyrightsByLicense`.
- [ ] **Step 4: Run** `poetry run pytest tests/test_fossa_json_draft.py -q`; all draft conversion tests pass.
- [ ] **Step 5: Commit** only `scripts/fossa_json_draft.py` and the new test changes with `-S --only`; verify the commit.

### Task 3: Draft CLI and artifact provenance

**Files:** Complete `scripts/generate_draft_credits.py`; extend `tests/test_fossa_json_draft.py`.

**Interfaces:**
- `write_draft_artifacts(raw: bytes, settings: dict, version: str, revision: str, output_dir: Path, api_url: str, fetched_at: str) -> Tuple[Path, Path, Path]`: validate and write raw, request metadata, and draft; this pure-input path supports offline validation without another FOSSA request.
- `main(argv: Optional[List[str]] = None) -> int` accepts `--ta-repo` (default cwd), required `--ta-version`, optional `--revision`, `--token-file` or `FOSSA_API_KEY`, and `--output-dir` (default cwd). It fetches once, calls `write_draft_artifacts`, and returns `0`; invalid input, auth/API, parse, or write error returns `2` without a traceback or token disclosure. `TBD` in the draft does not fail this command.

- [ ] **Step 1: Write failing tests** `test_draft_cli_writes_three_named_artifacts`, `test_wrong_revision_and_bad_json_preserve_previous_artifacts`, and `test_missing_token_never_writes_artifacts`. Assert the metadata SHA-256 equals raw bytes, its TA identity/version and counts match the draft and arrays, draft provenance matches metadata, and the reported revision mismatch creates no new files.
- [ ] **Step 2: Run** `poetry run pytest tests/test_fossa_json_draft.py -q`; expect the new CLI tests to fail.
- [ ] **Step 3: Implement `write_draft_artifacts` and `main`** using Task 1's safe fetch and Task 2's conversion. Validate everything before touching final paths; write each artifact through a temporary file in `--output-dir` and replace atomically only after all content is ready. Metadata includes TA name/display/version, URL/options, selected/response revisions, UTC fetch time, SHA-256, and direct/deep counts. Never put auth in metadata or diagnostics.
- [ ] **Step 4: Run** `poetry run pytest tests/test_fossa_json_draft.py -q`; all downloader and draft CLI tests pass.
- [ ] **Step 5: Commit** only Task 3 changes with `-S --only`; verify the commit.

### Task 4: Offline final validation and reviewed-rule loading

**Files:** Create `scripts/fossa_json_final.py`; create `tests/test_fossa_json_final.py`.

**Interfaces:**
- `validate_draft(draft: dict) -> None`: require the five root fields, unique exact component identities, nine draft fields, `fossa_evidence`, and native candidate object shapes.
- `verify_provenance(draft: dict, draft_path: Path) -> None`: read only basename siblings named by `provenance`; reject path traversal, missing/mismatched raw SHA, and mismatch among raw `project.revision`, request metadata, and draft revision. Rebuild the expected draft from raw JSON and metadata using Task 2's `build_draft` and reject any changed draft field.
- `load_config(path: Path) -> dict`: accept current schema-version-1 rules and new rules with optional `evidence`; reject duplicate JSON keys/rules, unknown fields, blank overrides, invalid notices, and overlapping exclusions.
- Structured `evidence` requires `candidates` with `{source_group, name, disposition, reason, references}`, `copyright_sources` string array, and `distribution_findings` records with `{finding, references}`. Candidate groups and dispositions use the exact values in the design. Each reference must be a nonempty string. An unresolved disposition is valid input, handled by Task 5 as incomplete review.

- [ ] **Step 1: Write failing tests** `test_tampered_raw_metadata_or_draft_rejected`, `test_provenance_filename_traversal_rejected`, `test_legacy_aws_style_rule_accepted`, `test_structured_evidence_validation`, and `test_duplicate_and_overlapping_rules_rejected`. Assert an unchanged draft/raw/metadata trio validates, any altered hash, revision, or draft candidate/credit field raises, legacy notices and fallbacks remain valid, and malformed evidence is rejected.
- [ ] **Step 2: Run** `poetry run pytest tests/test_fossa_json_final.py -q`; expect missing-interface failures.
- [ ] **Step 3: Implement** the three interfaces with strict JSON object-pair loading, exact field/type validation, and no import from the CSV processor. Validate UTF-8 and report SHA on original raw bytes; reconstruct the draft using metadata's trusted TA name/display/version and compare JSON values. Treat `evidence` as optional so the existing 47 AWS rules remain usable.
- [ ] **Step 4: Run** `poetry run pytest tests/test_fossa_json_final.py -q`; all validation tests pass.
- [ ] **Step 5: Commit** only Task 4 files with `-S --only`; verify the commit.

### Task 5: Final credits, notice review gate, and CLI

**Files:** Complete `scripts/fossa_json_final.py`; create `scripts/generate_credits.py`; extend `tests/test_fossa_json_final.py`.

**Interfaces:**
- `build_credits(draft: dict, config: dict) -> Tuple[dict, List[str], int]`: return public credits without `provenance` or `fossa_evidence`, diagnostics, and status `0` or `1`.
- `main(argv: Optional[List[str]] = None) -> int`: require `--draft` and `--config`, accept `--output-dir` (default cwd), verify provenance first, then write `{ta_name}-{version}-credits.json` and print diagnostics. `ta_name` is taken from the draft raw-report filename prefix and cross-checked against the draft provenance filenames; do not derive it from the display name. Invalid inputs return `2` before replacing an existing final file.
- Exact-version rules override main fields independently. Candidate objects stay in the draft and do not appear in final credits; `notices` replaces all draft notices. Exclusions remove components before completeness checks and print reasons. Unmatched rules warn.
- A structured rule's candidate decisions are compared by `(source_group, casefold(name))` against the draft's full native candidates; missing/extra candidate decisions or disposition `unresolved` yield status `1`. An unignored FOSSA notice file requires a matching rule with an explicit `notices` field (possibly `[]`); absent review yields status `1`. Any public main/notice `TBD` also yields status `1`.

- [ ] **Step 1: Write failing tests** `test_partial_overrides_and_notices_preserved`, `test_unresolved_evidence_or_unreviewed_notice_exits_one`, `test_exclusions_and_unmatched_rules`, `test_final_cli_writes_on_one_and_zero`, and `test_invalid_provenance_exits_two_without_replacement`. Assert final components have exactly seven fields; unresolved output is written with named diagnostics; complete output exits `0`; malformed input leaves prior output intact.
- [ ] **Step 2: Run** `poetry run pytest tests/test_fossa_json_final.py -q`; expect the new tests to fail.
- [ ] **Step 3: Implement** the final engine and CLI. Use a temporary output file plus `os.replace`; preserve component ordering, full text, and the fixed disclaimer. For `--draft` in another directory, resolve only its sibling raw and metadata there. Report each unresolved component and field/notice-review/candidate reason on stderr.
- [ ] **Step 4: Run** `poetry run pytest tests/test_fossa_json_final.py -q`; all final tests pass.
- [ ] **Step 5: Commit** only Task 5 paths with `-S --only`; verify the commit.

### Task 6: Research skill, runbook, and real AWS validation

**Files:** Create `runbooks/fossa-credits-json.md`, `runbooks/examples/fossa-json-settings.json`, and `runbooks/examples/fossa-json-license-resolutions.json`; modify `.agents/skills/resolve-fossa-credits/SKILL.md`, `.agents/skills/resolve-fossa-credits/references/resolution-contract.md`, and `README.md`; create `.fossa-credits.json` in the isolated AWS TA checkout.

**Interfaces:** Document both local commands; raw, metadata, draft, config, and final file shapes; exit codes; provenance; explicit notice-review decision; structured evidence; and legacy rule support. Skill queue includes main/notice `TBD`, unresolved or uncovered candidates, and unreviewed notice files. It writes configuration only, then reruns the final command; the raw and draft files remain generated inputs.

- [ ] **Step 1: Write failing documentation-contract checks** in `tests/test_fossa_json_final.py` that load `runbooks/examples/fossa-json-settings.json` and `runbooks/examples/fossa-json-license-resolutions.json` through the new validators.
- [ ] **Step 2: Run** the focused tests; expect failure because the new examples do not yet exist.
- [ ] **Step 3: Write the two examples, runbook, and skill changes**, then place the four-field `.fossa-credits.json` from the spec in `/Users/mkolasin/Documents/Codex/2026-10-12/rev/work/aws-ta-8.2.2-license-resolutions`. Preserve the existing `fossa-license-resolutions.json` and all primary checkout changes.
- [ ] **Step 4: Run local validation**: call `write_draft_artifacts` with bytes from `/Users/mkolasin/Documents/Codex/2026-10-12/rev/outputs/Splunk_TA_aws-8.2.2-fossa-attribution.json` and AWS settings into a temporary output directory. Assert 165 components, Requests' copyright, lodash's two named candidates, Arrow's `otherLicenses`, and original direct/deep order. Run the final CLI with the 47 existing AWS rules; assert all 165 remain, inspect every unresolved field and notice-review finding, and expect exit `1` while review is incomplete. Do not change legal decisions just to make this check green.
- [ ] **Step 5: Run required repository checks**: `poetry run pytest`, `pre-commit run --all-files`, and `git diff --check`. Require at least 80% repository Python coverage and no unintended CSV behavior change; inspect `git status --short` before any commit.
- [ ] **Step 6: Commit only documentation, skill, and TA settings paths** in their respective isolated checkouts with signed local commits and verify each commit. Do not push as part of this plan; do not include raw FOSSA output, API token, or unrelated staged files.

## Self-review and acceptance

- Each design section maps to a task: identity/auth (1), draft evidence (2–3), audited resolution/provenance (4–5), local usage and AWS exercise (6).
- The JSON route is isolated from the staged CSV path. The draft retains nine component fields with native FOSSA candidate categories; the final output preserves the established root and emits seven component fields, omitting both verification fields.
- Acceptance is a local run that downloads or consumes the exact AWS revision, writes the three stage-one artifacts, applies the existing configuration, writes final credits and diagnostics, and reports incomplete review through exit `1`. A fully resolved synthetic fixture demonstrates exit `0`; invalid source/config demonstrates exit `2`.
