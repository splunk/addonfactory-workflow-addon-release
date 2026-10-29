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
"""Behavioral tests for the offline FOSSA credits review contract."""
import csv
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/process_fossa_report.py"
HEADERS = [
    "Title", "Version", "Declared License", "Discovered License(s)",
    "Copyrights", "Package Homepage", "Declared License Text (1)",
    "Discovered License Text (1)",
]
FIELDS = {
    "name", "version", "license", "copyright_holder", "license_text",
    "licenses_to_verify", "licenses_text_to_verify", "project_url", "notices",
}


def component(**changes):
    row = {
        "Title": "example", "Version": "v1.2.3", "Declared License": "MIT",
        "Discovered License(s)": "", "Copyrights": "Copyright 2026 Example Contributors",
        "Package Homepage": "https://example.test/project",
        "Declared License Text (1)": "MIT\nPermission is hereby granted.\n",
        "Discovered License Text (1)": "",
    }
    row.update(changes)
    return row


def write_csv(path, rows, headers=None, bom=False):
    headers = headers or list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows([row.get(key, "") for key in headers] for row in rows)


def inputs(tmp_path, rows=None, config=None):
    csv_path = tmp_path / "report.csv"
    config_path = tmp_path / "resolutions.json"
    write_csv(csv_path, rows if rows is not None else [component()])
    config_path.write_text(json.dumps(config if config is not None else {
        "schema_version": 1, "resolutions": [], "exclusions": [],
    }), encoding="utf-8")
    return ["--csv", str(csv_path), "--config", str(config_path),
            "--ta-name", "Splunk_TA_aws", "--ta-version", "8.2.2",
            "--ta-display-name", "Splunk Add-on for Amazon Web Services",
            "--output-dir", str(tmp_path / "artifacts")]


def invoke(tmp_path, rows=None, config=None):
    parser = importlib.import_module("scripts.process_fossa_report")
    status = parser.main(inputs(tmp_path, rows, config))
    path = tmp_path / "artifacts/Splunk_TA_aws-8.2.2-credits.json"
    return status, json.loads(path.read_text())["components"] if path.exists() else None


def resolution(**changes):
    row = {"name": "example", "version": "v1.2.3", "reason": "Reviewed artifact",
           "source": "https://example.test/LICENSE", "license": "MIT"}
    row.update(changes)
    return row


def config_for(*rows, exclusions=None):
    return {"schema_version": 1, "resolutions": list(rows), "exclusions": exclusions or []}


def test_cli_writes_exact_contract_with_addon_release_filename(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), *inputs(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    document = json.loads((tmp_path / "artifacts/Splunk_TA_aws-8.2.2-credits.json").read_text())
    assert set(document) == {"name", "version", "disclaimer", "components"}
    assert document["name"] == "Splunk Add-on for Amazon Web Services"
    assert document["version"] == "8.2.2"
    assert document["disclaimer"] == (
        "Third-party open source copyright and notices are provided for informational purposes only. "
        "Splunk makes no warranties, express or implied, regarding the accuracy or completeness of "
        "this information and disclaims liability for any errors or omissions."
    )
    assert document["components"] == [{
        "name": "example", "version": "v1.2.3", "license": "MIT",
        "copyright_holder": "Copyright 2026 Example Contributors",
        "license_text": "MIT\nPermission is hereby granted.\n",
        "licenses_to_verify": {"declared": [], "discovered": []},
        "licenses_text_to_verify": {"declared": [], "discovered": []},
        "project_url": "https://example.test/project",
        "notices": [],
    }]
    assert set(document["components"][0]) == FIELDS


@pytest.mark.parametrize("declared,discovered,expected", [
    ("MIT", "", "MIT"), ("", "BSD-3-Clause", "BSD-3-Clause"),
    (" MIT ", "mit", "MIT"), ("MIT; mit", "", "MIT"),
    ("MIT; CC0-1.0", "", "TBD"), ("Apache-2.0", "MPL-2.0; MIT", "TBD"),
    ("MIT", "BSD-3-Clause", "TBD"), ("", "", "TBD"),
    ("MIT OR Apache-2.0", "", "TBD"), ("MIT AND BSD-3-Clause", "", "TBD"),
    ("Multi-license: BSD-2-Clause OR MIT", "", "TBD"),
    ("Apache-2.0 WITH llvm-exception", "", "Apache-2.0 WITH llvm-exception"),
    ("Apache-2.0", "Apache-2.0 WITH llvm-exception", "TBD"),
    ("NOASSERTION", "", "TBD"), ("unknown", "", "TBD"),
])
def test_main_license_selection(tmp_path, declared, discovered, expected):
    _, data = invoke(tmp_path, [component(**{"Declared License": declared,
                                            "Discovered License(s)": discovered})])
    assert data[0]["license"] == expected
    if expected != "TBD":
        assert data[0]["licenses_to_verify"] == {"declared": [], "discovered": []}


def test_conflicts_preserve_source_and_more_than_two_licenses(tmp_path):
    _, data = invoke(tmp_path, [component(**{
        "Declared License": "Apache-2.0", "Discovered License(s)": "MIT; MPL-2.0; BSD-3-Clause",
    })])
    assert data[0]["licenses_to_verify"] == {
        "declared": ["Apache-2.0"], "discovered": ["MIT", "MPL-2.0", "BSD-3-Clause"],
    }


def test_chunks_reassemble_numerically_without_added_characters(tmp_path):
    row = component(**{"Declared License Text (1)": "x" * 32000,
                       "Declared License Text (2)": "continued", "Declared License Text (3)": ""})
    headers = [*HEADERS, "Declared License Text (3)", "Declared License Text (2)"]
    args = inputs(tmp_path)
    write_csv(tmp_path / "report.csv", [row], headers, bom=True)
    parser = importlib.import_module("scripts.process_fossa_report")
    assert parser.main(args) == 0
    data = json.loads((tmp_path / "artifacts/Splunk_TA_aws-8.2.2-credits.json").read_text())["components"]
    assert data[0]["license_text"] == "x" * 32000 + "continued"


@pytest.mark.parametrize("discovered,text,expected", [
    ("MIT", "MIT\r\nPermission is hereby granted.\r\n", "MIT\nPermission is hereby granted.\n"),
    ("MIT", "different legal terms", "TBD"),
    ("BSD-3-Clause", "BSD terms", "TBD"),
])
def test_text_agreement_and_conflict(tmp_path, discovered, text, expected):
    _, data = invoke(tmp_path, [component(**{
        "Discovered License(s)": discovered, "Discovered License Text (1)": text,
    })])
    assert data[0]["license_text"] == expected
    candidates = data[0]["licenses_text_to_verify"]
    assert candidates == ({"declared": [], "discovered": []} if expected != "TBD" else {
        "declared": ["MIT\nPermission is hereby granted.\n"], "discovered": [text],
    })


def test_discovered_only_text_and_unnumbered_text(tmp_path):
    row = component(**{"Declared License": "", "Declared License Text (1)": "",
                       "Discovered License(s)": "MIT", "Discovered License Text": "MIT terms"})
    status, data = invoke(tmp_path, [row])
    assert status == 0
    assert data[0]["license_text"] == "MIT terms"


def test_text_without_a_matching_license_source_requires_review(tmp_path):
    status, data = invoke(tmp_path, [component(**{
        "Declared License Text (1)": "", "Discovered License Text (1)": "Unidentified terms",
    })])
    assert status == 1
    assert data[0]["license"] == "MIT"
    assert data[0]["license_text"] == "TBD"
    assert data[0]["licenses_text_to_verify"]["discovered"] == ["Unidentified terms"]


def test_no_text_or_header_substitution_fails_and_writes_report(tmp_path, capsys):
    status, data = invoke(tmp_path, [component(**{
        "Declared License Text (1)": "", "Declared License Header": "MIT header terms",
    })])
    assert status == 1
    assert data[0]["license_text"] == "TBD"
    assert data[0]["licenses_text_to_verify"] == {"declared": [], "discovered": []}
    assert "example" in capsys.readouterr().err


def test_notice_copyright_is_collected_without_license_template_inference(tmp_path):
    row = component(**{"Copyrights": "", "Notice Text (1)": "Example project\nCopyright 2024 Alice\nPermission granted",
                       "Notice Text (2)": "Copyright 2025 Bob\nCopyright (c) <year> <copyright holders>"})
    _, data = invoke(tmp_path, [row])
    assert data[0]["copyright_holder"] == "Copyright 2024 Alice\nCopyright 2025 Bob"


def test_notice_license_conditions_are_not_copyright_holders(tmp_path):
    _, data = invoke(tmp_path, [component(**{"Copyrights": "", "Notice Text":
        "Copyright 2024 Alice\nThe above copyright notice shall be included in all copies."})])
    assert data[0]["copyright_holder"] == "Copyright 2024 Alice"


@pytest.mark.parametrize("value", ["", "None", "Copyright (c) <year> <copyright holders>"])
def test_missing_or_placeholder_copyright_stays_tbd(tmp_path, value):
    status, data = invoke(tmp_path, [component(**{"Copyrights": value,
        "Declared License Text (1)": "MIT\nCopyright (c) <year> <copyright holders>", "Authors": "Alice"})])
    assert status == 1
    assert data[0]["copyright_holder"] == "TBD"


def test_license_override_does_not_clear_text_ambiguity(tmp_path):
    status, data = invoke(tmp_path, [component(**{"Declared License": "MIT; CC0-1.0"})],
                          config_for(resolution()))
    assert status == 1
    assert data[0]["license"] == "MIT"
    assert data[0]["licenses_to_verify"] == {"declared": [], "discovered": []}
    assert data[0]["license_text"] == "TBD"
    assert data[0]["licenses_text_to_verify"]["declared"]


def test_reviewed_overrides_complete_the_report(tmp_path):
    status, data = invoke(tmp_path, [component(**{"Declared License": "MIT; CC0-1.0", "Copyrights": ""})],
        config_for(resolution(copyright_holder="Copyright 2026 Alice", license_text="Reviewed full MIT terms")))
    assert status == 0
    assert data[0]["copyright_holder"] == "Copyright 2026 Alice"
    assert data[0]["license_text"] == "Reviewed full MIT terms"
    assert data[0]["licenses_text_to_verify"] == {"declared": [], "discovered": []}


def test_text_override_leaves_unresolved_license_candidates(tmp_path):
    rule = resolution(license_text="Reviewed terms")
    del rule["license"]
    status, data = invoke(tmp_path, [component(**{"Declared License": "MIT; CC0-1.0"})], config_for(rule))
    assert status == 1
    assert data[0]["license"] == "TBD"
    assert data[0]["licenses_to_verify"]["declared"] == ["MIT", "CC0-1.0"]
    assert data[0]["licenses_text_to_verify"] == {"declared": [], "discovered": []}


def test_exact_version_exclusions_and_unmatched_rules_are_diagnosed(tmp_path, capsys):
    rows = [component(), component(Title="build-only", Version="2", Copyrights="")]
    rule = resolution(version="v1.2.2", license="BSD-3-Clause")
    status, data = invoke(tmp_path, rows, config_for(rule, exclusions=[
        {"name": "build-only", "version": "2", "reason": "Not distributed"},
        {"name": "gone", "version": "1", "reason": "Historical rule"},
    ]))
    assert status == 0
    assert [entry["name"] for entry in data] == ["example"]
    assert data[0]["license"] == "MIT"
    stderr = capsys.readouterr().err
    assert "Not distributed" in stderr and "v1.2.2" in stderr and "gone" in stderr


@pytest.mark.parametrize("bad_config", [
    {}, {"schema_version": True, "resolutions": [], "exclusions": []},
    {"schema_version": 2, "resolutions": [], "exclusions": []},
    dict(config_for(), extra=1), dict(config_for(), resolutions={}),
    config_for({}), config_for(dict(resolution(), unknown="field")),
    config_for(resolution(license="")), config_for(resolution(license="TBD")),
    config_for(resolution(license="MIT OR Apache-2.0")),
    config_for(resolution(license="MIT; MIT")), config_for(resolution(license="Multi-license: MIT")),
    config_for(resolution(license_text=None)), config_for(resolution(reason="")),
    config_for(resolution(source="")), config_for(resolution(version="*")),
    config_for(resolution(version="^1.2.3")), config_for(resolution(name="@types/*")),
    config_for(resolution(version="1.0 || 2.0")), config_for(resolution(version="1.0 - 2.0")),
    config_for(resolution(), resolution()),
    config_for(resolution(), exclusions=[{"name": "example", "version": "v1.2.3", "reason": "Excluded"}]),
    config_for(exclusions=[{"name": "x", "version": "1", "reason": ""}]),
    config_for(exclusions=[{"name": "x", "version": "1", "reason": "ok", "license": "MIT"}]),
])
def test_invalid_configuration_fails_without_output(tmp_path, bad_config):
    status, data = invoke(tmp_path, config=bad_config)
    assert status == 2
    assert data is None


def test_configuration_requires_at_least_one_override(tmp_path):
    rule = resolution()
    del rule["license"]
    assert invoke(tmp_path, config=config_for(rule))[0] == 2


def test_section_headers_change_positions_and_preserve_order(tmp_path):
    args = inputs(tmp_path)
    with (tmp_path / "report.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerows([["Project Declared Licenses"], ["No declared licenses found."], [], ["Direct Dependencies"]])
        writer.writerow(HEADERS)
        writer.writerow([component().get(key, "") for key in HEADERS])
        writer.writerows([[], ["Deep Dependencies"]])
        second = list(reversed(HEADERS)) + ["Description"]
        writer.writerow(second)
        row = component(Title="second", Version="2", Description="Quoted, multiline\ntext")
        writer.writerow([row.get(key, "") for key in second])
        writer.writerow(second)
    parser = importlib.import_module("scripts.process_fossa_report")
    assert parser.main(args) == 0
    data = json.loads((tmp_path / "artifacts/Splunk_TA_aws-8.2.2-credits.json").read_text())["components"]
    assert [entry["name"] for entry in data] == ["example", "second"]


@pytest.mark.parametrize("bad_rows", [
    [["unexpected data"]], [HEADERS, ["only-name"]], [HEADERS, ["x", "1"]],
    [["Title", "Version"], ["x", "1"]], [HEADERS, [""] * len(HEADERS)],
    [HEADERS, ["x", "", "MIT", "", "Alice", "", "terms", ""]],
    [HEADERS, ["", "1", "MIT", "", "Alice", "", "terms", ""]],
    [HEADERS, [component().get(key, "") for key in HEADERS], [component().get(key, "") for key in HEADERS]],
])
def test_invalid_csv_fails_without_output(tmp_path, bad_rows):
    args = inputs(tmp_path)
    with (tmp_path / "report.csv").open("w", newline="") as stream:
        csv.writer(stream).writerows(bad_rows)
    parser = importlib.import_module("scripts.process_fossa_report")
    assert parser.main(args) == 2
    assert not (tmp_path / "artifacts/Splunk_TA_aws-8.2.2-credits.json").exists()


@pytest.mark.parametrize("changes", [
    {"Declared License Text (1)": "", "Declared License Text (2)": "orphan chunk"},
    {"Declared License Text": "other unnumbered text"},
])
def test_invalid_text_chunk_layout_is_not_silently_repaired(tmp_path, changes):
    assert invoke(tmp_path, [component(**changes)])[0] == 2


@pytest.mark.parametrize("flag,value", [("--ta-name", "../aws"), ("--ta-name", "aws\\name"),
                                        ("--ta-version", "8/2/2"), ("--ta-version", "")])
def test_invalid_filename_identity_is_rejected(tmp_path, flag, value):
    args = inputs(tmp_path)
    args[args.index(flag) + 1] = value
    result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    assert result.returncode == 2
    assert not (tmp_path / "artifacts").exists()


def test_default_output_directory_and_missing_url(tmp_path, monkeypatch):
    args = inputs(tmp_path, [component(**{"Package Homepage": ""})])
    args = args[:-2]
    monkeypatch.chdir(tmp_path)
    parser = importlib.import_module("scripts.process_fossa_report")
    assert parser.main(args) == 0
    data = json.loads((tmp_path / "Splunk_TA_aws-8.2.2-credits.json").read_text())["components"]
    assert data[0]["project_url"] is None


def test_display_name_defaults_to_package_identifier(tmp_path):
    args = inputs(tmp_path)
    index = args.index("--ta-display-name")
    del args[index:index + 2]
    parser = importlib.import_module("scripts.process_fossa_report")
    assert parser.main(args) == 0
    document = json.loads((tmp_path / "artifacts/Splunk_TA_aws-8.2.2-credits.json").read_text())
    assert document["name"] == "Splunk_TA_aws"
    assert document["components"][0]["version"] == "v1.2.3"


def test_missing_files_bad_json_and_duplicate_json_keys_fail(tmp_path):
    parser = importlib.import_module("scripts.process_fossa_report")
    args = inputs(tmp_path)
    (tmp_path / "resolutions.json").write_text('{"schema_version":1,"schema_version":2}')
    assert parser.main(args) == 2
    (tmp_path / "resolutions.json").write_text("not JSON")
    assert parser.main(args) == 2
    (tmp_path / "resolutions.json").unlink()
    assert parser.main(args) == 2
    args = inputs(tmp_path)
    (tmp_path / "report.csv").unlink()
    assert parser.main(args) == 2


def test_output_cannot_overwrite_input_config(tmp_path):
    parser = importlib.import_module("scripts.process_fossa_report")
    args = inputs(tmp_path)
    output = tmp_path / "artifacts/Splunk_TA_aws-8.2.2-credits.json"
    output.parent.mkdir()
    (tmp_path / "resolutions.json").rename(output)
    args[args.index("--config") + 1] = str(output)
    assert parser.main(args) == 2
    assert json.loads(output.read_text())["schema_version"] == 1


def supporting_notice(**changes):
    notice = {
        "name": "bundled-helper", "license": "BSD-3-Clause",
        "copyright_holder": "Copyright 2024 Helper Contributors",
        "license_text": "Complete supporting license text\n",
        "notice_text": "Helper attribution\nCopyright 2024 Helper Contributors\n",
        "source": "dependency@v1.2.3/vendor/helper/LICENSE",
        "reason": "vendor/helper is included in the TA release artifact at commit abc123",
    }
    notice.update(changes)
    return notice


def test_main_and_supporting_licenses_survive_regeneration(tmp_path):
    row = component(**{"Declared License": "MIT; BSD-3-Clause"})
    rule = resolution(license_text="Complete main MIT license text\n", notices=[supporting_notice()])
    status, data = invoke(tmp_path, [row], config_for(rule))
    assert status == 0
    assert data[0]["license"] == "MIT"
    assert data[0]["license_text"] == rule["license_text"]
    assert data[0]["notices"] == rule["notices"]
    assert data[0]["licenses_to_verify"] == {"declared": [], "discovered": []}
    assert data[0]["licenses_text_to_verify"] == {"declared": [], "discovered": []}


def test_notices_only_override_preserves_primary_ambiguity(tmp_path):
    rule = resolution(notices=[supporting_notice()])
    del rule["license"]
    status, data = invoke(tmp_path, [component(**{"Declared License": "MIT; BSD-3-Clause"})], config_for(rule))
    assert status == 1
    assert data[0]["license"] == "TBD"
    assert data[0]["licenses_to_verify"]["declared"] == ["MIT", "BSD-3-Clause"]
    assert data[0]["notices"] == rule["notices"]


@pytest.mark.parametrize("field", ["license", "copyright_holder", "license_text"])
def test_unresolved_supporting_notice_blocks_completeness(tmp_path, capsys, field):
    rule = resolution(notices=[supporting_notice(**{field: "TBD"})])
    status, data = invoke(tmp_path, config=config_for(rule))
    assert status == 1
    assert data[0]["license"] == "MIT"
    assert data[0]["notices"][0][field] == "TBD"
    assert f"notices[0].{field}" in capsys.readouterr().err


def test_reviewed_missing_upstream_copyright_is_accepted(tmp_path):
    fallback = "Copyright holder not declared by upstream"
    rule = resolution(copyright_holder=fallback,
                      notices=[supporting_notice(copyright_holder=fallback)])
    status, data = invoke(tmp_path, [component(Copyrights="")], config_for(rule))
    assert status == 0
    assert data[0]["copyright_holder"] == fallback
    assert data[0]["notices"][0]["copyright_holder"] == fallback


def test_missing_fossa_copyright_still_requires_upstream_review(tmp_path):
    status, data = invoke(tmp_path, [component(Copyrights="")])
    assert status == 1
    assert data[0]["copyright_holder"] == "TBD"


@pytest.mark.parametrize("notices", [
    {}, ["BSD-3-Clause"], [supporting_notice(extra="unsupported")],
    [supporting_notice(name="")], [supporting_notice(source="")],
    [supporting_notice(reason="")], [supporting_notice(license="MIT AND BSD-3-Clause")],
    [supporting_notice(license="")], [supporting_notice(license_text="unknown")],
    [supporting_notice(copyright_holder="Copyright [yyyy] <copyright holder>")],
    [supporting_notice(notice_text=None)], [supporting_notice(), supporting_notice()],
])
def test_invalid_supporting_notices_are_rejected(tmp_path, notices):
    assert invoke(tmp_path, config=config_for(resolution(notices=notices)))[0] == 2


def test_supporting_notice_required_fields_and_optional_notice_text(tmp_path):
    notice = supporting_notice()
    del notice["source"]
    assert invoke(tmp_path, config=config_for(resolution(notices=[notice])))[0] == 2
    notice = supporting_notice()
    del notice["notice_text"]
    status, data = invoke(tmp_path, config=config_for(resolution(notices=[notice])))
    assert status == 0
    assert data[0]["notices"] == [notice]


def test_review_can_explicitly_record_no_supporting_notices(tmp_path):
    rule = resolution(notices=[])
    del rule["license"]
    status, data = invoke(tmp_path, config=config_for(rule))
    assert status == 0
    assert data[0]["notices"] == []


def test_known_required_notice_with_missing_text_blocks_completeness(tmp_path, capsys):
    rule = resolution(notices=[supporting_notice(notice_text="TBD")])
    status, data = invoke(tmp_path, config=config_for(rule))
    assert status == 1
    assert data[0]["notices"][0]["notice_text"] == "TBD"
    assert "notices[0].notice_text" in capsys.readouterr().err
