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
"""Prefill a working draft from release-local resolutions before agent research."""

import argparse
from pathlib import Path
import sys

try:
    from .fossa_json_final import _read_json, build_credits, prefill_draft, verify_provenance
    from .fossa_review_queue import pending_reviews
    from .generate_draft_credits import _json_bytes, _replace_artifacts
except ImportError:  # Direct script execution.
    from fossa_json_final import _read_json, build_credits, prefill_draft, verify_provenance
    from fossa_review_queue import pending_reviews
    from generate_draft_credits import _json_bytes, _replace_artifacts


def resolve_gaps(draft_path: Path, config_path=None):
    """Verify raw evidence, prefill the working draft, and return the remaining queue inputs."""
    _, working = _read_json(draft_path)
    canonical = verify_provenance(working, draft_path)
    config_path = config_path or draft_path.parent / "fossa-license-resolutions.json"
    inputs = [draft_path, *(draft_path.parent / canonical["provenance"][field]
                           for field in ("raw_report_file", "request_metadata_file"))]
    if config_path.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("Resolution configuration overlaps a generated input")
    _, config = _read_json(config_path)
    working = prefill_draft(canonical, config)
    credits, diagnostics, status = build_credits(canonical, config)
    _replace_artifacts((draft_path,), (_json_bytes(working),))
    return credits, diagnostics, status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--config", type=Path,
                        help="Defaults to fossa-license-resolutions.json beside the draft")
    args = parser.parse_args(argv)
    try:
        credits, diagnostics, status = resolve_gaps(args.draft, args.config)
        pending = pending_reviews(credits, diagnostics)
    except (ValueError, OSError, TypeError, KeyError, OverflowError) as error:
        print(f"Cannot resolve credits gaps: {error}", file=sys.stderr)
        return 2
    for name, version, fields in pending:
        print(f"{name} {version}: {', '.join(fields)}")
    for diagnostic in diagnostics:
        print(diagnostic, file=sys.stderr)
    print(f"{len(pending)} components need review; {len(diagnostics)} generator diagnostics",
          file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
