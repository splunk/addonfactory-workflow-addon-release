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
"""Convert native FOSSA attribution JSON into a reviewable credits draft."""

import re


TBD = "TBD"
GROUPS = ("licenses", "otherLicenses")
DISCLAIMER = (
    "Third-party open source copyright and notices are provided for informational purposes only. "
    "Splunk makes no warranties, express or implied, regarding the accuracy or completeness of "
    "this information and disclaims liability for any errors or omissions."
)
COMPOUND = re.compile(r"(?:^|[\s(])(?:AND|OR)(?:[\s)]|$)", re.IGNORECASE)
COPYRIGHT = re.compile(r"^\s*(?:portions\s+)?(?:copyright\b|©|\(c\))", re.IGNORECASE)
COPYRIGHT_PREFIX = re.compile(r"^\s*(?:copyright(?:\s*\(c\))?|©|\(c\))\s*", re.IGNORECASE)
COPYRIGHT_GENERIC = re.compile(
    r"^(?:notice|licen[cs]e|you|infringement)\b",
    re.IGNORECASE,
)
COPYRIGHT_UNNAMED_HOLDER = re.compile(
    r"^(?:owners?|holders?)\s*:?[\s]*$|^(?:owners?|holders?)\s+(?:of|or|and)\b",
    re.IGNORECASE,
)
COPYRIGHT_BOILERPLATE = re.compile(
    r"^\s*copyright\s+(?:notice(?:,\s*this list of conditions|\s+that is included)"
    r"|holders?\s+be liable)\b",
    re.IGNORECASE,
)
COPYRIGHT_HOLDER_PREFIX = re.compile(r"^(?:owners?|holders?)(?::\s*|\s+)", re.IGNORECASE)
PLACEHOLDER = re.compile(
    r"<\s*(?:year|copyright holders?|copyright owners?|author|name)\b[^>]*>"
    r"|\[\s*(?:yyyy|year|name of copyright owner|copyright holders?)\s*\]"
    r"|\{\s*(?:yyyy|year|name of copyright owner|copyright holders?)\s*\}",
    re.IGNORECASE,
)
APACHE_APPENDIX_MARKER = "appendix: how to apply the apache license to your work."
APACHE_APPENDIX_EXAMPLE = "copyright [yyyy] [name of copyright owner]"


def has_unfilled_copyright_slot(license_text):
    """Find operative copyright placeholders in an attributed license body.

    The Apache-2.0 appendix includes an instructional example which is part
    of the canonical license text, not the package's copyright grant.
    """
    in_apache_appendix = False
    for line in license_text.splitlines():
        if APACHE_APPENDIX_MARKER in line.casefold():
            in_apache_appendix = True
        if COPYRIGHT.search(line) and PLACEHOLDER.search(line):
            example = line.strip().casefold().translate(str.maketrans("{}", "[]"))
            if not (example == APACHE_APPENDIX_EXAMPLE
                    and in_apache_appendix):
                return True
    return False


def has_copyright_boilerplate(text):
    """Find common license clauses mistaken for holder declarations."""
    return any(COPYRIGHT_BOILERPLATE.search(line) for line in text.splitlines())


def _text(value, label, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"Invalid FOSSA {label}")
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"Invalid FOSSA {label}")
    return value


def _normal(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _statements(text):
    statements = []
    for line in text.splitlines():
        marker = COPYRIGHT.search(line)
        if not marker or PLACEHOLDER.search(line) or COPYRIGHT_BOILERPLATE.search(line):
            continue
        remainder = line[marker.end():].strip()
        remainder = re.sub(r"^\(c\)\s*", "", remainder, flags=re.IGNORECASE)
        if (remainder and not COPYRIGHT_GENERIC.search(remainder)
                and not COPYRIGHT_UNNAMED_HOLDER.search(remainder)):
            statements.append(line.strip())
    return statements


def _statement_key(statement):
    name = COPYRIGHT_PREFIX.sub("", statement)
    name = COPYRIGHT_HOLDER_PREFIX.sub("", name)
    return " ".join(name.casefold().split())


def _copyright_holder(notices, selected_attributions):
    """Choose only one unambiguous, component-local copyright statement."""
    notice_statements = []
    for notice in notices:
        if notice.get("ignored") or not notice["contents"]:
            continue
        lines = _statements(notice["contents"])
        notice_statements.extend(lines)
        full_line_keys = {_statement_key(line) for line in lines}
        for value in notice["copyrights"]:
            if not value.strip() or PLACEHOLDER.search(value) or has_copyright_boilerplate(value):
                continue
            statement = value.strip()
            if not COPYRIGHT.search(statement):
                statement = "Copyright " + statement
            if _statement_key(statement) not in full_line_keys:
                notice_statements.append(statement)
    attribution_statements = [
        line for attribution in selected_attributions for line in _statements(attribution)
    ]
    statements = {}
    for statement in [*notice_statements, *attribution_statements]:
        statements.setdefault(_statement_key(statement), statement)
    return next(iter(statements.values())) if len(statements) == 1 else TBD


def _native_candidates(dependency):
    """Validate and retain both native license groups and their attribution blocks."""
    candidates = {group: [] for group in GROUPS}
    text_candidates = {group: [] for group in GROUPS}
    entries = {}
    distinct = {}
    for group in GROUPS:
        entries[group] = _list(dependency.get(group), group)
        seen = set()
        for entry in entries[group]:
            if not isinstance(entry, dict):
                raise ValueError(f"Invalid FOSSA {group} entry")
            identifier = _text(entry.get("name"), f"{group} name", required=True).strip()
            if "attribution" in entry and entry["attribution"] is not None:
                _text(entry["attribution"], f"{group} attribution")
            key = identifier.casefold()
            distinct.setdefault(key, identifier)
            if key not in seen:
                candidates[group].append(identifier)
                seen.add(key)
            attribution = entry.get("attribution")
            if isinstance(attribution, str) and attribution.strip():
                text_candidates[group].append({"name": entry["name"],
                                               "text": attribution})

    return entries, distinct, candidates, text_candidates


def _notice_files(dependency):
    """Validate complete notice evidence without discarding native fields."""
    notices = _list(dependency.get("noticeFiles"), "noticeFiles")
    for notice in notices:
        if not isinstance(notice, dict):
            raise ValueError("Invalid FOSSA noticeFiles entry")
        _text(notice.get("path"), "notice path", required=True)
        _text(notice.get("contents"), "notice contents")
        copyrights = _list(notice.get("copyrights"), "notice copyrights")
        if any(not isinstance(value, str) for value in copyrights):
            raise ValueError("Invalid FOSSA notice copyrights")
        for flag in ("ignored", "corrected"):
            if not isinstance(notice.get(flag), bool):
                raise ValueError(f"Invalid FOSSA notice {flag}")

    return notices


def build_component(dependency: dict, kind: str) -> dict:
    """Preserve native evidence and select only unambiguous component fields."""
    if not isinstance(dependency, dict) or kind not in ("direct", "deep"):
        raise ValueError("Invalid FOSSA dependency")
    name = _text(dependency.get("title"), "dependency title", required=True)
    version = _text(dependency.get("version"), "dependency version", required=True)
    for field in ("package", "source"):
        _text(dependency.get(field), field, required=True)
    project_url = dependency.get("projectUrl")
    if project_url is not None:
        _text(project_url, "projectUrl")
    download_url = dependency.get("downloadUrl")
    if download_url is not None:
        _text(download_url, "downloadUrl")
    paths = _list(dependency.get("dependencyPaths"), "dependencyPaths")
    if any(not isinstance(path, str) for path in paths):
        raise ValueError("Invalid FOSSA dependencyPaths")

    entries, distinct, candidates, text_candidates = _native_candidates(dependency)

    license_id = TBD
    if len(distinct) == 1:
        only = next(iter(distinct.values()))
        if not COMPOUND.search(only) and only.casefold() != TBD.casefold():
            license_id = only

    selected_attributions = []
    if license_id != TBD:
        selected_attributions = [entry["attribution"] for group in GROUPS
                                 for entry in entries[group]
                                 if isinstance(entry.get("attribution"), str)
                                 and entry["attribution"].strip()]
    license_text = TBD
    if (selected_attributions
            and len({_normal(text) for text in selected_attributions}) == 1
            and not has_unfilled_copyright_slot(selected_attributions[0])):
        license_text = selected_attributions[0]

    notices = _notice_files(dependency)

    return {
        "name": name,
        "version": version,
        "license": license_id,
        "copyright_holder": _copyright_holder(notices, selected_attributions),
        "license_text": license_text,
        "licenses_to_verify": candidates if license_id == TBD else {group: [] for group in GROUPS},
        "licenses_text_to_verify": text_candidates if license_text == TBD else {group: [] for group in GROUPS},
        "project_url": project_url or None,
        "notices": [],
        "fossa_evidence": {
            "dependency_kind": kind,
            "package": dependency["package"],
            "source": dependency["source"],
            "downloadUrl": download_url,
            "dependencyPaths": paths,
            "licenses": entries["licenses"],
            "otherLicenses": entries["otherLicenses"],
            "noticeFiles": notices,
        },
    }


def build_draft(report: dict, settings: dict, version: str, revision: str,
                raw_sha256: str, raw_filename: str, metadata_filename: str) -> dict:
    """Validate report identity and preserve dependency order in a five-key draft."""
    if not isinstance(report, dict) or not isinstance(settings, dict):
        raise ValueError("Invalid FOSSA report or settings")
    project = report.get("project")
    if not isinstance(project, dict):
        raise ValueError("Invalid FOSSA project")
    _text(project.get("name"), "project name", required=True)
    if _text(project.get("revision"), "project revision", required=True) != revision:
        raise ValueError("FOSSA response revision does not match selected revision")
    for key in ("display_name", "fossa_project_locator"):
        _text(settings.get(key), f"settings {key}", required=True)
    for value, label in ((version, "version"), (revision, "revision"),
                         (raw_sha256, "raw SHA-256"), (raw_filename, "raw filename"),
                         (metadata_filename, "metadata filename")):
        _text(value, label, required=True)
    components = []
    identities = set()
    for section, kind in (("directDependencies", "direct"),
                          ("deepDependencies", "deep")):
        for row in _list(report.get(section), section):
            component = build_component(row, kind)
            identity = (component["name"], component["version"])
            if identity in identities:
                raise ValueError(f"FOSSA report contains duplicate dependency {identity}")
            identities.add(identity)
            components.append(component)
    return {
        "name": settings["display_name"],
        "version": version,
        "disclaimer": DISCLAIMER,
        "components": components,
        "provenance": {
            "fossa_project_locator": settings["fossa_project_locator"],
            "revision": revision,
            "raw_sha256": raw_sha256,
            "raw_report_file": raw_filename,
            "request_metadata_file": metadata_filename,
        },
    }
