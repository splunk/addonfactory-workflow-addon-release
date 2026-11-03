"""Offline public-format checks shared by generation and validation."""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

DATA = Path(__file__).parent / "data/credits-validation"
ROOT_FIELDS = {"name", "version", "disclaimer", "components"}
COMPONENT_FIELDS = {"name", "version", "license", "copyright_holder", "license_text", "project_url", "notices"}
NOTICE_FIELDS = {"name", "license", "copyright_holder", "license_text"}
APPENDIX = re.compile(r"(?:APPENDIX:\s*)?How to apply the Apache License to your work", re.I)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    field: str
    severity: str = "error"
    component_index: Optional[int] = None
    notice_index: Optional[int] = None


def _terms(text):
    start = re.search(r"1\.\s*Definitions\.", text)
    if not start:
        return ""
    body = text[start.start():]
    body = re.split(r"END OF TERMS AND CONDITIONS", body, maxsplit=1, flags=re.I)[0]
    body = APPENDIX.split(body, maxsplit=1)[0]
    return " ".join(body.split())


def valid_project_url(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not re.search(r"\s", value)
    except ValueError:
        return False


def _legal_findings(value, identifiers, apache, index, notice=None):
    result = []
    def add(code, message, field):
        result.append(Finding(code, message, field, component_index=index, notice_index=notice))
    if value["license"] not in identifiers:
        add("SPDX_IDENTIFIER", "expected one recognized SPDX license identifier; preserve obligations for review", "license")
    if not value["copyright_holder"].strip():
        add("COPYRIGHT_EMPTY", "public copyright holder is empty; retain verified attribution or the reviewed absence statement", "copyright_holder")
    text = value["license_text"].strip()
    if re.fullmatch(r"https?://\S+", text):
        add("LICENSE_URL_ONLY", "license text is only a URL", "license_text")
    elif value["license"] == "Apache-2.0":
        if _terms(text) != apache:
            add("APACHE_TERMS", "Apache Sections 1–9 differ from the pinned terms; review exact upstream text", "license_text")
        if APPENDIX.search(text):
            add("APACHE_APPENDIX", "remove application instructions from public license text; retain Sections 1–9 and actual attribution", "license_text")
    return result


def _shape(value, notice=False):
    if not isinstance(value, dict):
        return False
    required = NOTICE_FIELDS if notice else COMPONENT_FIELDS
    optional = {"notice_text"} if notice else set()
    if not required <= value.keys() or value.keys() - required - optional:
        return False
    strings = required - {"project_url", "notices"}
    if any(not isinstance(value[k], str) for k in strings):
        return False
    if not value["name"].strip() or (not notice and not value["version"].strip()):
        return False
    if notice:
        return "notice_text" not in value or isinstance(value["notice_text"], str)
    return isinstance(value["notices"], list)


def _identity_findings(value, index, seen):
    result = []
    identity = (value["name"], value["version"])
    if identity in seen:
        result.append(Finding("DUPLICATE_COMPONENT", "duplicate public component identity", "name", component_index=index))
    seen.add(identity)
    if re.search(r"^[\w+.-]+://|^git@[^:]+:", value["name"]):
        result.append(Finding("PUBLIC_NAME", "public name is a repository transport locator", "name", component_index=index))
    if not valid_project_url(value["project_url"]):
        result.append(Finding("PROJECT_URL", "expected a public HTTP(S) project URL", "project_url", component_index=index))
    return result


def validate_public_credits(credits):
    """Return deterministic findings without changing the supplied report."""
    if (not isinstance(credits, dict) or set(credits) != ROOT_FIELDS
            or not isinstance(credits.get("components"), list)
            or any(not isinstance(credits.get(k), str) or not credits[k].strip()
                   for k in ("name", "version", "disclaimer"))):
        return [Finding("PUBLIC_SCHEMA", "invalid public credits root", "root")]
    identifiers = set(json.loads((DATA / "spdx-identifiers.json").read_text())["identifiers"])
    apache = _terms((DATA / "Apache-2.0.txt").read_text())
    result, seen, versions = [], set(), {}
    for index, value in enumerate(credits["components"]):
        if not _shape(value):
            result.append(Finding("PUBLIC_SCHEMA", "invalid public component fields", "component", component_index=index))
            continue
        result.extend(_identity_findings(value, index, seen))
        versions.setdefault(value["name"], set()).add(value["version"])
        result.extend(_legal_findings(value, identifiers, apache, index))
        for j, notice in enumerate(value["notices"]):
            if not _shape(notice, notice=True):
                result.append(Finding("PUBLIC_SCHEMA", "invalid public notice fields", "notice", component_index=index, notice_index=j))
                continue
            result.extend(_legal_findings(notice, identifiers, apache, index, j))
    for name, values in versions.items():
        if len(values) > 1:
            result.append(Finding("VERSION_DISCLOSURE", f"{name} has multiple versions: {', '.join(sorted(values))}; disclose their applicability", "version", "warning"))
    return result
