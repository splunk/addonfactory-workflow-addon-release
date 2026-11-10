"""Decide whether an RFCT CIM comparison runs and freeze the revisions it compares.

The planner is workflow-owned and must never load code from the add-on checkout.
It applies trusted main-branch event and changed-path routing, selects an
immutable baseline release for this run's build, and records the frozen decision
so a release published mid-run cannot change it.
"""

import json
import os
import re
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

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


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


def _eligible_releases(releases):
    """Yield published stable releases paired with their parsed version."""
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        if not tag:
            continue
        match = RELEASE_TAG_RE.match(str(tag).strip())
        if not match or match.group(4):
            continue
        yield release, parse_release_tag(str(tag), "release tag")


def baseline_candidates(releases):
    """Return usable stable releases ordered by highest numeric version first."""
    options = []
    for release, parsed in _eligible_releases(releases):
        # A tag cut without a package asset is not a usable baseline. Fall through
        # to the next older release with an identifiable package.
        if package_asset(release, parsed) is None:
            continue
        options.append((release, parsed))
    options.sort(key=lambda option: option[1]["order"], reverse=True)
    return options


def select_baseline(repository, token, releases, build_sha):
    """Describe the newest eligible baseline that is not the candidate revision."""
    options = baseline_candidates(releases)
    for release, parsed in options:
        described = describe_release(repository, token, release, parsed)
        if not build_sha or described["commit"] != build_sha:
            return described, parsed
    raise PlanSkip("no-baseline")


def _input(name, default=""):
    return (os.environ.get(f"INPUT_{name}") or default).strip()


def _flag(name):
    return _input(name).lower() == "true"


def _workflow_message(level, message):
    escaped = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


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
    check_eligibility(event_name, _flag("IS_FORK"), _input("BASE_BRANCH"))

    releases = list_releases(repository, token)
    baseline, _ = select_baseline(repository, token, releases, _input("BUILD_SHA"))
    base, head = comparison_range(event_name, baseline["commit"])
    paths = [path for path in changed_paths(repository, token, base, head) if matches_supported_path(path)]
    if not paths:
        raise PlanSkip("no-mapping-changes")

    return {
        "decision": PROCEED,
        "reason": "mapping-change",
        "changed_paths": paths,
        "baseline": baseline,
        "candidate": {"source": "build", "commit": _input("BUILD_SHA")},
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
    }
    return document


def write_outputs(document, output_path):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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
        "plan-path": str(output_path),
    }
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")
    return outputs


def main():
    output_path = _input("OUTPUT_PATH")
    if not output_path:
        raise PlanInputError("Missing required input output-path")
    try:
        plan = build_plan(_input("REPOSITORY"), os.environ.get("INPUT_TOKEN") or "")
    except PlanSkip as skip:
        plan = {"decision": SKIP, "reason": skip.reason, "changed_paths": [], "baseline": {}, "candidate": {}}
        _workflow_message("notice", f"RFCT comparison skipped: {skip.reason}")
    document = plan_document(plan)
    write_outputs(document, output_path)
    print(json.dumps(document, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, PlanInputError) as exc:
        _workflow_message("error", exc)
        raise SystemExit(str(exc)) from exc
