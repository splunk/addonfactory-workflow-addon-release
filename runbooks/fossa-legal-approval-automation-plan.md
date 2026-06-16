# FOSSA Legal Approval Automation Plan

## Purpose

Legal approval is required for supported add-on releases, including minor and patch releases, when FOSSA reports license or compliance findings. Today this depends on manual review and communication, which can delay releases and makes it difficult to track which dependency/license/version combinations already have a disposition.

This plan proposes automation in the `build-test-release` reusable workflow to detect FOSSA license obligations, prepare structured Legal review data, queue or send the Legal request, track Jira-linked disposition state, and expose approval status to release gating.

## Process Requirements

The documented FOSSA process establishes these rules:

- Do not release an add-on with unresolved FOSSA issues.
- A release can proceed only when the FOSSA scan is green, or every remaining FOSSA issue has an approved and linked disposition in Jira.
- Jira is the source of record.
- Slack and email are useful for coordination only.
- License or compliance findings go to Legal at `productlegal-jirareq@splunk.com`.
- Related license findings for the same add-on or release should be grouped into one request.
- Security or CVE findings should be fixed in the add-on. If a fix is not currently possible, a Security/ProdSec exception must be recorded with an owner and target remediation date.
- The release gate is clear only when all remaining findings have recorded outcomes in Jira.

## Current Workflow Context

The reusable workflow already has useful FOSSA stages:

- `fossa-scan` runs `fossa analyze`, captures the FOSSA report URL, and uploads the `THIRDPARTY` artifact.
- `fossa-license-test` queries active FOSSA licensing issues and uploads `fossa-license-issues.json`.
- `fossa-vulnerability-test` queries active FOSSA vulnerability issues and separately tracks critical, high, and medium release blockers.
- `pre-publish` currently blocks release readiness when active license issues or release-blocking vulnerability issues are present.

The automation should build on these jobs instead of replacing them.

## Proposed Workflow Changes

### 1. Add Structured FOSSA Legal Data

Extend the FOSSA license handling to generate structured artifacts:

- `fossa-license-issues.json`
- `fossa-license-legal-request.json`
- `fossa-license-legal-request.md`
- `fossa-license-email.txt`
- `fossa-disposition-status.json`

Each license/compliance finding should be normalized into a stable record:

```json
{
  "addon": "TA-example",
  "repository": "https://github.com/splunk/TA-example",
  "release_or_pr": "v1.2.3 or PR URL",
  "commit": "github sha",
  "fossa_scan_url": "https://app.fossa.com/...",
  "fossa_issue_url": "https://app.fossa.com/...",
  "bucket": "license",
  "dependency": "package",
  "version": "1.2.3",
  "license_or_policy": "GPL-2.0-only or denied policy",
  "jira_issue": "ADDON-1234",
  "disposition_status": "missing",
  "disposition_link": null
}
```

### 2. Add Jira Tracking Inputs

Add workflow inputs for the Jira source of record:

```yaml
jira-tracking-url:
  required: false
  type: string
  description: Jira issue or release epic URL used as the source of record for FOSSA dispositions.

jira-tracking-key:
  required: false
  type: string
  description: Jira issue key or release epic key used as the source of record for FOSSA dispositions.
```

For release-gated paths, the workflow should fail closed when active FOSSA issues exist and no Jira tracking issue is provided.

### 3. Add `fossa-legal-approval` Job

Add a new job after `fossa-license-test` and before `pre-publish`.

Responsibilities:

- Download the `fossa-license-issues` artifact.
- Generate grouped Legal request data for license/compliance findings.
- Check whether each finding has an approved Jira-linked disposition.
- Avoid duplicate Legal requests when Jira already records a pending or approved disposition.
- Upload Legal request and disposition artifacts.
- Publish approval state as workflow outputs.

Proposed outputs:

```yaml
status: approved|pending|not_required|blocked|failed
license-findings-count: number
missing-dispositions-count: number
pending-dispositions-count: number
approved-dispositions-count: number
jira-tracking-url: string
legal-request-artifact: string
```

Status meaning:

- `not_required`: no active license/compliance findings.
- `approved`: every active license/compliance finding has an approved Jira-linked disposition.
- `pending`: Legal request or Jira disposition exists but is not approved yet.
- `blocked`: one or more findings have no Jira tracking or no disposition.
- `failed`: automation could not evaluate the state safely.

### 4. Generate Legal Request

The generated request should follow the documented template:

```text
To: productlegal-jirareq@splunk.com
Subject: FOSSA license issue: <add-on> <release or branch>

Hello Legal team,

Please review the FOSSA license findings for <add-on>.

Add-on: <name>
Repository: <GitHub URL>
Release or PR: <release version, release epic, or PR URL>
FOSSA scan: <revision scan URL>
FOSSA issue list: <filtered issue-list URL>
Jira tracking: <ADDON issue or release epic URL>

Findings:
- <dependency>@<version>: <license or denied policy>, <FOSSA issue URL>

Requested outcome:
Please resolve these in FOSSA if they are false positives, approve the usage if acceptable, or confirm which dependency must be removed or replaced.
```

Phase 1 should prepare the request rather than silently sending email:

- Upload `fossa-license-email.txt` as a workflow artifact.
- Add the Legal request body to the job summary.
- Comment on the pull request with the generated request and Jira tracking link.
- Mark approval state as `pending` or `blocked` until Jira records approved dispositions.

Phase 2 can send the email automatically to `productlegal-jirareq@splunk.com` once the team agrees on credentials, audit expectations, and failure handling.

### 5. Track Duplicate Requests Through Jira

Because Jira is the source of record, duplicate avoidance should be based on Jira disposition data rather than Slack/email history.

Rules:

- If the same dependency/version/license/FOSSA issue is already linked in the Jira tracking issue with an approved Legal disposition, do not create a new request item.
- If Jira has a pending Legal request link for the same finding, do not resend the request.
- If the dependency version changes, treat it as a new review item.
- If the license or denied policy changes, treat it as a new review item.
- If the FOSSA issue changes materially, treat it as a new review item.

The stable key should include:

```text
addon repository | dependency name | dependency version | license or policy | FOSSA issue id
```

### 6. Keep Security Findings Separate

Security/CVE findings should not be sent to Legal.

For active vulnerability findings:

- Engineering should upgrade, remove, or constrain the affected dependency.
- If a fix is not currently possible, a Security/ProdSec exception must be linked in Jira.
- Any exception must include an owner and target remediation date.

`fossa-vulnerability-test` should continue to produce vulnerability artifacts. A later enhancement can add Jira disposition checking for Security/ProdSec exceptions in the same style as the Legal approval job.

### 7. Update `pre-publish` Release Gate

Add `fossa-legal-approval` to `pre-publish.needs`.

Update release gating so the release can proceed only when:

- FOSSA has no active issues, or
- every active license/compliance finding has an approved Jira-linked Legal disposition, and
- every active security finding is fixed or has an approved Jira-linked Security/ProdSec exception.

The gate should fail closed when:

- Jira tracking URL/key is missing for active FOSSA issues.
- Legal approval status is `pending`, `blocked`, `failed`, or unknown.
- Security exception data is missing owner or target remediation date.
- Any active FOSSA issue has no Jira-linked disposition.

This changes the current behavior from "block on any active license issue" to "block on any active license issue without approved Jira-linked disposition."

## Proposed Implementation Milestones

1. Generate structured Legal request artifacts from FOSSA license findings.
2. Add Jira tracking workflow inputs and fail release-gated paths when active FOSSA findings lack Jira tracking.
3. Add the `fossa-legal-approval` job with disposition status outputs.
4. Wire `fossa-legal-approval` into `pre-publish`.
5. Add PR comments and job summaries with the generated Legal request and gate state.
6. Add optional automatic email sending to `productlegal-jirareq@splunk.com`.
7. Add Jira disposition checking for Security/ProdSec exceptions.
8. Update README and troubleshooting docs.

## Validation Plan

Add fixtures and tests for:

- No active license findings returns `not_required`.
- New license finding without Jira tracking returns `blocked`.
- New license finding with Jira tracking but no disposition returns `pending` or `blocked`.
- Existing approved Jira-linked disposition returns `approved`.
- Existing pending Jira-linked request avoids duplicate request generation.
- Changed package version creates a new review item.
- Changed license or denied policy creates a new review item.
- Malformed or unavailable Jira data fails closed on release-gated paths.

Workflow validation:

- Run `actionlint` for workflow syntax.
- Run the Legal approval script against sample FOSSA license issue JSON.
- Verify PR-to-main path blocks when approval is missing.
- Verify PR-to-main path passes when all active license findings have approved Jira-linked dispositions.
- Verify non-release workflow paths can report Legal state without publishing.

## Open Questions For Leads

- Should Jira tracking be required on every PR to `main`, only when FOSSA fails, or only for release branches/custom releases?
- What Jira fields or labels should represent Legal approval, pending Legal review, rejected usage, and false positive resolution?
- Should the automation create/update Jira issues, or only read and validate Jira state?
- Should approval be scoped per add-on, per release, or globally per dependency/version/license combination?
- Should automatic email sending be enabled in the first release, or should the first release only prepare request artifacts?
- Which token or service account should be used for Jira reads, PR comments, and optional email sending?
- What is the expected SLA or escalation path when Legal approval remains pending?
