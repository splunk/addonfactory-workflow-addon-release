#!/usr/bin/env python3
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
"""Process a FOSSA attribution CSV with reviewed, version-specific decisions.

Offline and standard-library-only. See runbooks/fossa-credits.md for the
configuration contract and review workflow. Exit codes: 0 complete, 1 TBD,
2 invalid inputs or processing error.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path


TBD = "TBD"
DISCLAIMER = (
    "Third-party open source copyright and notices are provided for informational purposes only. "
    "Splunk makes no warranties, express or implied, regarding the accuracy or completeness of "
    "this information and disclaims liability for any errors or omissions."
)
SOURCES = ("declared", "discovered")
REVIEW_FIELDS = ("license", "copyright_holder", "license_text")
REQUIRED_COLUMNS = {"Title", "Version", "Declared License", "Discovered License(s)"}
SECTIONS = {"Direct Dependencies", "Deep Dependencies", "Transitive Dependencies",
            "Project Declared Licenses", "First Party Licenses", "Dependencies Summary"}
EMPTY_VALUES = {"", "none", "n/a", "noassertion", "unknown", "tbd"}
COMPOUND = re.compile(r"(?:^|[\s(])(?:AND|OR)(?:[\s)]|$)", re.IGNORECASE)
TEXT_COLUMN = re.compile(r"^(Declared|Discovered) License Text(?: \((\d+)\))?$")
NOTICE_COLUMN = re.compile(r"^Notice (?:Text|File Text|Files|File\(s\))(?: \(\d+\))?$")
COPYRIGHT_MARKER = re.compile(r"^\s*(?:portions\s+)?(?:copyright\b|©|\(c\))", re.IGNORECASE)
PLACEHOLDER = re.compile(
    r"<\s*(?:year|copyright holders?|copyright owners?|author|name)\b[^>]*>"
    r"|\[\s*(?:yyyy|year|name of copyright owner|copyright holders?)\s*\]",
    re.IGNORECASE,
)


def empty_candidates():
    return {source: [] for source in SOURCES}


def available(value):
    return bool(value.strip()) and value.strip().casefold() not in EMPTY_VALUES


def license_values(value):
    value = re.sub(r"^\s*Multi-license:\s*", "", value, flags=re.IGNORECASE)
    result = {}
    for part in value.split(";"):
        part = part.strip()
        if part:
            result.setdefault(part.casefold(), part)
    return list(result.values())


def single_license(value):
    return available(value) and not COMPOUND.search(value)


def select_license(candidates):
    declared, discovered = (candidates[source] for source in SOURCES)
    present = [values for values in (declared, discovered) if values]
    if not present or any(len(values) != 1 for values in present):
        return TBD
    choices = [values[0] for values in present]
    if not all(single_license(choice) for choice in choices):
        return TBD
    if len({choice.casefold() for choice in choices}) != 1:
        return TBD
    return choices[0]


def license_text_blocks(row):
    chunks = {source: {} for source in SOURCES}
    for column, text in row.items():
        match = TEXT_COLUMN.fullmatch(column)
        if match and available(text):
            source = match[1].lower()
            number = int(match[2] or 1)
            if number < 1 or number in chunks[source]:
                raise ValueError(f"{row['Title']}: duplicate or invalid {source} text chunk {number}")
            chunks[source][number] = text
    result = empty_candidates()
    for source, parts in chunks.items():
        if parts:
            numbers = sorted(parts)
            if numbers != list(range(1, numbers[-1] + 1)):
                raise ValueError(f"{row['Title']}: missing {source} text chunk")
            result[source] = ["".join(parts[number] for number in numbers)]
    return result


def select_text(license_id, candidates, licenses):
    blocks = [text for source in SOURCES for text in candidates[source]]
    if license_id == TBD or not blocks:
        return TBD
    if any(candidates[source] and [value.casefold() for value in licenses[source]] != [license_id.casefold()]
           for source in SOURCES):
        return TBD
    normalized = {text.replace("\r\n", "\n").replace("\r", "\n").strip() for text in blocks}
    return blocks[0] if len(normalized) == 1 else TBD


def copyright_statement(value):
    return available(value) and not PLACEHOLDER.search(value)


def select_copyright(row):
    statements = []
    dedicated = row.get("Copyrights", "").strip()
    if copyright_statement(dedicated):
        statements.append(dedicated)
    for column, value in row.items():
        if NOTICE_COLUMN.fullmatch(column):
            statements.extend(line.strip() for line in value.splitlines()
                              if COPYRIGHT_MARKER.search(line) and copyright_statement(line))
    return "\n".join(dict.fromkeys(statements)) or TBD


def build_component(row):
    licenses = {"declared": license_values(row["Declared License"]),
                "discovered": license_values(row["Discovered License(s)"])}
    license_id = select_license(licenses)
    texts = license_text_blocks(row)
    license_text = select_text(license_id, texts, licenses)
    return {
        "name": row["Title"].strip(),
        "version": row["Version"].strip(),
        "license": license_id,
        "copyright_holder": select_copyright(row),
        "license_text": license_text,
        "licenses_to_verify": licenses if license_id == TBD else empty_candidates(),
        "licenses_text_to_verify": texts if license_text == TBD else empty_candidates(),
        "project_url": row.get("Package Homepage", "").strip() or None,
        "notices": [],
    }


def header_columns(row):
    header = [column.strip() for column in row]
    missing = REQUIRED_COLUMNS - set(header)
    if missing or len(set(header)) != len(header):
        raise ValueError(f"Invalid CSV header; missing columns: {', '.join(sorted(missing)) or 'none'}; "
                         "column names must be unique")
    return header


def component_row(header, row, line):
    if header is None or len(row) != len(header):
        raise ValueError(f"CSV line {line}: component row has no header or incorrect column count")
    result = dict(zip(header, row))
    if not result["Title"].strip() or not result["Version"].strip():
        raise ValueError(f"CSV line {line}: component name and version are required")
    return result


def read_csv_rows(reader):
    header = None
    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) == 1 and (row[0].strip() in SECTIONS or
                             re.fullmatch(r"No .+ found\.", row[0].strip())):
            header = None
            continue
        if row[0].strip() == "Title" or {"Title", "Version"}.issubset(row):
            header = header_columns(row)
            continue
        yield component_row(header, row, reader.line_num)


def parse_csv(path):
    csv.field_size_limit(max(csv.field_size_limit(), 10 * 1024 * 1024))
    components = []
    identities = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in read_csv_rows(csv.reader(stream, strict=True)):
            item = build_component(row)
            identity = (item["name"], item["version"])
            if identity in identities:
                raise ValueError(f"Duplicate component identity: {identity[0]} {identity[1]}")
            identities.add(identity)
            components.append(item)
    if not components:
        raise ValueError("CSV contains no components")
    return components


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def check_fields(value, allowed, required, context):
    if not isinstance(value, dict):
        raise ValueError(f"{context}: expected an object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown or missing:
        raise ValueError(f"{context}: unknown fields {sorted(unknown)}; missing fields {sorted(missing)}")


def nonempty_string(value, context):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: expected a nonempty string")
    return value.strip()


def rule_identity(rule, context):
    name = nonempty_string(rule["name"], f"{context}.name")
    version = nonempty_string(rule["version"], f"{context}.version")
    if "*" in name or re.search(r"[\s*?^~<>=|,]", version):
        raise ValueError(f"{context}: wildcards and version ranges are not supported")
    return name, version


def check_override(field, value, context):
    nonempty_string(value, f"{context}.{field}")
    if not available(value):
        raise ValueError(f"{context}.{field}: override must resolve the field")
    if field == "license" and (license_values(value) != [value.strip()] or not single_license(value)):
        raise ValueError(f"{context}.license: expected a single license")
    if field == "copyright_holder" and not copyright_statement(value):
        raise ValueError(f"{context}.copyright_holder: placeholder attribution is unresolved")


def validated_rules(rules, kind):
    if not isinstance(rules, list):
        raise ValueError(f"{kind}: expected an array")
    required = {"name", "version", "reason"}
    allowed = set(required)
    if kind == "resolutions":
        required.add("source")
        allowed.update({"source", "notices", *REVIEW_FIELDS})
    result = {}
    for index, rule in enumerate(rules):
        context = f"{kind}[{index}]"
        check_fields(rule, allowed, required, context)
        identity = rule_identity(rule, context)
        for field in required - {"name", "version"}:
            nonempty_string(rule[field], f"{context}.{field}")
        check_rule_overrides(rule, kind, context)
        if identity in result:
            raise ValueError(f"Duplicate {kind} rule: {identity[0]} {identity[1]}")
        result[identity] = rule
    return result


def check_rule_overrides(rule, kind, context):
    if kind == "resolutions":
        overrides = set(rule) & {"notices", *REVIEW_FIELDS}
        if not overrides:
            raise ValueError(f"{context}: at least one override is required")
        for field in overrides:
            if field == "notices":
                check_notices(rule[field], context)
            else:
                check_override(field, rule[field], context)


def check_notices(notices, context):
    if not isinstance(notices, list):
        raise ValueError(f"{context}.notices: expected an array")
    names = set()
    required = {"name", "source", "reason", *REVIEW_FIELDS}
    for index, notice in enumerate(notices):
        label = f"{context}.notices[{index}]"
        check_fields(notice, required | {"notice_text"}, required, label)
        name = nonempty_string(notice["name"], f"{label}.name")
        if name in names:
            raise ValueError(f"{label}: duplicate notice name {name}")
        names.add(name)
        check_notice_values(notice, label)


def check_notice_values(notice, context):
    for field in ("source", "reason"):
        nonempty_string(notice[field], f"{context}.{field}")
    for field in REVIEW_FIELDS:
        if notice[field] != TBD:
            check_override(field, notice[field], context)
    if "notice_text" in notice and not isinstance(notice["notice_text"], str):
        raise ValueError(f"{context}.notice_text: expected a string")


def load_configuration(path):
    with path.open(encoding="utf-8-sig") as stream:
        data = json.load(stream, object_pairs_hook=unique_json_object)
    fields = {"schema_version", "resolutions", "exclusions"}
    check_fields(data, fields, fields, "configuration")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("configuration.schema_version: expected 1")
    resolutions = validated_rules(data["resolutions"], "resolutions")
    exclusions = validated_rules(data["exclusions"], "exclusions")
    if set(resolutions) & set(exclusions):
        raise ValueError("Resolution and exclusion rules overlap")
    return resolutions, exclusions


def apply_resolution(component, rule):
    for field in REVIEW_FIELDS:
        if field in rule:
            component[field] = rule[field].strip() if field != "license_text" else rule[field]
    if "license" in rule:
        component["licenses_to_verify"] = empty_candidates()
    if "license_text" in rule:
        component["licenses_text_to_verify"] = empty_candidates()
    if "notices" in rule:
        component["notices"] = rule["notices"]


def process_components(components, resolutions, exclusions):
    present = set()
    included = []
    for component in components:
        identity = (component["name"], component["version"])
        present.add(identity)
        if identity in exclusions:
            print(f"Excluded {identity[0]} {identity[1]}: {exclusions[identity]['reason']}", file=sys.stderr)
            continue
        if identity in resolutions:
            apply_resolution(component, resolutions[identity])
        included.append(component)
    for kind, rules in (("resolution", resolutions), ("exclusion", exclusions)):
        for name, version in sorted(set(rules) - present):
            print(f"Warning: unmatched {kind}: {name} {version}", file=sys.stderr)
    return included


def unresolved_fields(component):
    fields = [field for field in REVIEW_FIELDS if component[field] == TBD]
    for index, notice in enumerate(component["notices"]):
        fields.extend(f"notices[{index}].{field}" for field in REVIEW_FIELDS if notice[field] == TBD)
        if notice.get("notice_text", "").strip() == TBD:
            fields.append(f"notices[{index}].notice_text")
    return fields


def completeness_status(components):
    unresolved = 0
    for component in components:
        fields = unresolved_fields(component)
        if fields:
            unresolved += 1
            print(f"Unresolved {component['name']} {component['version']}: {', '.join(fields)}", file=sys.stderr)
    print(f"{len(components)} included components; {unresolved} unresolved", file=sys.stderr)
    return 1 if unresolved else 0


def filename_identity(value):
    if not value.strip() or re.search(r"[/\\\x00-\x1f\x7f]", value):
        raise argparse.ArgumentTypeError("identity must be nonempty and contain no path separators or control characters")
    return value


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="FOSSA attribution CSV")
    parser.add_argument("--config", required=True, type=Path, help="Reviewed resolution configuration JSON")
    parser.add_argument("--ta-name", required=True, type=filename_identity)
    parser.add_argument("--ta-version", required=True, type=filename_identity, help="Add-on release version")
    parser.add_argument("--ta-display-name", help="Report display name; defaults to --ta-name")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    return parser


def main(argv=None):
    args = argument_parser().parse_args(argv)
    output = args.output_dir / f"{args.ta_name}-{args.ta_version}-credits.json"
    try:
        if output.resolve() in {args.csv.resolve(), args.config.resolve()}:
            raise ValueError("Output must not overwrite the CSV or configuration")
        resolutions, exclusions = load_configuration(args.config)
        components = process_components(parse_csv(args.csv), resolutions, exclusions)
        document = {
            "name": args.ta_display_name or args.ta_name,
            "version": args.ta_version,
            "disclaimer": DISCLAIMER,
            "components": components,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, csv.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(f"Credits written to {output}", file=sys.stderr)
    return completeness_status(components)


if __name__ == "__main__":
    sys.exit(main())
