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
"""Group unresolved legal fields and diagnostic-only review blockers."""

FIELDS = ("license", "copyright_holder", "license_text")


def pending_components(credits):
    if not isinstance(credits, dict) or not isinstance(credits.get("components"), list):
        raise ValueError("Expected a credits object with components")
    pending = []
    for component in credits["components"]:
        if not isinstance(component, dict):
            raise ValueError("Invalid component")
        name, version = component.get("name"), component.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("Invalid component identity")
        fields = [field for field in FIELDS if component.get(field) == "TBD"]
        notices = component.get("notices", [])
        if not isinstance(notices, list):
            raise ValueError("Invalid notices")
        for notice in notices:
            if not isinstance(notice, dict) or not isinstance(notice.get("name"), str):
                raise ValueError("Invalid notice")
            fields.extend(
                f"notice {notice['name']}: {field}"
                for field in (*FIELDS, "notice_text") if notice.get(field) == "TBD"
            )
        if fields:
            pending.append((name, version, fields))
    return pending


def pending_reviews(credits, diagnostics):
    """Include generator blockers that have no TBD field, in component order."""
    fields_by_identity = {
        (name, version): list(fields)
        for name, version, fields in pending_components(credits)
    }
    for component in credits["components"]:
        name, version = component["name"], component["version"]
        prefix = f"Unresolved {name} {version}: "
        for diagnostic in diagnostics:
            if diagnostic.startswith(prefix):
                reason = diagnostic[len(prefix):]
                fields = fields_by_identity.setdefault((name, version), [])
                if reason not in fields and reason.removesuffix(" is TBD") not in fields:
                    fields.append(reason)
    return [
        (component["name"], component["version"],
         fields_by_identity[(component["name"], component["version"])])
        for component in credits["components"]
        if (component["name"], component["version"]) in fields_by_identity
    ]
