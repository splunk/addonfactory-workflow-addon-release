"""Offline provenance and reviewed-rule validation contracts."""

from copy import deepcopy
import hashlib
import json

import pytest

from scripts.fossa_json_draft import build_draft
from scripts.generate_draft_credits import REQUEST_OPTIONS, attribution_url
from scripts.fossa_json_final import build_credits, load_config, validate_draft, verify_provenance


REVISION = "a" * 40
SETTINGS = {"ta_name": "Example_TA", "display_name": "Example TA",
            "fossa_project_locator": "custom+1/example"}


@pytest.fixture
def trio(tmp_path):
    report = {"project": {"name": "example", "revision": REVISION},
              "directDependencies": [{"title": "example", "version": "1.0",
                  "package": "example", "source": "pip", "downloadUrl": None,
                  "dependencyPaths": ["example"], "licenses": [{"name": "MIT",
                      "attribution": "Copyright 2026 Example\nMIT terms"}],
                  "otherLicenses": [], "noticeFiles": []}], "deepDependencies": []}
    raw = json.dumps(report).encode()
    digest = hashlib.sha256(raw).hexdigest()
    raw_name = "Example_TA-1.0-fossa-attribution.json"
    metadata_name = "Example_TA-1.0-fossa-attribution.request.json"
    draft = build_draft(report, SETTINGS, "1.0", REVISION, digest, raw_name, metadata_name)
    metadata = dict(SETTINGS, ta_version="1.0", selected_revision=REVISION,
        response_revision=REVISION, raw_sha256=digest, fetched_at="2026-10-27T00:00:00+00:00",
        request_options=REQUEST_OPTIONS, direct_dependencies=1, deep_dependencies=0,
        api_url=attribution_url(SETTINGS["fossa_project_locator"], REVISION))
    (tmp_path / raw_name).write_bytes(raw)
    (tmp_path / metadata_name).write_text(json.dumps(metadata))
    path = tmp_path / "Example_TA-1.0-credits-draft.json"
    path.write_text(json.dumps(draft))
    return draft, path, metadata, report


def test_tampered_raw_metadata_or_draft_rejected(trio):
    draft, path, metadata, report = trio
    validate_draft(draft)
    verify_provenance(draft, path)
    for field in ("name", "version", "disclaimer"):
        changed = deepcopy(draft)
        changed[field] = "changed"
        with pytest.raises(ValueError):
            verify_provenance(changed, path)
    for field in ("license", "copyright_holder", "license_text", "project_url"):
        changed = deepcopy(draft)
        changed["components"][0][field] = "changed"
        with pytest.raises(ValueError):
            verify_provenance(changed, path)
    changed = deepcopy(draft)
    changed["components"][0]["fossa_evidence"]["licenses"][0]["attribution"] = "changed"
    with pytest.raises(ValueError):
        verify_provenance(changed, path)
    changed = deepcopy(draft)
    changed["components"][0]["licenses_to_verify"]["licenses"].append("BSD")
    with pytest.raises(ValueError):
        verify_provenance(changed, path)
    for field in ("raw_sha256", "selected_revision", "response_revision", "ta_version",
                  "display_name", "fossa_project_locator", "ta_name", "direct_dependencies"):
        changed = deepcopy(metadata)
        changed[field] = "changed"
        (path.parent / draft["provenance"]["request_metadata_file"]).write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            verify_provenance(draft, path)
    (path.parent / draft["provenance"]["request_metadata_file"]).write_text(json.dumps(metadata))
    raw_path = path.parent / draft["provenance"]["raw_report_file"]
    raw_path.write_bytes(raw_path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        verify_provenance(draft, path)
    report["project"]["revision"] = "b" * 40
    raw = json.dumps(report).encode()
    raw_path.write_bytes(raw)
    draft["provenance"]["raw_sha256"] = metadata["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    (path.parent / draft["provenance"]["request_metadata_file"]).write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        verify_provenance(draft, path)


@pytest.mark.parametrize("filename", ["../outside.json", "/tmp/outside.json", "a/b.json",
                                       "a\\b.json", "..", ".", ""])
def test_provenance_filename_traversal_rejected(trio, filename):
    draft, path, _, _ = trio
    for field in ("raw_report_file", "request_metadata_file"):
        changed = deepcopy(draft)
        changed["provenance"][field] = filename
        with pytest.raises(ValueError):
            verify_provenance(changed, path)


def config_file(tmp_path, config):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(config))
    return path


def legacy_rule():
    return {"name": "example", "version": "1.0", "reason": "Exact artifact checked",
            "source": "https://example.org/1.0/LICENSE", "license": "MIT",
            "copyright_holder": "Copyright holder not declared by upstream",
            "license_text": "Full MIT text", "notices": [{"name": "helper",
                "license": "BSD-3-Clause", "copyright_holder": "TBD", "license_text": "TBD",
                "notice_text": "TBD", "source": "exact path", "reason": "Ships"}]}


def config(rule=None):
    return {"schema_version": 1, "resolutions": [rule or legacy_rule()], "exclusions": []}


def evidence():
    return {"candidates": [{"source_group": "otherLicenses", "name": "MIT",
                "disposition": "unresolved", "reason": "Need artifact", "references": ["exact ref"]}],
            "copyright_sources": ["exact LICENSE"],
            "distribution_findings": [{"finding": "Ships helper", "references": ["TA path"]}]}


def test_legacy_aws_style_rule_accepted(tmp_path):
    value = config()
    assert load_config(config_file(tmp_path, value)) == value
    for field in ("license", "copyright_holder", "license_text"):
        rule = legacy_rule()
        rule.pop(field)
        assert load_config(config_file(tmp_path, config(rule))) == config(rule)


def test_structured_evidence_validation(tmp_path):
    rule = legacy_rule()
    rule["evidence"] = evidence()
    for disposition in ("main", "supporting", "inapplicable", "unresolved"):
        rule["evidence"]["candidates"][0]["disposition"] = disposition
        assert load_config(config_file(tmp_path, config(rule))) == config(rule)
    bad = []
    for field, value in (("source_group", "declared"), ("disposition", "excluded"),
                         ("reason", " "), ("name", None), ("references", []),
                         ("references", [""]), ("unknown", True)):
        item = evidence()
        item["candidates"][0][field] = value
        bad.append(item)
    for field, value in (("copyright_sources", "path"), ("copyright_sources", [None]),
                         ("distribution_findings", [{}]), ("unknown", [])):
        item = evidence()
        item[field] = value
        bad.append(item)
    item = evidence()
    item["candidates"].append(deepcopy(item["candidates"][0]))
    bad.append(item)
    for item in bad:
        rule["evidence"] = item
        with pytest.raises(ValueError):
            load_config(config_file(tmp_path, config(rule)))


def test_duplicate_and_overlapping_rules_rejected(tmp_path):
    value = config()
    value["resolutions"].append(legacy_rule())
    with pytest.raises(ValueError):
        load_config(config_file(tmp_path, value))
    value = config()
    value["exclusions"] = [{"name": "example", "version": "1.0", "reason": "Absent"}]
    with pytest.raises(ValueError):
        load_config(config_file(tmp_path, value))
    path = tmp_path / "rules.json"
    path.write_text('{"schema_version":1,"schema_version":1,"resolutions":[],"exclusions":[]}')
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize("field,value", [("license", " "), ("license", "MIT OR BSD"),
    ("copyright_holder", " "), ("copyright_holder", "<year> <copyright holder>"), ("license_text", "unknown"),
    ("version", "1.*"), ("typo", "bad"), ("notices", [{}]), ("notices", None)])
def test_invalid_rules_rejected(tmp_path, field, value):
    rule = legacy_rule()
    rule[field] = value
    with pytest.raises(ValueError):
        load_config(config_file(tmp_path, config(rule)))


def test_unfilled_template_cannot_be_a_reviewed_license_text(tmp_path):
    rule = legacy_rule()
    rule["license_text"] = "MIT License\nCopyright (c) <year> <copyright holders>\n"
    with pytest.raises(ValueError, match="Unresolved license text placeholder"):
        load_config(config_file(tmp_path, config(rule)))


def test_boilerplate_cannot_be_a_reviewed_copyright_holder(tmp_path):
    rule = legacy_rule()
    rule["copyright_holder"] = (
        "Copyright 2026 Example\n"
        "copyright notice, this list of conditions and the following disclaimer"
    )
    with pytest.raises(ValueError, match="boilerplate"):
        load_config(config_file(tmp_path, config(rule)))


def test_generic_text_blocks_final_even_with_reviewed_copyright(trio):
    draft = deepcopy(trio[0])
    component = draft["components"][0]
    component["license_text"] = "MIT License\nCopyright (c) <year> <copyright holders>\n"
    component["copyright_holder"] = "Copyright 2026 Real Holder"
    rule = {"name": "example", "version": "1.0", "reason": "Holder checked",
            "source": "https://example.org/1.0/license", "copyright_holder": "Copyright 2026 Real Holder"}
    _, diagnostics, status = build_credits(draft, config(rule))
    assert status == 1
    assert any("license_text contains an unfilled copyright holder" in line
               for line in diagnostics)


@pytest.mark.parametrize("mutation", ["root", "duplicate", "missing", "candidates", "text_candidates",
                                      "evidence", "native", "notices"])
def test_draft_shape_rejected(trio, mutation):
    draft = deepcopy(trio[0])
    component = draft["components"][0]
    if mutation == "root":
        draft["unexpected"] = True
    elif mutation == "duplicate":
        draft["components"].append(deepcopy(component))
    elif mutation == "missing":
        component.pop("license_text")
    elif mutation == "candidates":
        component["licenses_to_verify"]["licenses"] = [{}]
    elif mutation == "text_candidates":
        component["licenses_text_to_verify"]["licenses"] = ["text"]
    elif mutation == "evidence":
        component["fossa_evidence"]["dependency_kind"] = "indirect"
    elif mutation == "native":
        component["fossa_evidence"]["licenses"][0]["name"] = ""
    else:
        component["notices"] = [{}]
    with pytest.raises(ValueError):
        validate_draft(draft)


def test_missing_symlink_and_non_utf8_provenance_rejected(trio, tmp_path):
    draft, path, _, _ = trio
    raw_path = tmp_path / draft["provenance"]["raw_report_file"]
    original = raw_path.read_bytes()
    raw_path.unlink()
    with pytest.raises(ValueError):
        verify_provenance(draft, path)
    outside = tmp_path / "target.json"
    outside.write_bytes(original)
    raw_path.symlink_to(outside)
    with pytest.raises(ValueError):
        verify_provenance(draft, path)
    raw_path.unlink()
    raw_path.write_bytes(b'\xff')
    with pytest.raises(ValueError):
        verify_provenance(draft, path)


def test_structured_candidate_group_requires_exact_native_value(tmp_path):
    rule = legacy_rule()
    rule["evidence"] = evidence()
    rule["evidence"]["candidates"][0]["source_group"] = " otherLicenses "
    with pytest.raises(ValueError):
        load_config(config_file(tmp_path, config(rule)))


def test_native_extra_field_boolean_tampering_rejected(trio):
    draft, path, metadata, report = trio
    report["directDependencies"][0]["licenses"][0]["native_flag"] = True
    raw = json.dumps(report).encode()
    digest = hashlib.sha256(raw).hexdigest()
    (path.parent / draft["provenance"]["raw_report_file"]).write_bytes(raw)
    metadata["raw_sha256"] = digest
    (path.parent / draft["provenance"]["request_metadata_file"]).write_text(json.dumps(metadata))
    draft = build_draft(report, SETTINGS, "1.0", REVISION, digest,
                        draft["provenance"]["raw_report_file"],
                        draft["provenance"]["request_metadata_file"])
    verify_provenance(draft, path)
    draft["components"][0]["fossa_evidence"]["licenses"][0]["native_flag"] = 1
    with pytest.raises(ValueError):
        verify_provenance(draft, path)


def reviewed_rule(**overrides):
    return dict(name="example", version="1.0", source="exact artifact/LICENSE",
                reason="Inspected exact artifact", **overrides)


def test_partial_overrides_and_notices_preserved(trio):
    from scripts.fossa_json_final import build_credits

    draft = deepcopy(trio[0])
    component = draft["components"][0]
    component["licenses_to_verify"] = {"licenses": ["MIT"], "otherLicenses": ["BSD"]}
    component["licenses_text_to_verify"] = {"licenses": [{"name": "MIT", "text": "full text"}],
                                           "otherLicenses": []}
    notice = dict(legacy_rule()["notices"][0], copyright_holder="Copyright 2025 Helper",
                  license="MIT", license_text="Helper full text\n\n", notice_text="Helper NOTICE\n")
    component["notices"] = [notice]
    original = deepcopy(draft)
    for field, value in (
        ("license", "BSD-3-Clause"),
        ("license_text", "Exact complete text\n\n"),
    ):
        credits, diagnostics, status = build_credits(draft, config(reviewed_rule(**{field: value})))
        result = credits["components"][0]
        assert status == 0 and diagnostics == []
        assert set(credits) == {"name", "version", "disclaimer", "components"}
        assert set(result) == {"name", "version", "license", "copyright_holder", "license_text",
                               "project_url", "notices"}
        assert result[field] == value
        assert result["notices"] == [{k: v for k, v in notice.items()
                                      if k not in ("reason", "source")}]
        assert credits["disclaimer"] == draft["disclaimer"]
    credits, _, status = build_credits(draft, config(reviewed_rule(notices=[])))
    assert status == 0 and credits["components"][0]["notices"] == []
    assert draft == original


def test_reviewed_empty_copyright_is_distinct_from_tbd(trio, tmp_path):
    from scripts.fossa_json_final import build_credits

    draft = deepcopy(trio[0])
    draft["components"][0]["copyright_holder"] = "TBD"
    rule = reviewed_rule(copyright_holder="", notices=[{
        "name": "embedded helper", "license": "MIT", "copyright_holder": "",
        "license_text": "Complete MIT terms", "source": "exact helper path",
        "reason": "Checked exact helper source; no holder declared",
    }])
    assert load_config(config_file(tmp_path, config(rule))) == config(rule)
    credits, diagnostics, status = build_credits(draft, config(rule))
    component = credits["components"][0]
    assert status == 0 and diagnostics == []
    assert component["copyright_holder"] == ""
    assert component["notices"][0]["copyright_holder"] == ""
    assert "reason" not in component["notices"][0]
    assert "source" not in component["notices"][0]

    credits, diagnostics, status = build_credits(draft, {"schema_version": 1,
        "resolutions": [], "exclusions": []})
    assert status == 1 and credits["components"][0]["copyright_holder"] == "TBD"
    assert any("copyright_holder is TBD" in item for item in diagnostics)


def test_notice_applicability_is_separate_from_known_license(trio, tmp_path):
    notice = {
        "name": "runtime/helper.go", "license": "BSD-3-Clause",
        "copyright_holder": "Copyright Example Contributors",
        "license_text": "Complete BSD-3-Clause terms", "source": "exact artifact/helper.go",
        "reason": "Distribution or derivation still needs review",
        "applicability": "unresolved",
    }
    rule = reviewed_rule(notices=[notice])
    assert load_config(config_file(tmp_path, config(rule))) == config(rule)
    credits, diagnostics, status = build_credits(trio[0], config(rule))
    assert status == 1
    assert credits["components"][0]["notices"] == [{
        key: value for key, value in notice.items()
        if key not in ("reason", "source", "applicability")
    }]
    assert any("notice runtime/helper.go: applicability unresolved" in item
               for item in diagnostics)
    assert not any("license is TBD" in item for item in diagnostics)

    notice["applicability"] = "confirmed"
    credits, diagnostics, status = build_credits(trio[0], config(rule))
    assert status == 0 and diagnostics == []
    assert len(credits["components"][0]["notices"]) == 1

    notice["applicability"] = "not_shipped"
    credits, diagnostics, status = build_credits(trio[0], config(rule))
    assert status == 0 and diagnostics == []
    assert credits["components"][0]["notices"] == []


@pytest.mark.parametrize("value", ["", "TBD", "maybe", None, 1, True, []])
def test_notice_applicability_rejects_unknown_values(tmp_path, value):
    rule = legacy_rule()
    rule["notices"][0]["applicability"] = value
    with pytest.raises(ValueError):
        load_config(config_file(tmp_path, config(rule)))


def test_unresolved_evidence_or_unreviewed_notice_exits_one(trio):
    from scripts.fossa_json_final import build_credits

    draft = deepcopy(trio[0])
    component = draft["components"][0]
    component["fossa_evidence"]["noticeFiles"] = [{"path": "NOTICE", "contents": "",
        "copyrights": [], "ignored": False, "corrected": True}]
    credits, diagnostics, status = build_credits(draft, config(reviewed_rule(license="MIT")))
    assert status == 1 and credits["components"][0]["license"] == "MIT"
    assert any("example" in item and "1.0" in item and "notice" in item.lower() for item in diagnostics)
    _, _, status = build_credits(draft, config(reviewed_rule(notices=[])))
    assert status == 0
    component["fossa_evidence"]["noticeFiles"][0]["ignored"] = True
    _, _, status = build_credits(draft, {"schema_version": 1, "resolutions": [], "exclusions": []})
    assert status == 0

    rule = reviewed_rule(notices=[], evidence=evidence())
    candidate = rule["evidence"]["candidates"][0]
    candidate.update(source_group="licenses", name="mit", disposition="main")
    _, _, status = build_credits(draft, config(rule))
    assert status == 0  # The full native MIT candidate still requires a decision after auto-selection.
    for mutation, expected in (("unresolved", "unresolved"), ("missing", "missing"), ("extra", "extra")):
        changed = deepcopy(rule)
        candidates = changed["evidence"]["candidates"]
        if mutation == "unresolved":
            candidates[0]["disposition"] = "unresolved"
        elif mutation == "missing":
            candidates.clear()
        else:
            candidates.append(dict(candidate, source_group="otherLicenses"))
        _, diagnostics, status = build_credits(draft, config(changed))
        assert status == 1
        assert any("example" in item and expected in item.lower() and "candidate" in item.lower()
                   for item in diagnostics)


@pytest.mark.parametrize("field", ["license", "copyright_holder", "license_text"])
def test_unresolved_main_and_notice_fields_named(trio, field):
    from scripts.fossa_json_final import build_credits

    draft = deepcopy(trio[0])
    draft["components"][0][field] = "TBD"
    _, diagnostics, status = build_credits(draft, {"schema_version": 1, "resolutions": [], "exclusions": []})
    assert status == 1
    assert any("example" in item and field in item for item in diagnostics)
    draft = deepcopy(trio[0])
    notice = dict(legacy_rule()["notices"][0], license="MIT",
                  copyright_holder="Copyright 2025 Helper", license_text="Full helper text",
                  notice_text="Complete notice")
    notice[field] = "TBD"
    rule = reviewed_rule(notices=[notice])
    _, diagnostics, status = build_credits(draft, config(rule))
    assert status == 1
    assert any("helper" in item and field in item for item in diagnostics)


def test_exclusions_and_unmatched_rules(trio):
    from scripts.fossa_json_final import build_credits

    draft = deepcopy(trio[0])
    draft["components"][0]["license"] = "TBD"
    second = deepcopy(draft["components"][0])
    second.update(name="second", license="MIT")
    draft["components"].append(second)
    value = config()
    value["resolutions"] = [dict(reviewed_rule(license="MIT"), name="absent")]
    value["exclusions"] = [{"name": "example", "version": "1.0", "reason": "Not distributed"},
                            {"name": "absent-too", "version": "1.0", "reason": "Gone"}]
    credits, diagnostics, status = build_credits(draft, value)
    assert status == 0
    assert [item["name"] for item in credits["components"]] == ["second"]
    assert any("example" in item and "Not distributed" in item for item in diagnostics)
    assert any("absent" in item and "unmatched" in item.lower() for item in diagnostics)
    assert any("absent-too" in item and "unmatched" in item.lower() for item in diagnostics)


def test_final_cli_writes_on_one_and_zero(trio, tmp_path, capsys, monkeypatch):
    from scripts.generate_credits import main

    draft, path, _, _ = trio
    rules = config_file(tmp_path, config())
    output_dir = tmp_path / "results"
    output = output_dir / "Example_TA-1.0-credits.json"
    args = ["--draft", str(path), "--config", str(rules), "--output-dir", str(output_dir)]
    monkeypatch.chdir(tmp_path.parent)
    assert main(args) == 1
    assert output.is_file()
    assert "helper" in capsys.readouterr().err
    incomplete = json.loads(output.read_text())
    assert "provenance" not in incomplete
    assert set(incomplete["components"][0]) == {"name", "version", "license",
                                               "copyright_holder", "license_text", "project_url", "notices"}
    rules.write_text(json.dumps(config(reviewed_rule(notices=[]))))
    assert main(args) == 0
    result = json.loads(output.read_text())
    assert result["name"] == "Example TA"
    assert set(result["components"][0]) == {"name", "version", "license",
                                           "copyright_holder", "license_text", "project_url", "notices"}
    assert result["components"][0]["notices"] == []
    assert not list(output_dir.glob("*.tmp"))
    monkeypatch.chdir(output_dir)
    assert main(["--draft", str(path), "--config", str(rules)]) == 0


@pytest.mark.parametrize("mutation", ["raw", "metadata", "draft", "config", "filename", "collision"])
def test_invalid_provenance_exits_two_without_replacement(trio, tmp_path, capsys, mutation):
    from scripts.generate_credits import main

    draft, path, metadata, _ = trio
    output = tmp_path / "Example_TA-1.0-credits.json"
    output.write_text("prior successful credits\n")
    rules = config_file(tmp_path, config(reviewed_rule(notices=[])))
    if mutation == "raw":
        (tmp_path / draft["provenance"]["raw_report_file"]).write_text("{}")
    elif mutation == "metadata":
        metadata["selected_revision"] = "b" * 40
        (tmp_path / draft["provenance"]["request_metadata_file"]).write_text(json.dumps(metadata))
    elif mutation == "draft":
        draft["components"][0]["license"] = "Changed"
        path.write_text(json.dumps(draft))
    elif mutation == "config":
        rules.write_text('{"schema_version": 1, "resolutions": null, "exclusions": []}')
    elif mutation == "filename":
        draft["provenance"]["raw_report_file"] = "../raw.json"
        path.write_text(json.dumps(draft))
    else:
        output.write_text(rules.read_text())
        rules = output
    prior = output.read_bytes()
    assert main(["--draft", str(path), "--config", str(rules), "--output-dir", str(tmp_path)]) == 2
    assert output.read_bytes() == prior
    assert capsys.readouterr().err


def test_cli_atomic_failure_preserves_prior_output(trio, tmp_path, monkeypatch):
    from scripts import generate_credits

    output = tmp_path / "Example_TA-1.0-credits.json"
    output.write_text("prior successful credits")
    rules = config_file(tmp_path, config(reviewed_rule(notices=[])))
    def fail_replace(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(generate_credits.os, "replace", fail_replace)
    assert generate_credits.main(["--draft", str(trio[1]), "--config", str(rules),
                                  "--output-dir", str(tmp_path)]) == 2
    assert output.read_text() == "prior successful credits"
    assert sorted(item.name for item in tmp_path.iterdir()) == sorted([
        "Example_TA-1.0-credits.json", "Example_TA-1.0-credits-draft.json",
        "Example_TA-1.0-fossa-attribution.json", "Example_TA-1.0-fossa-attribution.request.json", "rules.json"])


def test_explicit_notice_text_tbd_is_unresolved(trio):
    from scripts.fossa_json_final import build_credits

    notice = dict(legacy_rule()["notices"][0], license="MIT",
                  copyright_holder="Copyright 2025 Helper", license_text="Full helper text")
    _, diagnostics, status = build_credits(trio[0], config(reviewed_rule(notices=[notice])))
    assert status == 1
    assert any("helper" in item and "notice_text" in item for item in diagnostics)


def test_cli_script_entrypoint_and_missing_arguments(trio, tmp_path):
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_credits.py"
    rules = config_file(tmp_path, config(reviewed_rule(notices=[])))
    run = subprocess.run([sys.executable, str(script), "--draft", str(trio[1]),
                          "--config", str(rules)], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "Example_TA-1.0-credits.json").is_file()
    run = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert run.returncode == 2 and "--draft" in run.stderr and "--config" in run.stderr


def test_documented_json_settings_example():
    from pathlib import Path
    from scripts.generate_draft_credits import load_settings

    example = Path(__file__).resolve().parents[1] / "runbooks/examples/fossa-json-settings.json"
    settings = load_settings(example)
    assert settings["schema_version"] == 1
    assert settings["ta_name"] == "Splunk_TA_aws"


def test_documented_json_resolution_example():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "runbooks/examples/fossa-json-license-resolutions.json"
    rules = load_config(example)
    assert len(rules["resolutions"]) == 1
    rule = rules["resolutions"][0]
    assert rule["notices"] == []
    assert {item["source_group"] for item in rule["evidence"]["candidates"]} == {
        "licenses", "otherLicenses"}
    assert {item["disposition"] for item in rule["evidence"]["candidates"]} == {
        "main", "unresolved"}


@pytest.mark.parametrize("duplicate_name", ["mit", " MIT ", " MiT "])
def test_duplicate_normalized_candidate_decisions_rejected(tmp_path, duplicate_name):
    rule = legacy_rule()
    rule["evidence"] = evidence()
    first = rule["evidence"]["candidates"][0]
    first.update(source_group="licenses", name="MIT", disposition="main")
    rule["evidence"]["candidates"].append(
        dict(first, name=duplicate_name, disposition="inapplicable"))
    with pytest.raises(ValueError, match="duplicate native candidate"):
        load_config(config_file(tmp_path, config(rule)))
