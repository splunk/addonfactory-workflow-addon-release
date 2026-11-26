#
# Copyright 2026 Splunk Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Prepare an idempotent, reviewable release-docs update from CI artifacts."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import quote, unquote

try:
    from .generate_draft_credits import load_settings
except ImportError:  # Direct script execution.
    from generate_draft_credits import load_settings


def _relative_file(repo: Path, value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("TA docs index must be a repository-relative path")
    result = repo.joinpath(*path.parts)
    if not result.is_file():
        raise ValueError("Configured TA docs index does not exist")
    return result


def _docs_index(repo: Path, settings: dict) -> Path:
    configured = settings.get("docs_index")
    if configured:
        if not isinstance(configured, str):
            raise ValueError("docs_index must be repository-relative text")
        return _relative_file(repo, configured)
    for candidate in (
        "docs/ReleaseNotes/index.md",
        "docs/ReleaseHistory/index.md",
        "docs/ReleaseNotes.md",
    ):
        path = repo / candidate
        if path.is_file():
            return path
    raise ValueError("Set docs_index in .fossa-credits.json for this TA")


def _update_index(path: Path, display_name: str, version: str, credits_path: Path,
                  repo: Path, section_heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    heading_index = next((i for i, line in enumerate(lines)
                          if re.match(r"^#{1,6}\s+" + re.escape(section_heading) + r"\s*#*\s*$",
                                      line.rstrip("\r\n"), re.IGNORECASE)), None)
    if heading_index is None:
        raise ValueError("TA docs index has no third-party software attributions section")
    heading_level = len(lines[heading_index].split()[0])
    end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s", lines[index])
        if match and len(match.group(1)) <= heading_level:
            end = index
            break

    relative = os.path.relpath(credits_path, path.parent).replace(os.sep, "/")
    target = quote(relative, safe="/.-_~")
    label = display_name.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    link = f"[{label} third-party software credits ({version})]({target})\n"
    candidates = []
    for index in range(heading_index + 1, end):
        if "thirdpartycredits/" in lines[index].lower():
            match = re.search(r"\]\(([^)]+)\)", lines[index])
            if match:
                candidates.append((index, unquote(match.group(1))))
    if len(candidates) > 1:
        raise ValueError("TA docs section has multiple third-party credits links")
    if candidates:
        index, old_target = candidates[0]
        if old_target != relative and old_target != target:
            lines[index] = link
    else:
        lines.insert(heading_index + 1, link)
    updated = "".join(lines)
    updated = updated.replace("available as a PDF file for download:", "available for download:")
    return updated


def prepare(args) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.version):
        raise ValueError("Release version is not a safe filename identity")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.expected_revision):
        raise ValueError("Expected scan revision must be a 40-character Git SHA")
    settings = load_settings(args.ta_repo / ".fossa-credits.json")
    summary_path = args.artifacts / "credits-summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Release credits summary is missing or invalid") from None
    if not isinstance(summary, dict):
        raise ValueError("Release credits summary must be a JSON object")
    if summary.get("status") != "complete":
        raise ValueError("Release credits did not pass completeness validation")
    if summary.get("scan_revision") != args.expected_revision:
        raise ValueError("Release credits were generated from a different commit")
    if summary.get("ta_version") != args.version:
        raise ValueError("Release credits use a different release version")

    filename = f"{settings['ta_name']}-{args.version}-credits.json"
    generated = args.artifacts / filename
    if not generated.is_file():
        raise ValueError("Versioned release credits JSON is missing")
    destination = args.ta_repo / "docs/thirdpartycredits" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = generated.read_bytes()
    if destination.exists() and destination.read_bytes() != content:
        raise ValueError("A different credits JSON already exists for this release version")

    index = _docs_index(args.ta_repo, settings)
    updated_index = _update_index(
        index, settings["display_name"], args.version, destination, args.ta_repo,
        settings.get("docs_section_heading", "Third-party software attributions"),
    )
    if not destination.exists():
        destination.write_bytes(content)
    if updated_index != index.read_text(encoding="utf-8"):
        index.write_text(updated_index, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ta-repo", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args(argv)
    try:
        prepare(args)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Cannot prepare release credits docs: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
