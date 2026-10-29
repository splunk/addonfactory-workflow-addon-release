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
"""Offline validation of FOSSA JSON drafts and reviewed exact-version rules."""

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import List, Tuple

try:
    from .fossa_json_draft import GROUPS, COMPOUND, PLACEHOLDER, build_component, build_draft, has_copyright_boilerplate, has_unfilled_copyright_slot
    from .fossa_json_common import parse_json, validate_metadata
except ImportError:  # Direct script imports.
    from fossa_json_draft import GROUPS, COMPOUND, PLACEHOLDER, build_component, build_draft, has_copyright_boilerplate, has_unfilled_copyright_slot
    from fossa_json_common import parse_json, validate_metadata


REVIEW_FIELDS = {"license", "copyright_holder", "license_text"}
DRAFT_FIELDS = {"name", "version", "license", "copyright_holder", "license_text",
                "licenses_to_verify", "licenses_text_to_verify", "project_url", "notices"}
FINAL_FIELDS = DRAFT_FIELDS - {"licenses_to_verify", "licenses_text_to_verify"}
PUBLIC_NOTICE_FIELDS = {"name", "license", "copyright_holder", "license_text", "notice_text"}
NOTICE_APPLICABILITY = {"confirmed", "unresolved", "not_shipped"}
PROVENANCE_FIELDS = {"fossa_project_locator", "revision", "raw_sha256", "raw_report_file",
                     "request_metadata_file"}
EMPTY_VALUES = {"none", "n/a", "noassertion", "unknown", "tbd"}


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - set(optional):
        raise ValueError("Object has missing or unknown fields")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")
    return value.strip()


def _array(value):
    if not isinstance(value, list):
        raise ValueError("Expected an array")
    return value


def _strings(value):
    for item in _array(value):
        _text(item)


def _read_json(path):
    try:
        raw = path.read_bytes()
        value = parse_json(raw)
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Cannot read valid UTF-8 JSON") from None
    return raw, value


def _identity(rule):
    name, version = _text(rule["name"]), _text(rule["version"])
    if "*" in name or re.search(r"[\s*?^~<>=|,]", version):
        raise ValueError("Expected exact component name and version")
    return name, version


def _override(field, value):
    # An explicit empty copyright override records a completed upstream review
    # that found no holder declaration. Missing/unreviewed fields remain TBD.
    if field == "copyright_holder" and value == "":
        return
    text = _text(value)
    if text.casefold() in EMPTY_VALUES:
        raise ValueError("Override must resolve the field")
    if field == "license" and (COMPOUND.search(text) or ";" in text or text.lower().startswith("multi-license:")):
        raise ValueError("Expected a single license")
    if field == "copyright_holder" and PLACEHOLDER.search(text):
        raise ValueError("Unresolved copyright placeholder")
    if field == "copyright_holder" and has_copyright_boilerplate(text):
        raise ValueError("Copyright holder contains license boilerplate")
    if field == "license_text" and has_unfilled_copyright_slot(text):
        raise ValueError("Unresolved license text placeholder")


def _notices(value):
    names = set()
    for notice in _array(value):
        _fields(notice, {"name", "source", "reason"} | REVIEW_FIELDS,
                {"notice_text", "applicability"})
        name = _text(notice["name"])
        if name in names:
            raise ValueError("Duplicate notice name")
        names.add(name)
        _text(notice["source"])
        _text(notice["reason"])
        applicability = notice.get("applicability", "confirmed")
        if not isinstance(applicability, str) or applicability not in NOTICE_APPLICABILITY:
            raise ValueError("Invalid notice applicability")
        for field in REVIEW_FIELDS:
            if notice[field] != "TBD":
                _override(field, notice[field])
        if "notice_text" in notice and not isinstance(notice["notice_text"], str):
            raise ValueError("Notice text must be text")


def _evidence(value):
    _fields(value, {"candidates", "copyright_sources", "distribution_findings"})
    identities = set()
    for candidate in _array(value["candidates"]):
        _fields(candidate, {"source_group", "name", "disposition", "reason", "references"})
        identity = (_text(candidate["source_group"]), _text(candidate["name"]).casefold())
        if candidate["source_group"] not in GROUPS or identity in identities:
            raise ValueError("Invalid or duplicate native candidate")
        identities.add(identity)
        if candidate["disposition"] not in ("main", "supporting", "inapplicable", "unresolved"):
            raise ValueError("Invalid candidate disposition")
        _text(candidate["reason"])
        _references(candidate["references"])
    _strings(value["copyright_sources"])
    for finding in _array(value["distribution_findings"]):
        _fields(finding, {"finding", "references"})
        _text(finding["finding"])
        _references(finding["references"])


def _references(value):
    _strings(value)
    if not value:
        raise ValueError("At least one source reference is required")


def _rules(values, kind):
    seen = set()
    required = {"name", "version", "reason"}
    optional = set()
    if kind == "resolutions":
        required |= {"source"}
        optional = REVIEW_FIELDS | {"notices", "evidence"}
    for rule in _array(values):
        _fields(rule, required, optional)
        identity = _identity(rule)
        if identity in seen:
            raise ValueError("Duplicate exact-version rule")
        seen.add(identity)
        for field in required:
            _text(rule[field])
        if kind == "resolutions":
            _resolution(rule)
    return seen


def _resolution(rule):
    if not (rule.keys() & (REVIEW_FIELDS | {"notices"})):
        raise ValueError("Resolution requires at least one override")
    for field in rule.keys() & REVIEW_FIELDS:
        _override(field, rule[field])
    if "notices" in rule:
        _notices(rule["notices"])
    if "evidence" in rule:
        _evidence(rule["evidence"])


def load_config(path: Path) -> dict:
    """Return validated schema-version-one configuration, including legacy rules."""
    _, data = _read_json(path)
    _validate_config(data)
    return data


def _validate_config(data):
    _fields(data, {"schema_version", "resolutions", "exclusions"})
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Expected schema_version 1")
    resolutions = _rules(data["resolutions"], "resolutions")
    exclusions = _rules(data["exclusions"], "exclusions")
    if resolutions & exclusions:
        raise ValueError("Resolution and exclusion rules overlap")


def _candidates(value, text=False):
    _fields(value, set(GROUPS))
    for group in GROUPS:
        for item in _array(value[group]):
            if text:
                _fields(item, {"name", "text"})
                _text(item["name"])
                _text(item["text"])
            else:
                _text(item)


def _draft_component(component):
    _fields(component, DRAFT_FIELDS | {"fossa_evidence"})
    for field in {"name", "version"} | REVIEW_FIELDS:
        _text(component[field])
    if component["project_url"] is not None and not isinstance(component["project_url"], str):
        raise ValueError("Invalid project URL")
    _candidates(component["licenses_to_verify"])
    _candidates(component["licenses_text_to_verify"], text=True)
    _notices(component["notices"])
    evidence = component["fossa_evidence"]
    _fields(evidence, {"dependency_kind", "package", "source", "downloadUrl", "dependencyPaths",
                       "licenses", "otherLicenses", "noticeFiles"})
    # Reuse the native dependency validator without importing the CSV processor.
    dependency = dict(evidence, title=component["name"], version=component["version"],
                      projectUrl=component["project_url"])
    build_component(dependency, evidence["dependency_kind"])


def _basename(value):
    name = _text(value)
    if name != value or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("Provenance filenames must be sibling basenames")
    return name


def _provenance(value):
    _fields(value, PROVENANCE_FIELDS)
    _text(value["fossa_project_locator"])
    for field, size in (("revision", 40), ("raw_sha256", 64)):
        if not isinstance(value[field], str) or not re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value[field]):
            raise ValueError("Invalid provenance revision or SHA-256")
    for field in ("raw_report_file", "request_metadata_file"):
        _basename(value[field])
    if value["raw_report_file"] == value["request_metadata_file"]:
        raise ValueError("Provenance siblings must be distinct")


def validate_draft(draft: dict) -> None:
    """Require the exact draft contract and unique exact component identities."""
    _fields(draft, {"name", "version", "disclaimer", "components", "provenance"})
    for field in ("name", "version", "disclaimer"):
        _text(draft[field])
    _provenance(draft["provenance"])
    identities = set()
    for component in _array(draft["components"]):
        _draft_component(component)
        identity = (component["name"], component["version"])
        if identity in identities:
            raise ValueError("Duplicate component identity")
        identities.add(identity)


def _sibling(parent, filename):
    path = parent / _basename(filename)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Provenance sibling must be a regular file")
    return path


def verify_provenance(draft: dict, draft_path: Path) -> None:
    """Bind the entire draft to unchanged sibling raw bytes and request metadata.

    This checks local consistency, not authenticity against a remote service or
    a signature: a replacement of the complete artifact set is not detectable.
    """
    validate_draft(draft)
    provenance = draft["provenance"]
    raw_path = _sibling(draft_path.parent, provenance["raw_report_file"])
    metadata_path = _sibling(draft_path.parent, provenance["request_metadata_file"])
    raw, report = _read_json(raw_path)
    _, metadata = _read_json(metadata_path)
    if hashlib.sha256(raw).hexdigest() != provenance["raw_sha256"]:
        raise ValueError("Raw report SHA-256 mismatch")
    # Build first so report shape errors consistently raise ValueError before
    # accessing its project/dependency fields for the metadata comparisons.
    _fields(metadata, {"ta_name", "display_name", "ta_version", "fossa_project_locator"},
            {"selected_revision", "response_revision", "fetched_at", "request_options",
             "raw_sha256", "direct_dependencies", "deep_dependencies", "api_url"})
    expected = build_draft(report, metadata, metadata["ta_version"], provenance["revision"],
                           provenance["raw_sha256"], raw_path.name, metadata_path.name)
    validate_metadata(metadata, report, provenance)
    if json.dumps(draft, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        raise ValueError("Draft differs from rederived authoritative report")


def _apply_rule(component, rule):
    result = {key: deepcopy(value) for key, value in component.items() if key in FINAL_FIELDS}
    for field in sorted(REVIEW_FIELDS | {"notices"}):
        if field in rule:
            result[field] = deepcopy(rule[field])
    result["notices"] = [
        {key: value for key, value in notice.items() if key in PUBLIC_NOTICE_FIELDS}
        for notice in result["notices"]
        if notice.get("applicability", "confirmed") != "not_shipped"
    ]
    return result


def _candidate_reasons(native, evidence):
    expected = {(group, entry["name"].strip().casefold())
                for group in GROUPS for entry in native[group]}
    decisions = {(entry["source_group"], entry["name"].strip().casefold())
                 for entry in evidence["candidates"]}
    reasons = []
    for group, name in sorted(expected - decisions):
        reasons.append(f"missing candidate decision: {group}/{name}")
    for group, name in sorted(decisions - expected):
        reasons.append(f"extra candidate decision: {group}/{name}")
    for entry in evidence["candidates"]:
        if entry["disposition"] == "unresolved":
            reasons.append(f"unresolved candidate: {entry['source_group']}/{entry['name']}: {entry['reason']}")
    return reasons


def _notice_applicability_reasons(rule):
    return [f"notice {notice['name']}: applicability unresolved: {notice['reason']}"
            for notice in rule.get("notices", [])
            if notice.get("applicability") == "unresolved"]


def _component_reasons(component, result, rule):
    reasons = [f"{field} is TBD" for field in sorted(REVIEW_FIELDS) if result[field] == "TBD"]
    if result["copyright_holder"] != "TBD" and has_copyright_boilerplate(result["copyright_holder"]):
        reasons.append("copyright_holder contains license boilerplate; review exact upstream artifact")
    if result["license_text"] != "TBD" and has_unfilled_copyright_slot(result["license_text"]):
        reasons.append("license_text contains an unfilled copyright holder; review exact upstream artifact")
    for notice in result["notices"]:
        for field in sorted(REVIEW_FIELDS | {"notice_text"}):
            if notice.get(field) == "TBD":
                reasons.append(f"notice {notice['name']}: {field} is TBD")
        if notice["license_text"] != "TBD" and has_unfilled_copyright_slot(notice["license_text"]):
            reasons.append(f"notice {notice['name']}: license_text contains an unfilled copyright holder")
    reasons.extend(_notice_applicability_reasons(rule))
    native = component["fossa_evidence"]
    if any(not notice["ignored"] for notice in native["noticeFiles"]) and "notices" not in rule:
        reasons.append("notice review required: unignored FOSSA noticeFiles need an explicit notices rule")
    if "evidence" in rule:
        reasons.extend(_candidate_reasons(native, rule["evidence"]))
    return reasons


def build_credits(draft: dict, config: dict) -> Tuple[dict, List[str], int]:
    """Apply exact-version research, returning public credits and completeness.

    Callers reading drafts from disk must verify_provenance before using this
    pure transformation. Warnings and exclusions do not make a report incomplete.
    """
    validate_draft(draft)
    _validate_config(config)
    resolutions = {(rule["name"], rule["version"]): rule for rule in config["resolutions"]}
    exclusions = {(rule["name"], rule["version"]): rule for rule in config["exclusions"]}
    seen = set()
    diagnostics = []
    components = []
    status = 0
    for component in draft["components"]:
        identity = (component["name"], component["version"])
        seen.add(identity)
        label = f"{identity[0]} {identity[1]}"
        if identity in exclusions:
            diagnostics.append(f"Excluded {label}: {exclusions[identity]['reason']}")
            continue
        rule = resolutions.get(identity, {})
        result = _apply_rule(component, rule)
        reasons = _component_reasons(component, result, rule)
        diagnostics.extend(f"Unresolved {label}: {reason}" for reason in reasons)
        if reasons:
            status = 1
        components.append(result)
    for kind, rules in (("resolution", resolutions), ("exclusion", exclusions)):
        for name, version in sorted(rules.keys() - seen):
            diagnostics.append(f"Warning: unmatched {kind} rule: {name} {version}")
    credits = {field: draft[field] for field in ("name", "version", "disclaimer")}
    credits["components"] = components
    return credits, diagnostics, status
