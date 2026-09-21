"""Prepare the pinned GitHub comment used for TA Validator exceptions."""

import json
import os
import sys
import tempfile
from pathlib import Path
from urllib import error, parse, request


MARKER = "<!-- ta-validator-exceptions:v1 -->"
API_VERSION = "2026-03-10"
PAGE_SIZE = 100
TEMPLATE = """<!-- ta-validator-exceptions:v1 -->

## Temporary TA Validator exceptions

Edit the YAML below, then rerun the TA Validator check.

<!-- ta-validator-exceptions-config:start -->
```yaml
version: 1
exceptions: []
```
<!-- ta-validator-exceptions-config:end -->

<details>
<summary>Example</summary>

```yaml
version: 1
exceptions:
  - check_slug: sensitive-data
    detection_slug: credential-exposure
    reason: Accepted for this PR; tracked in ADDON-12345.
```
</details>
"""


def _api_url():
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _api_request(token, method, endpoint, operation, payload=None):
    """Request GitHub JSON and retain operation-specific failures."""
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    api_request = request.Request(
        f"{_api_url()}{endpoint}", data=data, headers=headers, method=method
    )
    try:
        with request.urlopen(api_request) as response:
            status = response.getcode()
            response_body = response.read()
    except error.HTTPError as exc:
        raise RuntimeError(f"GitHub {operation} API returned {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"GitHub {operation} API request failed: {exc.reason}") from exc
    if not 200 <= status < 300:
        raise RuntimeError(f"GitHub {operation} API returned {status}")
    try:
        return json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub {operation} API returned invalid JSON") from exc


def fetch_comments(token, repository, pull_request_number):
    """Fetch every current pull-request conversation comment."""
    encoded_repository = parse.quote(repository, safe="/")
    comments = []
    for page in range(1, sys.maxsize):
        page_comments = _api_request(
            token,
            "GET",
            f"/repos/{encoded_repository}/issues/{pull_request_number}/comments"
            f"?per_page={PAGE_SIZE}&page={page}",
            "comments",
        )
        if not isinstance(page_comments, list):
            raise RuntimeError("GitHub comments API returned non-array JSON")
        comments.extend(page_comments)
        if len(page_comments) < PAGE_SIZE:
            return comments


def _marked_comments(comments):
    return [
        comment
        for comment in comments
        if isinstance(comment, dict)
        and isinstance(comment.get("body"), str)
        and MARKER in comment["body"]
    ]


def _comment_id(comment):
    comment_id = comment.get("id") if isinstance(comment, dict) else None
    if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id <= 0:
        raise RuntimeError("GitHub canonical comment has no valid ID")
    return comment_id


def _canonical_comment(token, repository, pull_request_number):
    """Return one existing or newly-created canonical comment, or fail closed."""
    encoded_repository = parse.quote(repository, safe="/")
    marked = _marked_comments(fetch_comments(token, repository, pull_request_number))
    if len(marked) > 1:
        raise RuntimeError("Found multiple marked TA Validator exception comments")
    if marked:
        candidate = marked[0]
    else:
        candidate = _api_request(
            token,
            "POST",
            f"/repos/{encoded_repository}/issues/{pull_request_number}/comments",
            "create",
            {"body": TEMPLATE},
        )
    comment_id = _comment_id(candidate)
    inspected = _api_request(
        token,
        "GET",
        f"/repos/{encoded_repository}/issues/comments/{comment_id}",
        "comment",
    )
    if not isinstance(inspected, dict):
        raise RuntimeError("GitHub comment API returned non-object JSON")
    if _comment_id(inspected) != comment_id:
        raise RuntimeError("GitHub comment API returned a mismatched comment")
    if inspected.get("pin") is None:
        _api_request(
            token,
            "PUT",
            f"/repos/{encoded_repository}/issues/comments/{comment_id}/pin",
            "pin",
        )
    return inspected


def _comment_envelope(comment, repository, pull_request_number):
    body = comment.get("body") if isinstance(comment, dict) else None
    reference = comment.get("html_url") if isinstance(comment, dict) else None
    if not isinstance(body, str) or not body:
        raise RuntimeError("GitHub canonical comment has no body")
    if not isinstance(reference, str) or not reference.strip():
        raise RuntimeError("GitHub canonical comment has no reference URL")
    return {
        "schema_version": 1,
        "source": {
            "provider": "github",
            "repository": repository,
            "pull_request": pull_request_number,
        },
        "comment": {"reference": reference.strip(), "body": body},
    }


def _write_envelope(output_path, envelope):
    """Atomically replace a same-directory, owner-only transport envelope."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as envelope_file:
            descriptor = None
            json.dump(envelope, envelope_file, indent=2)
            envelope_file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def _required_input(name):
    value = os.environ.get(f"INPUT_{name}")
    if not value:
        raise ValueError(f"Missing required input {name.lower()}")
    return value


def _workflow_message(level, message):
    escaped = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


def main():
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

    Path(output_path).unlink(missing_ok=True)
    comment = _canonical_comment(token, repository, pull_request_number)
    _write_envelope(
        output_path, _comment_envelope(comment, repository, pull_request_number)
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        _workflow_message("error", exc)
        raise SystemExit(str(exc)) from exc
