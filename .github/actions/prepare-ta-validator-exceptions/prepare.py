"""Extract the canonical TA Validator exception document from a PR comment."""

import os
import re
import sys
from pathlib import Path


MARKER = "<!-- ta-validator-exceptions:v1 -->"
CONFIG_START_MARKER = "<!-- ta-validator-exceptions-config:start -->"
CONFIG_END_MARKER = "<!-- ta-validator-exceptions-config:end -->"
ACTIVE_YAML_FENCE = re.compile(
    r"\A(?:[ \t]*\r?\n)*[ \t]*```yaml[ \t]*\r?\n"
    r"(?P<document>.*?\r?\n)[ \t]*```[ \t]*(?:\r?\n[ \t]*)*\Z",
    re.DOTALL,
)
FENCE_DELIMITER = re.compile(r"(?m)^[ \t]*```(?!`)")


def extract_pull_request_exception_document(comment_body):
    """Return the sole YAML document framed by the active configuration markers."""
    marker_positions = {
        "comment": (MARKER, comment_body.count(MARKER)),
        "configuration start": (CONFIG_START_MARKER, comment_body.count(CONFIG_START_MARKER)),
        "configuration end": (CONFIG_END_MARKER, comment_body.count(CONFIG_END_MARKER)),
    }
    for marker_name, (marker, count) in marker_positions.items():
        if count != 1:
            raise ValueError(f"Expected exactly one {marker_name} marker")

    comment_index = comment_body.index(MARKER)
    start_index = comment_body.index(CONFIG_START_MARKER)
    end_index = comment_body.index(CONFIG_END_MARKER)
    if not comment_index < start_index < end_index:
        raise ValueError("TA Validator exception markers are misordered")

    active_configuration = comment_body[
        start_index + len(CONFIG_START_MARKER) : end_index
    ]
    if len(FENCE_DELIMITER.findall(active_configuration)) != 2:
        raise ValueError("Expected exactly one fenced YAML document in the active configuration")
    match = ACTIVE_YAML_FENCE.fullmatch(active_configuration)
    if not match:
        raise ValueError("Expected exactly one fenced YAML document in the active configuration")
    return match.group("document")


def write_pull_request_exception_document(output_path, comment_body):
    """Write only the active canonical YAML document from a selected comment."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output_file:
        output_file.write(extract_pull_request_exception_document(comment_body))


def _required_input(name):
    value = os.environ.get(f"INPUT_{name}")
    if not value:
        raise ValueError(f"Missing required input {name.lower()}")
    return value


def _workflow_message(level, message):
    escaped = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


def _positive_integer(value, name):
    try:
        integer = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if integer <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return integer


def _append_comment_reference(github_output, repository, pull_request_number, comment_id):
    reference = (
        f"https://github.com/{repository}/pull/{pull_request_number}"
        f"#issuecomment-{comment_id}"
    )
    with Path(github_output).open("a", encoding="utf-8") as output_file:
        output_file.write(f"comment-reference={reference}\n")


def main():
    output_path = _required_input("OUTPUT_PATH")
    repository = _required_input("REPOSITORY")
    pull_request_number = _positive_integer(
        _required_input("PULL_REQUEST_NUMBER"), "pull_request_number"
    )
    comment_id = _positive_integer(_required_input("COMMENT_ID"), "comment_id")
    comment_body = _required_input("COMMENT_BODY")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        raise ValueError("Missing required GitHub Actions output file")
    write_pull_request_exception_document(output_path, comment_body)
    _append_comment_reference(github_output, repository, pull_request_number, comment_id)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        _workflow_message("error", exc)
        raise SystemExit(str(exc)) from exc
