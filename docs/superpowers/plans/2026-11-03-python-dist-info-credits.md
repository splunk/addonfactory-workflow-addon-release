# Python dist-info credits implementation plan

> Execute inline with test-driven development; review the whole change before publication.

**Goal:** With an existing release artifact, Python report membership follows the exact name/version identities in `.dist-info/METADATA`.

**Architecture:** Keep the generic package-presence matcher for other ecosystems. Pass Python ecosystem identity from raw FOSSA evidence or reviewed rule metadata to the scope matcher; make Python metadata authoritative before applying legal-field completeness checks. Preserve decisions and paths in the JSON receipt. No artifact means existing behavior.

**Tech stack:** Existing Python scripts and stdlib-only artifact reader.

**Spec:** User decision in this chat: “for python packages ... whatever is in dist-info should stay in the report, if its not there, should be dropped from the report”. Packaged versions missing from reviewed inputs become review entries by the stated recommended default; unknown legal fields remain TBD.

## Global constraints

- Do not alter JavaScript/Go scope policy, legacy CSV behavior, raw FOSSA inputs, or private evidence.
- Read an existing SPL; no build inventory, scan, archive extraction or execution.
- Preserve all distinct Python versions represented by packaged metadata.
- Omission is non-blocking and precedes license/notice review for the omitted row.
- Normalize Python project names and allow a leading version `v`; do not borrow license facts between versions.
- Keep reviewed configuration readable, with explicit `ecosystem: pip` available for additions and Git-sourced Python distributions.

## Review focus

- Equal names from different ecosystems must not be confused.
- All platform/library directories must contribute metadata; no first-match shortcut.
- Public aliases must retain the source identity for matching.
- Stale/legacy exclusions must not override authoritative Python membership.
- Invalid metadata must fail input processing rather than silently omit real distributions.

## Task 1: Exact metadata membership

- [x] Add failing unit/CLI tests for absent versions, multiple installed versions, normalized names, Python/npm name collision, old exclusions and malformed metadata.
- [x] Implement Python classification and scope decisions in `scripts/fossa_artifact_scope.py`, `scripts/fossa_json_final.py` and `scripts/generate_credits.py`.
- [x] Add focused regression tests for the selected handling of metadata-only identities.
- [x] Update `runbooks/fossa-credits-json.md` and the bundled credits skill contract.
- [x] Run focused tests, full pytest with coverage, and all pre-commit hooks.

## Task 2: Real release verification

- [x] Regenerate to new local output directories using the recorded 8.1.2 and 8.2.1 artifacts and reviewed inputs.
- [x] Verify requests filtering, cffi multi-version membership, receipt/output consistency, unchanged inputs, and non-Python membership.
- [x] Review the whole diff and address required findings with regression tests.

Publication receipt: record the signed commit and local/remote verification in the consuming TA verification artifacts.


## Verification ledger

- Metadata filtering RED: eight new membership/receipt tests failed before implementation; then passed.
- Metadata-only discovery RED: missing output/review rows reproduced before implementation; then passed.
- Edge tests RED: ecosystem-only rules, metadata deduplication and invalid URL classification reproduced before fixes; then passed.
- Fresh reviewer found two correctness issues: cross-ecosystem resolution reuse and a Python/npm public-identity collision. Both were reproduced with failing tests, then fixed. A Python-specific alias resolves the collision without altering npm legal fields; unresolved collisions remain inspectable status-1 reports.
- Real 8.1.2 and 8.2.1 previews contain requests 2.32.5 only and both cffi versions. Receipt membership agrees with output; non-Python rows and source input hashes are unchanged.
- Public AWS reports are not overwritten with incomplete previews. Verification outputs live in the consuming TA artifacts directory.

Final local validation: 309 tests pass, 92.78% coverage; all six pre-commit hooks pass. No public AWS report was replaced with a TBD-bearing preview.
