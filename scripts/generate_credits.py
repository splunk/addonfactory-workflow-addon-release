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
import tarfile
from typing import List, Optional

try:
    from .fossa_json_final import _read_json, build_credits, load_config, verify_provenance, _artifact_decision, artifact_python_additions, resolution_for_component
    from .fossa_artifact_scope import inspect_artifact, reconcile_scope, component_ecosystem
except ImportError:  # Direct script execution.
    from fossa_json_final import _read_json, build_credits, load_config, verify_provenance, _artifact_decision, artifact_python_additions, resolution_for_component
    from fossa_artifact_scope import inspect_artifact, reconcile_scope, component_ecosystem


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


def _scope_receipt(draft, config, artifact):
    rules = {(r["name"], r["version"]): r for r in config["resolutions"]}
    excluded = {(r["name"], r["version"]): r for r in config["exclusions"]}
    components, decisions = [], []
    for value in draft["components"] + config.get("additions", []) + artifact_python_additions(draft, config, artifact):
        identity = (value["name"], value["version"])
        rule = value if "reason" in value else resolution_for_component(value, rules)
        component = dict(name=rule.get("public_name", value["name"]), version=value["version"],
                         _source_names=[value["name"]] + value.get("_source_names", []),
                         _ecosystem=rule.get("ecosystem") or component_ecosystem(value))
        components.append(component)
        decisions.append(_artifact_decision(component, rule, excluded.get(identity), artifact))
    receipt = reconcile_scope(components, artifact)
    receipt["decisions"] = decisions
    return receipt


def _check_output(args, draft, config_path):
    output = _output_path(draft, args.output_dir or Path.cwd())
    inputs = [args.draft, config_path]
    inputs.extend(args.draft.parent / draft["provenance"][field]
                  for field in ("raw_report_file", "request_metadata_file"))
    if args.artifact:
        inputs.append(args.artifact)
    if output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("Final output would overwrite an input")
    return output


def _generate(args):
    if args.mode == "validate" and args.output_dir is not None:
        raise ValueError("validate does not accept --output-dir")
    _, draft = _read_json(args.draft)
    draft = verify_provenance(draft, args.draft)
    config_path = args.config or args.draft.parent / "fossa-license-resolutions.json"
    config = load_config(config_path)
    identity_path = _output_path(draft, Path.cwd())
    artifact = None
    if args.artifact:
        app_name = identity_path.name[:-len(f"-{draft['version']}-credits.json")]
        artifact = inspect_artifact(args.artifact, expected_version=draft["version"], expected_app=app_name)
    credits, diagnostics, status = build_credits(draft, config, artifact=artifact)
    if artifact is None:
        diagnostics.append("Warning: artifact scope not checked; supply --artifact for shipping reconciliation")
    if args.mode == "generate":
        _write_credits(_check_output(args, draft, config_path), credits)
    for diagnostic in diagnostics:
        print(diagnostic, file=sys.stderr)
    if args.json:
        print(json.dumps(dict(status=status, diagnostics=diagnostics,
                              scope=_scope_receipt(draft, config, artifact) if artifact else None),
                         indent=2, ensure_ascii=False, allow_nan=False))
    return status


def main(argv: Optional[List[str]] = None) -> int:
    """Write credits on completeness status 0/1; leave prior output on invalid input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("generate", "validate"), nargs="?", default="generate")
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--config", type=Path,
                        help="Defaults to fossa-license-resolutions.json beside the draft")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--artifact", type=Path, help="Existing release SPL; no build or scan is run")
    parser.add_argument("--json", action="store_true", help="Print diagnostics and artifact evidence to stdout")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code
    try:
        return _generate(args)
    except (ValueError, OSError, TypeError, KeyError, OverflowError, tarfile.TarError, EOFError) as error:
        print(f"Invalid input or processing failure: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
