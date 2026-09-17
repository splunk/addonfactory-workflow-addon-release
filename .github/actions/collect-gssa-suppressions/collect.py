"""Collect valid GSSA suppression declarations from GitHub issue comments."""

import json
import os
import sys
import tempfile
from pathlib import Path
from urllib import error, parse, request


DIRECTIVE = "/gssa-ignore"
API_VERSION = "2022-11-28"
PAGE_SIZE = 100


def _comment_reference(comment):
    reference = comment.get("html_url") if isinstance(comment, dict) else None
    return reference.strip() if isinstance(reference, str) else ""


def _warning(comment, message):
    reference = _comment_reference(comment) or "<unknown comment>"
    return f"{message} in {reference}"


def parse_comment(comment):
    """Parse one current GitHub comment into declarations or a rejection warning.

    A malformed directive is rejected as a unit, so none of its selectors can
    accidentally suppress a scorecard finding.
    """
    if not isinstance(comment, dict):
        return {"entries": [], "warnings": []}

    body = comment.get("body")
    lines = str(body or "").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0] != DIRECTIVE:
        return {"entries": [], "warnings": []}

    selectors = []
    reasons = []
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            return {
                "entries": [],
                "warnings": [_warning(comment, "Malformed /gssa-ignore line")],
            }
        field, value = line.split(":", 1)
        value = value.strip()
        if field not in {"check", "detection", "reason"}:
            return {
                "entries": [],
                "warnings": [_warning(comment, "Unknown /gssa-ignore field")],
            }
        if not value:
            return {
                "entries": [],
                "warnings": [_warning(comment, f"Blank {field} selector or reason")],
            }
        if field == "reason":
            reasons.append(value)
        else:
            selectors.append((field, value))

    if not selectors:
        return {
            "entries": [],
            "warnings": [_warning(comment, "/gssa-ignore requires at least one selector")],
        }
    if len(reasons) != 1:
        return {
            "entries": [],
            "warnings": [_warning(comment, "/gssa-ignore requires exactly one reason")],
        }

    user = comment.get("user")
    author = user.get("login") if isinstance(user, dict) else None
    reference = _comment_reference(comment)
    if not isinstance(author, str) or not author.strip() or not reference:
        return {
            "entries": [],
            "warnings": [_warning(comment, "/gssa-ignore requires an author and reference")],
        }

    declaration = {
        "author": author.strip(),
        "reason": reasons[0],
        "reference": reference,
    }
    entries = []
    for selector_type, value in selectors:
        if selector_type == "check":
            entries.append(
                {
                    "type": "check",
                    "check_slug": value,
                    "declaration": declaration,
                }
            )
            continue

        check_slug, separator, detection_slug = value.partition("/")
        if not separator or not check_slug.strip() or not detection_slug.strip():
            return {
                "entries": [],
                "warnings": [_warning(comment, "Malformed detection selector")],
            }
        entries.append(
            {
                "type": "detection",
                "check_slug": check_slug.strip(),
                "detection_slug": detection_slug.strip(),
                "declaration": declaration,
            }
        )
    return {"entries": entries, "warnings": []}


def build_manifest(comments, context):
    """Build the schema-v1 manifest while preserving current-comment ordering."""
    suppressions = []
    warnings = []
    for comment in comments:
        parsed = parse_comment(comment)
        suppressions.extend(parsed["entries"])
        warnings.extend(parsed["warnings"])

    if not suppressions:
        return {"manifest": None, "warnings": warnings}
    return {
        "manifest": {
            "schema_version": 1,
            "source": {
                "provider": "github",
                "repository": context["repository"],
                "pull_request": context["pullRequest"],
            },
            "suppressions": suppressions,
        },
        "warnings": warnings,
    }


def fetch_comments(token, repository, pull_request_number):
    """Fetch all current issue comments for a pull request from the GitHub API."""
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    encoded_repository = parse.quote(repository, safe="/")
    comments = []
    for page in range(1, sys.maxsize):
        url = (
            f"{api_url}/repos/{encoded_repository}/issues/{pull_request_number}"
            f"/comments?per_page={PAGE_SIZE}&page={page}"
        )
        api_request = request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with request.urlopen(api_request) as response:
                status = response.getcode()
                payload = response.read()
        except error.HTTPError as exc:
            raise RuntimeError(f"GitHub comments API returned {exc.code}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"GitHub comments API request failed: {exc.reason}") from exc
        if not 200 <= status < 300:
            raise RuntimeError(f"GitHub comments API returned {status}")
        try:
            page_comments = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("GitHub comments API returned invalid JSON") from exc
        if not isinstance(page_comments, list):
            raise RuntimeError("GitHub comments API returned non-array JSON")
        comments.extend(page_comments)
        if len(page_comments) < PAGE_SIZE:
            return comments


def _required_input(name):
    value = os.environ.get(f"INPUT_{name}")
    if not value:
        raise ValueError(f"Missing required input {name.lower()}")
    return value


def _workflow_message(level, message):
    escaped = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


def _write_manifest(output_path, manifest):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as manifest_file:
            descriptor = None
            json.dump(manifest, manifest_file, indent=2)
            manifest_file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def main():
    """Run the collector as a GitHub composite-action step."""
    token = _required_input("TOKEN")
    repository = _required_input("REPOSITORY")
    raw_pull_request_number = _required_input("PULL_REQUEST_NUMBER")
    output_path = _required_input("OUTPUT_PATH")
    try:
        pull_request_number = int(raw_pull_request_number)
    except ValueError as exc:
        raise ValueError("pull_request_number must be a positive integer") from exc
    if pull_request_number <= 0:
        raise ValueError("pull_request_number must be a positive integer")

    comments = fetch_comments(token, repository, pull_request_number)
    result = build_manifest(
        comments,
        {"repository": repository, "pullRequest": pull_request_number},
    )
    for warning in result["warnings"]:
        _workflow_message("warning", warning)
    if result["manifest"] is not None:
        _write_manifest(output_path, result["manifest"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # GitHub Actions needs a safe command message and failure.
        _workflow_message("error", exc)
        raise
