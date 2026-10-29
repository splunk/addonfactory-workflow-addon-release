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
"""Generate final public credits offline from a verified FOSSA JSON draft."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import List, Optional

try:
    from .fossa_json_final import _read_json, build_credits, load_config, verify_provenance
except ImportError:  # Direct script execution.
    from fossa_json_final import _read_json, build_credits, load_config, verify_provenance


def _output_path(draft, output_dir):
    version = draft["version"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("Invalid TA version filename identity")
    provenance = draft["provenance"]
    suffix = f"-{version}-fossa-attribution.json"
    raw_name = provenance["raw_report_file"]
    if not raw_name.endswith(suffix):
        raise ValueError("Raw report filename does not match TA version")
    ta_name = raw_name[:-len(suffix)]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", ta_name):
        raise ValueError("Invalid TA name filename identity")
    if provenance["request_metadata_file"] != f"{ta_name}-{version}-fossa-attribution.request.json":
        raise ValueError("Provenance filenames do not match TA identity")
    return output_dir / f"{ta_name}-{version}-credits.json"


def _write_credits(output, credits):
    payload = json.dumps(credits, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=f".{output.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _generate(args):
    _, draft = _read_json(args.draft)
    verify_provenance(draft, args.draft)
    config = load_config(args.config)
    output = _output_path(draft, args.output_dir)
    inputs = [args.draft, args.config]
    inputs.extend(args.draft.parent / draft["provenance"][field]
                  for field in ("raw_report_file", "request_metadata_file"))
    if output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("Final output would overwrite an input")
    credits, diagnostics, status = build_credits(draft, config)
    _write_credits(output, credits)
    for diagnostic in diagnostics:
        print(diagnostic, file=sys.stderr)
    return status


def main(argv: Optional[List[str]] = None) -> int:
    """Write credits on completeness status 0/1; leave prior output on invalid input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", default=Path.cwd(), type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code
    try:
        return _generate(args)
    except (ValueError, OSError, TypeError, KeyError, OverflowError) as error:
        print(f"Invalid input or processing failure: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
