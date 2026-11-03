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
import re
from copy import deepcopy
from pathlib import Path
from typing import List, Tuple

try:
    from .fossa_credits_validation import validate_public_credits, valid_project_url
    from .fossa_artifact_scope import scope_decision, component_ecosystem, python_metadata_additions
except ImportError:
    from fossa_credits_validation import validate_public_credits, valid_project_url
    from fossa_artifact_scope import scope_decision, component_ecosystem, python_metadata_additions

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
        optional = REVIEW_FIELDS | {"notices", "evidence", "scope", "public_name", "project_url", "ecosystem"}
    else:
        optional = {"artifact_sha256", "references"}
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
        elif "artifact_sha256" in rule:
            _scope(dict(disposition="not_shipped", reason=rule["reason"],
                        artifact_sha256=rule["artifact_sha256"], references=rule.get("references", [])))
    return seen


def _resolution(rule):
    if not (rule.keys() & (REVIEW_FIELDS | {"notices", "scope", "public_name", "project_url", "ecosystem"})):
        raise ValueError("Resolution requires at least one override")
    for field in rule.keys() & REVIEW_FIELDS:
        _override(field, rule[field])
    if "notices" in rule:
        _notices(rule["notices"])
    if "evidence" in rule:
        _evidence(rule["evidence"])
    if "scope" in rule:
        _scope(rule["scope"])
    if "public_name" in rule:
        name = _text(rule["public_name"])
        if re.search(r"^[\w+.-]+://|^git@[^:]+:", name):
            raise ValueError("Public name must not be a transport locator")
    if "project_url" in rule and not valid_project_url(rule["project_url"]):
        raise ValueError("Expected a public HTTP(S) project URL")
    if "ecosystem" in rule:
        _text(rule["ecosystem"])


def _scope(value):
    _fields(value, {"disposition", "artifact_sha256", "reason", "references"})
    if value["disposition"] not in ("present", "not_shipped", "unknown"):
        raise ValueError("Invalid scope disposition")
    if not isinstance(value["artifact_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["artifact_sha256"]):
        raise ValueError("Invalid scope artifact checksum")
    _text(value["reason"])
    _references(value["references"])


def _additions(values):
    required = FINAL_FIELDS | {"reason", "source"}
    seen = set()
    for rule in _array(values):
        _fields(rule, required, {"scope", "ecosystem"})
        identity = _identity(rule)
        if identity in seen:
            raise ValueError("Duplicate exact-version addition")
        seen.add(identity)
        _text(rule["reason"])
        _text(rule["source"])
        if rule["project_url"] is not None:
            _text(rule["project_url"])
        for field in REVIEW_FIELDS:
            if rule[field] != "TBD":
                _override(field, rule[field])
        _notices(rule["notices"])
        if "scope" in rule:
            _scope(rule["scope"])
        if "ecosystem" in rule:
            _text(rule["ecosystem"])
    return seen


def load_config(path: Path) -> dict:
    """Return validated schema-version-one configuration, including legacy rules."""
    _, data = _read_json(path)
    _validate_config(data)
    return data


def _validate_config(data):
    _fields(data, {"schema_version", "resolutions", "exclusions"}, {"additions"})
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Expected schema_version 1")
    resolutions = _rules(data["resolutions"], "resolutions")
    exclusions = _rules(data["exclusions"], "exclusions")
    if resolutions & exclusions:
        raise ValueError("Resolution and exclusion rules overlap")
    additions = _additions(data.get("additions", []))
    if additions & (resolutions | exclusions):
        raise ValueError("Addition overlaps a resolution or exclusion")


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
        if field == "copyright_holder" and component[field] == "":
            continue
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


def prefill_draft(draft: dict, config: dict) -> dict:
    """Apply exact reviewed fields while preserving the entire FOSSA inventory/evidence."""
    validate_draft(draft)
    _validate_config(config)
    rules = {(rule["name"], rule["version"]): rule for rule in config["resolutions"]}
    working = deepcopy(draft)
    for component in working["components"]:
        rule = rules.get((component["name"], component["version"]), {})
        for field in REVIEW_FIELDS | {"notices"}:
            if field in rule:
                component[field] = deepcopy(rule[field])
    return working


def verify_provenance(draft: dict, draft_path: Path) -> dict:
    """Verify raw or prefilled input, returning the canonical FOSSA-only draft.

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
    if draft["name"] != expected["name"] or draft["version"] != expected["version"]:
        raise ValueError("Draft release identity differs from request metadata")
    # Working drafts can be prefilled. Their legal fields are not authoritative:
    # use raw evidence plus the latest resolutions for every queue/final run.
    return expected


def _apply_rule(component, rule):
    result = {key: deepcopy(value) for key, value in component.items() if key in FINAL_FIELDS}
    for field in sorted(REVIEW_FIELDS | {"notices"}):
        if field in rule:
            result[field] = deepcopy(rule[field])
    if "public_name" in rule:
        result["name"] = rule["public_name"]
    if "project_url" in rule:
        result["project_url"] = rule["project_url"]
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


def _addition_reasons(addition, component):
    reasons = [f"{field} is TBD" for field in sorted(REVIEW_FIELDS)
               if component[field] == "TBD"]
    for notice in component["notices"]:
        reasons.extend(f"notice {notice['name']}: {field} is TBD"
                       for field in sorted(REVIEW_FIELDS | {"notice_text"})
                       if notice.get(field) == "TBD")
    reasons.extend(_notice_applicability_reasons(addition))
    return reasons


def _append_additions(components, diagnostics, seen, additions):
    status = 0
    for addition in additions:
        identity = (addition["name"], addition["version"])
        if identity in seen:
            if not addition.get("_from_python_metadata"):
                raise ValueError(f"Addition already appears in FOSSA draft: {identity[0]} {identity[1]}")
            diagnostics.append(f"Unresolved {identity[0]} {identity[1]}: [PYTHON_IDENTITY_COLLISION] "
                               "Python distribution shares another ecosystem's public identity; "
                               "set a distinct public_name in an ecosystem: pip resolution")
            status = 1
        label = f"{identity[0]} {identity[1]}"
        component = _apply_rule(addition, {})
        components.append(component)
        diagnostics.append(f"Added {label}: {addition['reason']} (Source: {addition['source']})")
        reasons = _addition_reasons(addition, component)
        diagnostics.extend(f"Unresolved {label}: {reason}" for reason in reasons)
        if reasons:
            status = 1
    return status


def _version_review_reasons(identity, config):
    """Prior versions seed upstream research; they never override another version."""
    configured = {(rule["name"], rule["version"])
                  for kind in ("resolutions", "additions", "exclusions")
                  for rule in config.get(kind, [])}
    if identity in configured:
        return []
    versions = sorted(version for name, version in configured if name == identity[0])
    if not versions:
        return []
    return ["version review required: configuration exists for " + ", ".join(versions)
            + "; verify this version's upstream license, notices and TA scope, then add an exact-version rule"]


def _artifact_decision(component, rule, exclusion, artifact, source_name=None, ecosystem=None):
    reviewed = rule.get("scope")
    if exclusion and "artifact_sha256" in exclusion:
        reviewed = dict(disposition="not_shipped", artifact_sha256=exclusion["artifact_sha256"],
                        reason=exclusion["reason"], references=exclusion["references"])
    candidate = dict(component)
    candidate["_ecosystem"] = rule.get("ecosystem") or ecosystem or component_ecosystem(component)
    if source_name:
        candidate["_source_names"] = [source_name]
    decision = scope_decision(candidate, artifact, reviewed)
    if exclusion and "artifact_sha256" not in exclusion and decision.get("policy") != "python-dist-info":
        decision.update(disposition="unknown", reason="[SCOPE_EXCLUSION] exclusion has no artifact checksum binding")
    return decision


def resolution_for_component(component, rules):
    """An explicit ecosystem selector cannot override a different known one."""
    rule = rules.get((component["name"], component["version"]), {})
    selected, actual = rule.get("ecosystem"), component_ecosystem(component)
    if selected and actual not in (None, "git", selected):
        return {}
    return rule


def artifact_python_additions(draft, config, artifact):
    """Create reviewable rows for dist-info identities missing from the inputs."""
    if artifact is None:
        return []
    rules = {(r["name"], r["version"]): r for r in config["resolutions"]}
    represented = []
    for value in draft["components"] + config.get("additions", []):
        rule = value if "reason" in value else resolution_for_component(value, rules)
        represented.append(dict(name=rule.get("public_name", value["name"]), version=value["version"],
                                _source_names=[value["name"]],
                                _ecosystem=rule.get("ecosystem") or component_ecosystem(value)))
    sources = {(c["name"], c["version"]): component_ecosystem(c) for c in draft["components"]}
    python_rules = {identity: rule for identity, rule in rules.items()
                    if (rule.get("ecosystem") or sources.get(identity) or component_ecosystem(rule))
                    in (None, "pip", "git")}
    return [_metadata_addition(observation, python_rules, artifact["sha256"])
            for observation in python_metadata_additions(represented, artifact)]


def _metadata_addition(observation, rules, digest):
    name, version = observation["name"], observation["version"]
    matches = [rule for (rule_name, rule_version), rule in rules.items()
               if re.sub(r"[-_.]+", "-", rule_name).lower() == re.sub(r"[-_.]+", "-", name).lower()
               and rule_version.removeprefix("v") == version.removeprefix("v")]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous Python metadata resolution: {name} {version}")
    rule = matches[0] if matches else {}
    addition = dict(name=name, version=version, license="TBD", copyright_holder="TBD",
                    license_text="TBD", project_url=None, notices=[])
    addition.update({field: rule[field] for field in FINAL_FIELDS - {"name", "version"} if field in rule})
    addition.update(name=rule.get("public_name", name), ecosystem="pip", _source_names=[name],
                    _from_python_metadata=True,
                    reason="Python distribution found in packaged metadata but absent from input rows",
                    source=f"SPL SHA256 {digest}; " + "; ".join(o["path"] for o in observation["evidence"]))
    return addition


def _publication_diagnostics(credits, labels):
    findings = validate_public_credits(credits)
    diagnostics = []
    for finding in findings:
        prefix = "Warning: " if finding.severity == "warning" else "Unresolved "
        if finding.component_index is not None:
            prefix += labels[finding.component_index] + ": "
        if finding.notice_index is not None:
            notice = credits["components"][finding.component_index]["notices"][finding.notice_index]
            prefix += f"notice {notice.get('name', finding.notice_index)}: "
        diagnostics.append(f"{prefix}[{finding.code}] {finding.message}")
    return diagnostics, int(any(f.severity == "error" for f in findings))


def build_credits(draft: dict, config: dict, artifact=None) -> Tuple[dict, List[str], int]:
    """Apply exact-version research, returning public credits and completeness.

    Callers reading drafts from disk must use the canonical draft returned by
    verify_provenance, so removed/changed resolutions cannot leave stale values.
    Warnings and exclusions do not make a report incomplete.
    """
    validate_draft(draft)
    _validate_config(config)
    resolutions = {(rule["name"], rule["version"]): rule for rule in config["resolutions"]}
    exclusions = {(rule["name"], rule["version"]): rule for rule in config["exclusions"]}
    seen = set()
    diagnostics = []
    components = []
    status = 0
    labels = []
    for component in draft["components"]:
        identity = (component["name"], component["version"])
        seen.add(identity)
        label = f"{identity[0]} {identity[1]}"
        if identity in exclusions and artifact is None:
            diagnostics.append(f"Excluded {label}: {exclusions[identity]['reason']}")
            continue
        rule = resolution_for_component(component, resolutions)
        result = _apply_rule(component, rule)
        if artifact is not None:
            decision = _artifact_decision(result, rule, exclusions.get(identity), artifact,
                                          component["name"], component_ecosystem(component))
            if decision["disposition"] == "not_shipped":
                diagnostics.append(f"Excluded {label}: {decision['reason']}")
                continue
            if decision["disposition"] == "unknown":
                diagnostics.append(f"Unresolved {label}: [SCOPE_UNKNOWN] {decision['reason']}")
                status = 1
        reasons = _component_reasons(component, result, rule) + _version_review_reasons(identity, config)
        diagnostics.extend(f"Unresolved {label}: {reason}" for reason in reasons)
        if reasons:
            status = 1
        components.append(result)
        labels.append(label)
    metadata_additions = artifact_python_additions(draft, config, artifact)
    additions, addition_status = _included_additions(config.get("additions", []) + metadata_additions,
                                                    artifact, diagnostics)
    status |= addition_status | _append_additions(components, diagnostics, seen, additions)
    labels.extend(f"{a['name']} {a['version']}" for a in additions)
    for kind, rules in (("resolution", resolutions), ("exclusion", exclusions)):
        for name, version in sorted(rules.keys() - seen):
            diagnostics.append(f"Warning: unmatched {kind} rule: {name} {version}")
    credits = {field: draft[field] for field in ("name", "version", "disclaimer")}
    credits["components"] = components
    public_diagnostics, public_status = _publication_diagnostics(credits, labels)
    diagnostics.extend(public_diagnostics)
    status |= public_status
    return credits, diagnostics, status


def _included_additions(additions, artifact, diagnostics):
    if artifact is None:
        return additions, 0
    retained, status = [], 0
    for addition in additions:
        label = f"{addition['name']} {addition['version']}"
        decision = _artifact_decision(addition, addition, None, artifact)
        if decision["disposition"] == "not_shipped":
            diagnostics.append(f"Excluded {label}: {decision['reason']}")
            continue
        if decision["disposition"] == "unknown":
            diagnostics.append(f"Unresolved {label}: [SCOPE_UNKNOWN] {decision['reason']}")
            status = 1
        retained.append(addition)
    return retained, status
