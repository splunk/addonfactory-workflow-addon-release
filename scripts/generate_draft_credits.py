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
"""Local settings, release identity, and safe FOSSA attribution retrieval."""

import argparse
import configparser
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Tuple

try:
    from .fossa_json_draft import build_draft
    from .fossa_json_common import REQUEST_OPTIONS, attribution_url, parse_json, validate_metadata
except ImportError:  # Direct execution: python /path/to/scripts/generate_draft_credits.py
    from fossa_json_draft import build_draft
    from fossa_json_common import REQUEST_OPTIONS, attribution_url, parse_json, validate_metadata


SETTINGS_FIELDS = {
    "schema_version",
    "fossa_project_locator",
    "ta_name",
    "display_name",
}


def _filename_identity(value: str) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is not None
    )


def load_settings(path: Path) -> dict:
    """Read the exact version-one settings contract owned by a TA checkout."""
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Cannot read valid FOSSA credits settings JSON") from None
    if not isinstance(settings, dict) or set(settings) != SETTINGS_FIELDS:
        raise ValueError(
            "FOSSA credits settings must contain exactly the required fields"
        )
    if type(settings["schema_version"]) is not int or settings["schema_version"] != 1:
        raise ValueError("FOSSA credits settings require schema_version 1")
    for key in SETTINGS_FIELDS - {"schema_version"}:
        value = settings[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("FOSSA credits settings require nonempty text fields")
    if not _filename_identity(settings["ta_name"]):
        raise ValueError("Invalid TA filename identity")
    return settings


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, ref: str) -> str:
    sha = _git(repo, "rev-parse", "--verify", ref + "^{commit}")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise ValueError("Selected revision must resolve to a 40-hex commit SHA")
    return sha.lower()


def _check_app_version(repo: Path, revision: str, version: str) -> None:
    try:
        contents = _git(repo, "show", revision + ":package/default/app.conf")
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(contents)
        actual = config.get("id", "version")
    except (subprocess.CalledProcessError, configparser.Error):
        raise ValueError(
            "Selected commit lacks a valid app.conf [id] version"
        ) from None
    if actual != version:
        raise ValueError(
            "Committed app.conf [id] version does not match requested TA version"
        )


def resolve_revision(repo: Path, version: str, override: Optional[str]) -> str:
    """Bind the release version to a tag or explicit immutable Git commit."""
    if not _filename_identity(version):
        raise ValueError("Invalid TA version filename identity")
    if override is not None and re.fullmatch(r"[0-9a-fA-F]{40}", override) is None:
        raise ValueError("Revision override must be a 40-hex commit SHA")
    try:
        try:
            tagged = _commit(repo, "refs/tags/v" + version)
        except subprocess.CalledProcessError:
            tagged = None
        selected = _commit(repo, override) if override is not None else tagged
        if tagged is not None and selected != tagged:
            raise ValueError(
                "Revision override must agree with the existing release tag"
            )
        if selected is None:
            raise ValueError(
                "Release tag unavailable; an explicit commit revision is required"
            )
        _check_app_version(repo, selected, version)
        return selected
    except (OSError, subprocess.CalledProcessError):
        raise ValueError("Cannot resolve the selected Git release revision") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "Redirect refused", headers, fp
        )


def fetch_attribution(
    project_locator: str, revision: str, token: str, opener=None
) -> bytes:
    """Fetch unchanged JSON bytes without forwarding authorization on redirects."""
    request = urllib.request.Request(
        attribution_url(project_locator, revision),
        headers={"Authorization": "Bearer " + token},
        method="GET",
    )
    if opener is None:
        opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=120) as response:
            if response.status != 200:
                raise ValueError("FOSSA attribution request did not return HTTP 200")
            return response.read()
    except (OSError, ValueError, http.client.HTTPException):
        # Server messages, URLs, response bodies and transport reasons can contain
        # credentials. Keep them out of diagnostics, including chained tracebacks.
        raise ValueError(
            "FOSSA attribution request failed (HTTP, redirect, or transport error)"
        ) from None


def _artifact_paths(settings: dict, version: str, output_dir: Path) -> Tuple[Path, Path, Path]:
    if not _filename_identity(settings.get("ta_name")) or not _filename_identity(version):
        raise ValueError("Invalid TA filename identity or version")
    prefix = settings["ta_name"] + "-" + version + "-"
    return tuple(output_dir / (prefix + suffix) for suffix in (
        "fossa-attribution.json", "fossa-attribution.request.json", "credits-draft.json",
    ))


def _temporary_bytes(output_dir: Path, contents: bytes) -> Path:
    with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".fossa-", delete=False) as stream:
        path = Path(stream.name)
        try:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise
    return path


def _replace_artifacts(paths: Tuple[Path, Path, Path], contents: tuple) -> None:
    """Prepare all files first and restore the previous set on a replace failure."""
    staged, backups, replaced = {}, {}, []
    try:
        for path, content in zip(paths, contents):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("Artifact destination must be a regular file")
            backups[path] = _temporary_bytes(path.parent, path.read_bytes()) if path.exists() else None
            staged[path] = _temporary_bytes(path.parent, content)
        try:
            for path in paths:
                os.replace(staged[path], path)
                replaced.append(path)
        except OSError:
            for path in reversed(replaced):
                if backups[path] is None:
                    path.unlink()
                else:
                    os.replace(backups[path], path)
            raise
    finally:
        for temporary in list(staged.values()) + list(backups.values()):
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def write_draft_artifacts(
    raw: bytes, settings: dict, version: str, revision: str, output_dir: Path,
    api_url: str, fetched_at: str,
) -> Tuple[Path, Path, Path]:
    """Bind unchanged response bytes, request metadata and draft before writing."""
    paths = _artifact_paths(settings, version, output_dir)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid selected revision")
    try:
        source = parse_json(raw)
    except (ValueError, UnicodeError):
        raise ValueError("Cannot parse FOSSA attribution JSON") from None
    digest = hashlib.sha256(raw).hexdigest()
    draft = build_draft(source, settings, version, revision, digest, paths[0].name, paths[1].name)
    metadata = {
        "ta_name": settings["ta_name"], "display_name": settings["display_name"],
        "ta_version": version, "fossa_project_locator": settings["fossa_project_locator"],
        "selected_revision": revision, "response_revision": source["project"]["revision"],
        "api_url": api_url, "fetched_at": fetched_at, "request_options": dict(REQUEST_OPTIONS),
        "raw_sha256": digest, "direct_dependencies": len(source["directDependencies"]),
        "deep_dependencies": len(source["deepDependencies"]),
    }
    validate_metadata(metadata, source, draft["provenance"])
    contents = (raw, _json_bytes(metadata), _json_bytes(draft))
    output_dir.mkdir(parents=True, exist_ok=True)
    _replace_artifacts(paths, contents)
    return paths


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _token(token_file: Optional[Path]) -> str:
    token = token_file.read_text(encoding="utf-8") if token_file else os.environ.get("FOSSA_API_KEY", "")
    token = token.strip()
    if not token or "\n" in token or "\r" in token:
        raise ValueError("A nonempty FOSSA token is required")
    return token


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default diagnostic echoes untrusted command-line values.
        raise ValueError("Invalid draft command arguments")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--ta-repo", type=Path, default=Path.cwd())
    parser.add_argument("--ta-version", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    try:
        args = parser.parse_args(argv)
        settings_path = args.ta_repo / ".fossa-credits.json"
        settings = load_settings(settings_path)
        revision = resolve_revision(args.ta_repo, args.ta_version, args.revision)
        token = _token(args.token_file)
        inputs = {settings_path.resolve()}
        if args.token_file:
            inputs.add(args.token_file.resolve())
        if any(path.resolve() in inputs for path in _artifact_paths(settings, args.ta_version, args.output_dir)):
            raise ValueError("Artifact path would overwrite an input")
        raw = fetch_attribution(settings["fossa_project_locator"], revision, token)
        write_draft_artifacts(
            raw, settings, args.ta_version, revision, args.output_dir,
            attribution_url(settings["fossa_project_locator"], revision),
            datetime.now(timezone.utc).isoformat(),
        )
    except (OSError, ValueError, TypeError):
        # Do not render exception text: even local filenames or server payloads
        # may contain a credential. Stable diagnostics cover all input sources.
        print("Cannot generate FOSSA credits draft: check settings, release revision, token, report and output paths.", file=sys.stderr)
        return 2
    print("Wrote FOSSA attribution, request metadata and credits draft.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
