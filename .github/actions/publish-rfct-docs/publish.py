#!/usr/bin/env python3
"""Publish versioned RFCT docs through one idempotent PR and update Jira."""

import base64
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


GITHUB_API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
TICKET_RE = re.compile(r"\bADDON-[0-9]{5,6}\b")
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?\Z")


class PublishError(RuntimeError):
    """A publishing input or remote operation failed."""


def safe_log(value):
    """Prevent untrusted metadata from becoming multiline or runner commands."""
    text = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", str(value))
    return text.replace("::", ": :")[:1000]


def api(method, path, token, payload=None, accept="application/vnd.github+json"):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{GITHUB_API}/{path.lstrip('/')}",
        data=body,
        method=method,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as error:
        raise PublishError(f"GitHub API {method} {path} returned HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise PublishError(f"GitHub API {method} {path} failed: {type(error).__name__}") from error


def reviewers_from_config(value):
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(parsed, list) or not parsed:
        raise PublishError("Configure RFCT_CIM_REVIEWERS as a nonempty JSON array or comma-separated logins")
    if any(not isinstance(login, str) or not LOGIN_RE.fullmatch(login) for login in parsed):
        raise PublishError("RFCT_CIM_REVIEWERS contains an invalid GitHub login")
    return list(dict.fromkeys(parsed))


def jira_tickets(event_path):
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublishError("Could not read the GitHub event payload") from error
    messages = [commit.get("message", "") for commit in event.get("commits", [])]
    pull = event.get("pull_request", {})
    if pull.get("title"):
        messages.append(pull["title"])
    head_commit = event.get("head_commit", {}) or {}
    messages.append(head_commit.get("message", ""))
    return sorted({ticket for message in messages for ticket in TICKET_RE.findall(message)})


def read_inputs():
    result_path = Path(os.environ["RFCT_RESULT"])
    docs_directory = Path(os.environ["RFCT_DOCS_DIRECTORY"])
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result = {"status": "failed", "reason": "RFCT result manifest is missing or invalid"}
    status = os.environ["RFCT_STATUS"]
    if status not in ("success", "failure", "cancelled"):
        status = result.get("status", status)
    if status == "success" and result.get("status") not in ("changes", "no-changes"):
        status = "failure"
    version = str(result.get("candidate", {}).get("version", ""))
    if status == "success" and not VERSION_RE.fullmatch(version):
        raise PublishError("RFCT result does not contain a valid candidate version")
    reports = list(docs_directory.rglob("index.md")) if docs_directory.exists() else []
    if status == "success" and len(reports) != 1:
        raise PublishError(f"Expected one generated CIM index.md, found {len(reports)}")
    return result, status, version, reports[0] if reports else None


def release_pr(repository, branch, token):
    owner = repository.split("/", 1)[0]
    query = quote(f"state=open&head={owner}:{branch}", safe="=&:")
    found = api("GET", f"repos/{repository}/pulls?{query}&per_page=100", token)
    if len(found) > 1:
        raise PublishError(f"More than one open documentation PR matches branch {branch}")
    return found[0] if found else None


def ensure_branch(repository, branch, token):
    ref_path = f"repos/{repository}/git/ref/heads/{quote(branch, safe='/')}"
    try:
        return api("GET", ref_path, token)["object"]["sha"]
    except PublishError as error:
        if "HTTP 404" not in str(error):
            raise
    main_ref = api("GET", f"repos/{repository}/git/ref/heads/main", token)
    created = api("POST", f"repos/{repository}/git/refs", token,
                  {"ref": f"refs/heads/{branch}", "sha": main_ref["object"]["sha"]})
    return created["object"]["sha"]


def write_report(repository, branch, report, destination, token):
    encoded_path = quote(destination, safe="/")
    query = quote(f"ref={branch}", safe="=&/:.-")
    current = None
    try:
        current = api("GET", f"repos/{repository}/contents/{encoded_path}?{query}", token)
    except PublishError as error:
        if "HTTP 404" not in str(error):
            raise
    report_bytes = report.read_bytes()
    if current and current.get("content"):
        current_content = base64.b64decode(current["content"].encode("ascii"))
        if current_content == report_bytes:
            print("Generated CIM report is unchanged; keeping the existing branch commit")
            return current
    payload = {
        "message": f"docs(CIM): update generated report for {destination.split('/')[2]}",
        "content": base64.b64encode(report_bytes).decode("ascii"),
        "branch": branch,
    }
    if current:
        payload["sha"] = current["sha"]
    return api("PUT", f"repos/{repository}/contents/{encoded_path}", token, payload)


def pr_body(result, run_url, artifact_url):
    baseline = result.get("baseline", {})
    candidate = result.get("candidate", {})
    return "\n".join([
        "<!-- rfct-cim-docs:managed -->",
        "## RFCT CIM comparison",
        "",
        f"- **TA:** `{result.get('app_code_name', '')}`",
        f"- **Release:** `{candidate.get('version', '')}`",
        f"- **Baseline:** `{baseline.get('version', '')}` (`{baseline.get('tag', '')}`)",
        f"- **Candidate commit:** `{candidate.get('commit', '')}`",
        f"- **Result:** `{result.get('status', '')}`",
        f"- **Workflow run:** {run_url}",
        f"- **RFCT evidence:** {artifact_url}",
        "",
        "This pull request is managed by the reusable add-on release workflow. "
        "Reruns for this TA release update the report on this branch.",
    ])


def create_or_update_pr(repository, version, report, result, reviewers, token):
    branch = f"rfct/cim-docs/v{version}"
    destination = f"docs/CIMChanges/{version}/index.md"
    artifact_url = f"https://github.com/{repository}/actions/runs/{os.environ['RFCT_RUN_ID']}#artifacts"
    title = f"docs(CIM): update CIM changes for {result.get('app_code_name', repository)} {version}"
    pull = release_pr(repository, branch, token)
    ensure_branch(repository, branch, token)
    write_report(repository, branch, report, destination, token)
    body = pr_body(result, os.environ["RFCT_RUN_URL"], artifact_url)
    if pull:
        number = pull["number"]
        api("PATCH", f"repos/{repository}/pulls/{number}", token, {"title": title, "body": body})
    else:
        pull = api("POST", f"repos/{repository}/pulls", token,
                   {"title": title, "head": branch, "base": "main", "body": body})
        number = pull["number"]
    pull_url = pull.get("html_url") or f"https://github.com/{repository}/pull/{number}"
    try:
        api("POST", f"repos/{repository}/pulls/{number}/requested_reviewers", token,
            {"reviewers": reviewers})
    except PublishError as error:
        error.pull_request_url = pull_url
        raise
    return pull_url


def jira_comment(issue, status, run_url, pr_url, report_url, report_location, artifact_url, error_message=""):
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    credentials = base64.b64encode(
        f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_TOKEN']}".encode("utf-8")
    ).decode("ascii")
    paragraph = lambda text: {"type": "paragraph", "content": [{"type": "text", "text": text}]}
    link_node = lambda label, url: {"type": "text", "text": label,
                                    "marks": [{"type": "link", "attrs": {"href": url}}]}
    content = [paragraph(f"RFCT CIM documentation workflow: {status}.")]
    content.append({"type": "paragraph", "content": [link_node("Workflow run", run_url)]})
    if pr_url:
        content.append({"type": "paragraph", "content": [link_node("CIM documentation PR", pr_url)]})
    if report_url:
        content.append({"type": "paragraph", "content": [link_node("Generated CIM report", report_url)]})
    if report_location:
        content.append(paragraph(f"Generated report path: {report_location}"))
    content.append({"type": "paragraph", "content": [link_node("RFCT evidence", artifact_url)]})
    if error_message:
        content.append(paragraph(f"Details: {error_message[:500]}"))
    payload = json.dumps({"body": {"type": "doc", "version": 1, "content": content}}).encode("utf-8")
    request = Request(
        f"{base}/rest/api/3/issue/{quote(issue, safe='-')}/comment", data=payload, method="POST",
        headers={"Authorization": f"Basic {credentials}", "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise PublishError(f"Could not update Jira issue {issue}: {type(error).__name__}") from error


def main():
    repository = os.environ["RFCT_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    run_url = os.environ["RFCT_RUN_URL"]
    try:
        result, status, version, report = read_inputs()
        input_error = ""
    except PublishError as error:
        result = {"status": "failed", "reason": safe_log(error)}
        status, version, report = "failure", "", None
        input_error = safe_log(error)
    tickets = jira_tickets(os.environ["GITHUB_EVENT_PATH"])
    pr_url = ""
    report_url = ""
    report_location = ""
    artifact_url = f"https://github.com/{repository}/actions/runs/{os.environ['RFCT_RUN_ID']}#artifacts"
    error_message = input_error
    publish_status = "success" if status == "success" else "failure"
    publish_docs = os.environ.get("RFCT_PUBLISH_DOCS", "false").lower() == "true"
    try:
        if publish_status == "success" and publish_docs:
            reviewers = reviewers_from_config(os.environ["RFCT_REVIEWERS"])
            pr_url = create_or_update_pr(repository, version, report, result, reviewers, token)
            report_url = f"https://github.com/{repository}/blob/rfct/cim-docs/v{version}/docs/CIMChanges/{version}/index.md"
            report_location = f"docs/CIMChanges/{version}/index.md on branch rfct/cim-docs/v{version}"
            print(f"Updated CIM documentation PR: {pr_url}")
        elif publish_status == "success":
            report_url = artifact_url
            report_location = f"cluster/docs/CIMChanges/{version}/index.md in the RFCT evidence artifact"
            print("RFCT succeeded; this event updates Jira without publishing a release documentation PR")
        else:
            error_message = str(result.get("reason") or "RFCT did not complete successfully")
    except PublishError as error:
        publish_status = "failure"
        error_message = safe_log(error)
        pr_url = getattr(error, "pull_request_url", pr_url)
        print(f"::error::{error_message}", file=sys.stderr)
    for issue in tickets:
        jira_comment(issue, publish_status, run_url, pr_url, report_url, report_location, artifact_url, error_message)
    if not tickets:
        print("::warning::No ADDON Jira key was found in this push's commit messages")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"pull-request-url={pr_url}\n")
    if publish_status != "success":
        raise SystemExit(error_message or "RFCT CIM documentation publishing failed")


if __name__ == "__main__":
    try:
        main()
    except PublishError as error:
        print(f"::error::{safe_log(error)}", file=sys.stderr)
        raise SystemExit(1)
