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
"""Generate FOSSA credits artifacts from one verified scan run."""

import argparse
import configparser
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from .fossa_json_common import attribution_url
    from .fossa_json_final import build_credits, load_config, verify_provenance, _read_json
    from .fossa_review_queue import pending_reviews
    from .generate_credits import _write_credits
    from .generate_draft_credits import fetch_attribution, load_settings, write_draft_artifacts
except ImportError:  # Direct script execution.
    from fossa_json_common import attribution_url
    from fossa_json_final import build_credits, load_config, verify_provenance, _read_json
    from fossa_review_queue import pending_reviews
    from generate_credits import _write_credits
    from generate_draft_credits import fetch_attribution, load_settings, write_draft_artifacts


EMPTY_CONFIG = {"schema_version": 1, "resolutions": [], "exclusions": []}


def _validate_ci_config(config: dict) -> None:
    if config.get("exclusions") or config.get("additions"):
        raise ValueError(
            "CI credits generation does not support component exclusions or additions; "
            "package inventory comparison is outside this workflow"
        )


def _scan_identity(path: Path, repo: Path) -> tuple[dict, str, str]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        revision = metadata["revision"]
        locator = metadata["projectId"]
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise ValueError("Cannot read valid FOSSA scan metadata") from None
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("FOSSA scan metadata has no immutable 40-character revision")
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError("FOSSA scan metadata has no project locator")
    if metadata.get("id") != f"{locator}${revision}":
        raise ValueError("FOSSA scan metadata locator/revision identity is inconsistent")
    try:
        checked_out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip().lower()
    except (OSError, subprocess.CalledProcessError):
        raise ValueError("Cannot identify the TA checkout used by this CI job") from None
    if checked_out != revision.lower():
        raise ValueError(
            "The checked-out TA commit does not match the FOSSA scan revision"
        )
    return metadata, revision.lower(), locator


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ta_version(repo: Path, requested: Optional[str]) -> str:
    if requested:
        return requested
    app_conf = configparser.ConfigParser(interpolation=None)
    try:
        app_conf.read(repo / "package/default/app.conf", encoding="utf-8")
        version = app_conf.get("id", "version")
    except (OSError, configparser.Error):
        raise ValueError("Cannot determine TA version from package/default/app.conf") from None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("TA version is not a safe credits filename identity")
    return version


def generate(args) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_path = args.output_dir / "credits-diagnostics.txt"
    queue_path = args.output_dir / "credits-review-queue.txt"
    summary_path = args.output_dir / "credits-summary.json"
    try:
        metadata, revision, locator = _scan_identity(args.scan_metadata, args.ta_repo)
        version = _ta_version(args.ta_repo, args.ta_version)
        settings = load_settings(args.ta_repo / ".fossa-credits.json")
        configured_locator = settings.get("fossa_project_locator")
        if configured_locator and configured_locator != locator:
            raise ValueError("Configured FOSSA project locator differs from this scan")
        settings["fossa_project_locator"] = locator
        raw = fetch_attribution(locator, revision, args.fossa_api_key)
        _, _, draft_path = write_draft_artifacts(
            raw, settings, version, revision, args.output_dir,
            attribution_url(locator, revision), datetime.now(timezone.utc).isoformat(),
        )
        canonical = verify_provenance(_read_json(draft_path)[1], draft_path)
        config_path = args.resolutions
        if config_path is None or not config_path.is_file():
            config_path = args.output_dir / "fossa-license-resolutions.json"
            _write(config_path, json.dumps(EMPTY_CONFIG, indent=2) + "\n")
        config = load_config(config_path)
        _validate_ci_config(config)
        credits, diagnostics, status = build_credits(canonical, config)
        _write_credits(args.output_dir / f"{settings['ta_name']}-{version}-credits.json", credits)
        queue = pending_reviews(credits, diagnostics)
        _write(queue_path, "".join(
            f"{name} {version}: {', '.join(fields)}\n"
            for name, version, fields in queue
        ))
        _write(diagnostics_path, "".join(item + "\n" for item in diagnostics))
        result = {
            "status": "complete" if status == 0 else "unresolved",
            "scan_revision": revision,
            "fossa_project_locator": locator,
            "fossa_report_url": metadata.get("url"),
            "ta_version": version,
            "unresolved_count": len(queue),
            "diagnostic_count": len(diagnostics),
        }
        _write(summary_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        return status
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        _write(diagnostics_path, f"Credits generation failed: {error}\n")
        _write(queue_path, "Generation failed before a review queue could be produced.\n")
        _write(summary_path, json.dumps({"status": "error"}, indent=2) + "\n")
        print(f"Credits generation failed: {error}", file=sys.stderr)
        return 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ta-repo", type=Path, default=Path.cwd())
    parser.add_argument("--ta-version", help="Release version; omitted on PRs to use package/default/app.conf")
    parser.add_argument("--scan-metadata", required=True, type=Path)
    parser.add_argument("--resolutions", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    args.fossa_api_key = os.environ.get("FOSSA_API_KEY", "").strip()
    if not args.fossa_api_key:
        print("Credits generation failed: FOSSA_API_KEY is required", file=sys.stderr)
        return 2
    return generate(args)


if __name__ == "__main__":
    sys.exit(main())
