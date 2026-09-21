"""Convert a selected PR comment into TA Validator exception input."""

import json
import os
import sys
from pathlib import Path


MARKER = "<!-- ta-validator-exceptions:v1 -->"


def write_exception_input(output_path, exception_input):
    """Write the JSON input consumed by TA Validator."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(exception_input, indent=2) + "\n", encoding="utf-8")


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


def main():
    output_path = _required_input("OUTPUT_PATH")
    repository = _required_input("REPOSITORY")
    pull_request_number = _positive_integer(
        _required_input("PULL_REQUEST_NUMBER"), "pull_request_number"
    )
    comment_id = _positive_integer(_required_input("COMMENT_ID"), "comment_id")
    comment_body = _required_input("COMMENT_BODY")
    exception_input = {
        "schema_version": 1,
        "source": {
            "provider": "github",
            "repository": repository,
            "pull_request": pull_request_number,
        },
        "comment": {
            "reference": (
                f"https://github.com/{repository}/pull/{pull_request_number}"
                f"#issuecomment-{comment_id}"
            ),
            "body": comment_body,
        },
    }
    write_exception_input(output_path, exception_input)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        _workflow_message("error", exc)
        raise SystemExit(str(exc)) from exc
