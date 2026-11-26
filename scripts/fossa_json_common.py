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
"""Shared strict JSON and request provenance checks for local FOSSA credits."""

import json
import math
import re
import urllib.parse
from datetime import datetime


REQUEST_OPTIONS = {
    "includeDeepDependencies": True,
    "includeCopyrightList": True,
    "includeNoticeFiles": True,
    "includeFileMatches": True,
    "includeHashAndVersionData": True,
    "excludeUnknownDependencies": False,
}


def attribution_url(project_locator: str, revision: str) -> str:
    locator = urllib.parse.quote(project_locator + "$" + revision, safe="")
    query = urllib.parse.urlencode(
        {key: str(value).lower() for key, value in REQUEST_OPTIONS.items()}
    )
    return "https://app.fossa.com/api/v2/revisions/" + locator + "/attribution/json?" + query


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - set(optional):
        raise ValueError("Object has missing or unknown fields")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")
    return value.strip()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON number")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON number")
    return result


def parse_json(raw: bytes):
    """Decode strict UTF-8 JSON, rejecting duplicate keys and nonfinite numbers."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant, parse_float=_finite_float)
    except (UnicodeError, ValueError):
        raise ValueError("Cannot read valid UTF-8 JSON") from None


def validate_metadata(metadata: dict, report: dict, provenance: dict) -> None:
    """Require request metadata to match the report and generated provenance."""
    required = {"ta_name", "display_name", "ta_version", "fossa_project_locator",
                "selected_revision", "response_revision", "fetched_at", "request_options",
                "raw_sha256", "direct_dependencies", "deep_dependencies", "api_url"}
    _fields(metadata, required)
    for field in required - {"request_options", "direct_dependencies", "deep_dependencies"}:
        _text(metadata[field])
    for field in ("ta_name", "ta_version"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", metadata[field]):
            raise ValueError("Invalid TA filename identity")
    prefix = metadata["ta_name"] + "-" + metadata["ta_version"] + "-"
    if (provenance["raw_report_file"] != prefix + "fossa-attribution.json"
            or provenance["request_metadata_file"] != prefix + "fossa-attribution.request.json"):
        raise ValueError("Provenance filenames do not match request identity")
    _metadata_request(metadata, report, provenance)


def _metadata_request(metadata, report, provenance):
    if (metadata["selected_revision"] != provenance["revision"]
            or metadata["response_revision"] != provenance["revision"]
            or metadata["raw_sha256"] != provenance["raw_sha256"]
            or metadata["fossa_project_locator"] != provenance["fossa_project_locator"]):
        raise ValueError("Request metadata does not match draft provenance")
    options = metadata["request_options"]
    if (not isinstance(options, dict) or options.keys() != REQUEST_OPTIONS.keys()
            or any(type(options[key]) is not bool or options[key] != value
                   for key, value in REQUEST_OPTIONS.items())):
        raise ValueError("Invalid attribution request options")
    if metadata["api_url"] != attribution_url(metadata["fossa_project_locator"], provenance["revision"]):
        raise ValueError("Invalid attribution API URL")
    try:
        timestamp = datetime.fromisoformat(metadata["fetched_at"].replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid fetch timestamp") from None
    if timestamp.utcoffset() is None:
        raise ValueError("Fetch timestamp requires a timezone")
    for field, section in (("direct_dependencies", "directDependencies"),
                           ("deep_dependencies", "deepDependencies")):
        if type(metadata[field]) is not int or metadata[field] != len(report[section]):
            raise ValueError("Request dependency count does not match report")
