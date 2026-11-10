"""Decide whether an RFCT CIM comparison runs and freeze the revisions it compares.

The planner is workflow-owned and must never load code from the add-on checkout.
It applies trusted main-branch event and changed-path routing, selects an
immutable baseline release for this run's build, and records the frozen decision
so a release published mid-run cannot change it.
"""

import html
import json
import os
import re
import sys
import unicodedata
import uuid
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
PER_PAGE = 100
# Bounds the paginated release and compare walks so a pathological repository
# cannot hang the job instead of failing it.
MAX_PAGES = 50
MAX_TAG_DEPTH = 10
RELEASE_TAG_RE = re.compile(r"\A[vV]?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?\Z")
PACKAGE_ASSET_SUFFIX = ".spl"
ABSENT_SHA = re.compile(r"\A0{40}\Z")

SCHEMA_VERSION = 1
PROCEED = "proceed"
SKIP = "skip"


class PlanInputError(ValueError):
    """An operator-supplied input or repository state makes the plan invalid."""


class PlanSkip(Exception):
    """RFCT is not eligible for this event; record the reason and skip."""

    def __init__(self, reason, baseline_selection=None):
        super().__init__(reason)
        self.reason = reason
        self.baseline_selection = baseline_selection


def safe_data(value):
    """Redact known transport credentials from logs and public plan evidence."""
    if isinstance(value, dict):
        return {key: safe_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_data(item) for item in value]
    if isinstance(value, str):
        for name in ("INPUT_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
            secret = os.environ.get(name)
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def log_event(phase, event, **fields):
    """Write one safe JSON log record, never an unintended runner command."""
    record = safe_data({"component": "rfct-planner", "phase": phase, "event": event, **fields})
    serialized = json.dumps(record, sort_keys=True).replace("::", r"\u003a\u003a")
    print(serialized, file=sys.stderr)


def selection_audit():
    return {"policy": "highest-usable-stable-version", "selected": None, "skipped": [], "fallback": False}


def record_skipped(selection, release, reason, parsed=None):
    skipped = {"tag": release.get("tag_name") or "", "release_id": release.get("id"), "reason": reason}
    if parsed:
        skipped["version"] = parsed["version"]
    selection["skipped"].append(skipped)


def matches_supported_path(path):
    """Report whether a changed repository path can affect CIM mappings."""
    if path == "globalConfig.json":
        return True
    if path.startswith("package/") and path.endswith(".conf"):
        return True
    return path.startswith(
        (
            "package/default/data/models/",
            "package/default/data/spl2/",
            "package/lookups/",
            "tests/knowledge/",
        )
    )


def parse_release_tag(value, field):
    """Normalize a release reference to a tag, version, and ordering key."""
    candidate = value.strip()
    match = RELEASE_TAG_RE.match(candidate)
    if not match:
        raise PlanInputError(
            f"{field} must be a semantic version or its v-prefixed tag, got '{value}'"
        )
    major, minor, patch, prerelease = match.groups()
    version = f"{major}.{minor}.{patch}" + (f"-{prerelease}" if prerelease else "")
    return {
        "tag": f"v{version}",
        "version": version,
        "order": (int(major), int(minor), int(patch)),
        "prerelease": bool(prerelease),
    }


def _request_json(url, token):
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request) as response:  # nosec B310 - fixed GitHub API host
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise PlanInputError(f"GitHub API request failed for {url}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise PlanInputError(f"GitHub API request failed for {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise PlanInputError(f"GitHub API returned malformed JSON for {url}") from exc


def paginate(url_template, token, collect):
    """Walk a paginated GitHub collection so a long diff cannot be truncated."""
    gathered = []
    for page in range(1, MAX_PAGES + 1):
        payload = _request_json(url_template.format(page=page, per_page=PER_PAGE), token)
        items = collect(payload)
        gathered.extend(items)
        if len(items) < PER_PAGE:
            return gathered
    raise PlanInputError(f"GitHub API pagination exceeded {MAX_PAGES} pages for {url_template}")


def changed_paths(repository, token, base, head):
    """Return every path touched between two revisions, including both rename sides."""
    url_template = (
        f"{API_ROOT}/repos/{repository}/compare/"
        f"{quote(base, safe='')}...{quote(head, safe='')}"
        "?per_page={per_page}&page={page}"
    )
    files = paginate(url_template, token, lambda payload: payload.get("files") or [])
    paths = set()
    for entry in files:
        for key in ("filename", "previous_filename"):
            value = entry.get(key)
            if value:
                paths.add(value)
    return sorted(paths)


def list_releases(repository, token):
    url_template = f"{API_ROOT}/repos/{repository}/releases?per_page={{per_page}}&page={{page}}"
    return paginate(url_template, token, lambda payload: payload if isinstance(payload, list) else [])


def resolve_tag_commit(repository, token, tag):
    """Peel lightweight or annotated tags to the actual commit object."""
    payload = _request_json(
        f"{API_ROOT}/repos/{repository}/git/ref/tags/{quote(tag, safe='')}", token
    )
    seen = set()
    for _ in range(MAX_TAG_DEPTH):
        target = payload.get("object") or {}
        sha, kind = target.get("sha"), target.get("type")
        if kind not in ("commit", "tag") or not re.fullmatch(r"[0-9a-f]{40}", str(sha)) or sha in seen:
            raise PlanInputError(f"Could not resolve a commit for tag {tag}")
        if kind == "commit":
            return sha
        seen.add(sha)
        payload = _request_json(f"{API_ROOT}/repos/{repository}/git/tags/{sha}", token)
    raise PlanInputError(f"Annotated tag nesting exceeded {MAX_TAG_DEPTH} objects for {tag}")


def package_asset(release, parsed):
    """Return the splunkbase package asset for a release, or None.

    A release publishes the splunkbase package alongside partitioned deployment
    packages such as ``Splunk_TA_box-0.8.7-_search_heads.spl``, so the asset whose
    name ends with the release version is the one to compare. Older releases that
    predate asset publishing have no package at all.
    """
    assets = [
        asset
        for asset in release.get("assets") or []
        if str(asset.get("name", "")).endswith(PACKAGE_ASSET_SUFFIX)
    ]
    versioned = [
        asset
        for asset in assets
        if str(asset["name"]).endswith(f"-{parsed['version']}{PACKAGE_ASSET_SUFFIX}")
    ]
    if len(versioned) == 1:
        return versioned[0]
    if not versioned and len(assets) == 1:
        return assets[0]
    return None


def require_package_asset(release, parsed):
    asset = package_asset(release, parsed)
    if asset is None:
        raise PlanInputError(
            f"Release {parsed['tag']} does not publish exactly one identifiable "
            f"{PACKAGE_ASSET_SUFFIX} package to compare"
        )
    return asset


def describe_release(repository, token, release, parsed):
    asset = require_package_asset(release, parsed)
    return {
        "tag": release.get("tag_name"),
        "version": parsed["version"],
        "commit": resolve_tag_commit(repository, token, release.get("tag_name")),
        "release_id": release.get("id"),
        "asset_id": asset.get("id"),
        "asset_name": asset.get("name"),
    }


def _eligible_releases(releases, selection):
    """Yield published stable releases paired with their parsed version."""
    for release in releases:
        if release.get("draft"):
            record_skipped(selection, release, "draft")
            continue
        if release.get("prerelease"):
            record_skipped(selection, release, "prerelease")
            continue
        tag = release.get("tag_name")
        if not tag:
            record_skipped(selection, release, "invalid-version")
            continue
        match = RELEASE_TAG_RE.match(str(tag).strip())
        if not match:
            record_skipped(selection, release, "invalid-version")
            continue
        if match.group(4):
            record_skipped(selection, release, "prerelease")
            continue
        yield release, parse_release_tag(str(tag), "release tag")


def baseline_candidates(releases, selection=None):
    """Return usable stable releases ordered by highest numeric version first."""
    options = []
    if selection is None:
        selection = selection_audit()
    for release, parsed in _eligible_releases(releases, selection):
        # A tag cut without a package asset is not a usable baseline. Fall through
        # to the next older release with an identifiable package.
        if package_asset(release, parsed) is None:
            reason = "ambiguous-package" if any(
                str(asset.get("name", "")).endswith(PACKAGE_ASSET_SUFFIX) for asset in release.get("assets") or []
            ) else "missing-package"
            record_skipped(selection, release, reason, parsed)
            continue
        options.append((release, parsed))
    options.sort(key=lambda option: option[1]["order"], reverse=True)
    return options


def select_baseline(repository, token, releases, build_sha, selection=None):
    """Describe the newest eligible baseline that is not the candidate revision."""
    if selection is None:
        selection = selection_audit()
    options = baseline_candidates(releases, selection)
    log_event("baseline", "candidates", release_count=len(releases), usable_count=len(options),
              skipped_counts=dict(Counter(item["reason"] for item in selection["skipped"])))
    for release, parsed in options:
        described = describe_release(repository, token, release, parsed)
        if not build_sha or described["commit"] != build_sha:
            selection["selected"] = described
            warn_baseline_fallback(selection, parsed)
            log_event("baseline", "selected", tag=described["tag"], version=described["version"],
                      commit=described["commit"], fallback=selection["fallback"])
            return described, parsed
        record_skipped(selection, release, "same-build-revision", parsed)
        log_event("baseline", "same-build-excluded", tag=described["tag"], commit=described["commit"])
    raise PlanSkip("no-baseline", selection)


def warn_baseline_fallback(selection, parsed):
    """Warn only when a higher stable package was unusable and an older one won."""
    for skipped in selection["skipped"]:
        if skipped["reason"] not in ("missing-package", "ambiguous-package"):
            continue
        if parse_release_tag(skipped["version"], "release version")["order"] <= parsed["order"]:
            continue
        selection["fallback"] = True
        detail = "has no .spl package" if skipped["reason"] == "missing-package" else "has ambiguous .spl packages"
        _workflow_message("warning", f"RFCT baseline fallback: stable release {skipped['tag']} {detail}; "
                          f"selecting older release {selection['selected']['tag']}.")
        log_event("baseline", "fallback", skipped=skipped, selected_tag=selection["selected"]["tag"])


def _input(name, default=""):
    return (os.environ.get(f"INPUT_{name}") or default).strip()


def _flag(name):
    return _input(name).lower() == "true"


def _workflow_message(level, message):
    escaped = safe_data(str(message)).replace("::", ": :").replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}", file=sys.stderr)


def check_eligibility(event_name, is_fork, base_branch):
    """Allow trusted main PRs and pushes before any credential is used."""
    if event_name == "pull_request_target":
        raise PlanSkip("untrusted-event")
    if event_name == "pull_request" and is_fork:
        raise PlanSkip("fork-pull-request")
    if event_name not in ("pull_request", "push"):
        raise PlanSkip("unsupported-event")
    if base_branch != "main":
        raise PlanSkip("unsupported-branch")


def comparison_range(event_name, baseline_commit):
    """Return the event-aware revision range used for changed-path detection.

    A pull request compares the event base against the revision that is actually
    built (the merge commit), never the head SHA alone. A push compares
    ``before``/``after``, falling back to the frozen baseline commit when the
    branch was just created and ``before`` is absent.
    """
    if event_name == "pull_request":
        base = _input("PR_BASE_SHA")
        head = _input("BUILD_SHA")
        if not base or not head:
            raise PlanInputError("A pull request requires both a base SHA and a build SHA")
        return base, head

    after = _input("PUSH_AFTER") or _input("BUILD_SHA")
    before = _input("PUSH_BEFORE")
    if not after:
        raise PlanInputError("A push requires an after SHA")
    if not before or ABSENT_SHA.match(before):
        if not baseline_commit:
            raise PlanInputError(
                "A newly created branch needs a baseline commit for changed-path detection"
            )
        before = baseline_commit
    return before, after


def build_plan(repository, token):
    """Produce the frozen RFCT decision for this event."""
    event_name = _input("EVENT_NAME")
    log_event("eligibility", "checking", event_name=event_name, base_branch=_input("BASE_BRANCH"), is_fork=_flag("IS_FORK"))
    check_eligibility(event_name, _flag("IS_FORK"), _input("BASE_BRANCH"))
    log_event("eligibility", "accepted")

    releases = list_releases(repository, token)
    log_event("releases", "loaded", count=len(releases))
    selection = selection_audit()
    baseline, _ = select_baseline(repository, token, releases, _input("BUILD_SHA"), selection)
    base, head = comparison_range(event_name, baseline["commit"])
    paths = [path for path in changed_paths(repository, token, base, head) if matches_supported_path(path)]
    log_event("paths", "checked", base=base, head=head, matched_count=len(paths))
    if not paths:
        raise PlanSkip("no-mapping-changes", selection)

    return {
        "decision": PROCEED,
        "reason": "mapping-change",
        "changed_paths": paths,
        "baseline": baseline,
        "candidate": {"source": "build", "commit": _input("BUILD_SHA")},
        "baseline_selection": selection,
    }


def plan_document(plan):
    document = {
        "schema_version": SCHEMA_VERSION,
        "decision": plan["decision"],
        "status": "skipped" if plan["decision"] == SKIP else "",
        "reason": plan["reason"],
        "event_name": _input("EVENT_NAME"),
        "base_branch": _input("BASE_BRANCH"),
        "pull_request_head_sha": _input("PR_HEAD_SHA"),
        "build_sha": _input("BUILD_SHA"),
        "changed_paths": plan.get("changed_paths", []),
        "baseline": plan.get("baseline", {}),
        "candidate": plan.get("candidate", {}),
        "baseline_selection": plan.get("baseline_selection") or selection_audit(),
    }
    return document


def markdown_text(value):
    """Render untrusted values as plain text inside Markdown tables and lists."""
    visible = "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char).startswith("C") or char in "\u2028\u2029" else char
        for char in str(safe_data(value))
    )
    escaped = re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", visible)
    return html.escape(escaped, quote=True).replace("::", "&#58;&#58;")


def render_summary(document):
    """Explain the frozen decision without duplicating the full release audit."""
    document = safe_data(document)
    selection = document.get("baseline_selection") or selection_audit()
    baseline = selection.get("selected") or document.get("baseline") or {}
    candidate_commit = document.get("candidate", {}).get("commit") or document.get("build_sha")
    baseline_text = baseline.get("tag") or "None selected"
    if baseline.get("commit"):
        baseline_text += " (commit " + str(baseline["commit"]) + ")"
    candidate_text = "Current build" + (" (commit " + str(candidate_commit) + ")" if candidate_commit else "")
    lines = [
        "## RFCT CIM changes plan", "", "| Field | Value |", "| --- | --- |",
        "| Decision | " + markdown_text(document["decision"]) + " |",
        "| Reason | " + markdown_text(document["reason"]) + " |",
        "| Baseline | " + markdown_text(baseline_text) + " |",
        "| Candidate | " + markdown_text(candidate_text) + " |", "",
    ]
    if baseline:
        lines.append("The baseline is the highest usable stable release whose commit differs from this build.")
        selected_order = parse_release_tag(str(baseline["version"]), "selected baseline version")["order"]
        fallback = [
            item for item in selection["skipped"]
            if item["reason"] in ("missing-package", "ambiguous-package")
            and parse_release_tag(item["version"], "skipped release version")["order"] > selected_order
        ]
        if fallback:
            lines.extend(["", "The following newer stable releases could not be used:", ""])
            for item in fallback:
                reason = "no .spl package" if item["reason"] == "missing-package" else "ambiguous .spl packages"
                lines.append("- " + markdown_text(item["tag"]) + ": " + reason + ".")
    elif document["reason"] == "no-baseline":
        lines.append("No usable stable release package differed from this build, so no cluster comparison will run.")
        counts = Counter(item["reason"] for item in selection["skipped"])
        if counts:
            lines.extend(["", "Release exclusions:", ""])
            for reason, count in sorted(counts.items()):
                lines.append("- " + markdown_text(reason) + ": " + str(count) + ".")
        else:
            lines.append("No published releases were available for baseline selection.")
    lines.extend(["", "The RFCT plan JSON artifact contains the full selection audit.", ""])
    return "\n".join(lines)


def write_outputs(document, output_path):
    document = safe_data(document)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path = Path(output_path).with_suffix(".summary.md")
    summary_path.write_text(render_summary(document), encoding="utf-8")

    baseline = document["baseline"]
    candidate = document["candidate"]
    outputs = {
        "decision": document["decision"],
        "status": document["status"],
        "reason": document["reason"],
        "changed-paths": json.dumps(document["changed_paths"]),
        "baseline-tag": baseline.get("tag") or "",
        "baseline-version": baseline.get("version") or "",
        "baseline-commit": baseline.get("commit") or "",
        "baseline-release-id": baseline.get("release_id") or "",
        "baseline-asset-id": baseline.get("asset_id") or "",
        "baseline-asset-name": baseline.get("asset_name") or "",
        "candidate-source": candidate.get("source") or "",
        "candidate-commit": candidate.get("commit") or "",
        "baseline-selection": json.dumps(document["baseline_selection"], sort_keys=True),
        "plan-path": str(output_path),
        "summary-path": str(summary_path),
    }
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                text = str(value)
                if "\n" in text or "\r" in text:
                    delimiter = "rfct_" + uuid.uuid4().hex
                    while delimiter in text:
                        delimiter = "rfct_" + uuid.uuid4().hex
                    handle.write(f"{key}<<{delimiter}\n{text}\n{delimiter}\n")
                else:
                    handle.write(f"{key}={text}\n")
    return outputs


def main():
    output_path = _input("OUTPUT_PATH")
    if not output_path:
        raise PlanInputError("Missing required input output-path")
    try:
        plan = build_plan(_input("REPOSITORY"), os.environ.get("INPUT_TOKEN") or "")
    except PlanSkip as skip:
        plan = {"decision": SKIP, "reason": skip.reason, "changed_paths": [], "baseline": {}, "candidate": {},
                "baseline_selection": skip.baseline_selection}
        _workflow_message("notice", f"RFCT comparison skipped: {skip.reason}")
    document = plan_document(plan)
    write_outputs(document, output_path)
    log_event("decision", "completed", decision=document["decision"], reason=document["reason"],
              baseline_tag=document["baseline"].get("tag", ""), matched_path_count=len(document["changed_paths"]))


if __name__ == "__main__":
    try:
        main()
    except (OSError, PlanInputError) as exc:
        log_event("decision", "failed", error=str(exc))
        _workflow_message("error", exc)
        raise SystemExit(1) from None
