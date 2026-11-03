"""Release-directory reuse precedes agent research without losing raw evidence."""

import json

import pytest

from scripts.generate_draft_credits import attribution_url, write_draft_artifacts
from scripts.generate_credits import main as generate_final
from scripts.fossa_json_final import verify_provenance


SETTINGS = {"ta_name": "Example_TA", "display_name": "Example Add-on",
            "fossa_project_locator": "custom+1234/example-add-on"}
REVISION = "a" * 40


def rule(name="stable", version="1.0", **overrides):
    value = {"name": name, "version": version, "license": "MIT",
             "copyright_holder": "No copyright holder stated in the distributed package.", "license_text": "Complete reviewed MIT terms",
             "notices": [], "reason": "Exact upstream artifact reviewed",
             "source": f"https://example.org/{name}/{version}/LICENSE"}
    value.update(overrides)
    return value


def write_rules(directory, *rules, **extra):
    path = directory / "fossa-license-resolutions.json"
    path.write_text(json.dumps({"schema_version": 1, "resolutions": list(rules),
                                "exclusions": [], **extra}))
    return path


def release(directory, version="1.2.4"):
    dependencies = [{"title": name, "version": dep_version, "package": name,
                     "source": "pip", "downloadUrl": None, "projectUrl": "https://example.org/" + name, "dependencyPaths": [name],
                     "licenses": [], "otherLicenses": [], "noticeFiles": []}
                    for name, dep_version in (("stable", "1.0"), ("changed", "2.0"), ("new", "1.0"))]
    raw = json.dumps({"project": {"name": "example-add-on", "revision": REVISION},
                      "directDependencies": dependencies, "deepDependencies": []}).encode()
    paths = write_draft_artifacts(raw, SETTINGS, version, REVISION, directory,
        attribution_url(SETTINGS["fossa_project_locator"], REVISION), "2000-01-01T00:00:00Z")
    return paths


def resolve(path, *args):
    from scripts.resolve_credits_gaps import main
    return main(["--draft", str(path), *args])


def test_previous_release_rules_prefill_exact_versions_then_agent_updates_final(tmp_path, capsys):
    # The user supplies only the previous release's editable configuration.
    rules = write_rules(tmp_path, rule(), rule("changed", "1.0"))
    seed_bytes = rules.read_bytes()
    raw, metadata, draft = release(tmp_path)
    original = json.loads(draft.read_bytes())
    evidence = (raw.read_bytes(), metadata.read_bytes())
    assert original["components"][0]["license"] == "TBD"  # Generation remains unchanged.

    assert resolve(draft) == 1
    output = capsys.readouterr()
    assert "changed 2.0:" in output.out and "new 1.0:" in output.out
    assert "stable 1.0:" not in output.out
    working = json.loads(draft.read_bytes())
    assert working["components"][0]["license"] == "MIT"
    assert working["components"][0]["copyright_holder"] == "No copyright holder stated in the distributed package."
    assert working["components"][1]["license"] == "TBD"
    assert working["components"][0]["fossa_evidence"] == original["components"][0]["fossa_evidence"]
    assert verify_provenance(working, draft) == original
    assert rules.read_bytes() == seed_bytes
    assert len(list(tmp_path.iterdir())) == 4  # No extra snapshots or state files.
    assert (raw.read_bytes(), metadata.read_bytes()) == evidence

    write_rules(tmp_path, rule(license_text="Corrected reviewed MIT terms"),
                rule("changed", "1.0"), rule("changed", "2.0"), rule("new"))
    assert resolve(draft) == 0
    assert generate_final(["--draft", str(draft), "--output-dir", str(tmp_path)]) == 0
    credits = json.loads((tmp_path / "Example_TA-1.2.4-credits.json").read_bytes())
    assert credits["components"][0]["license_text"] == "Corrected reviewed MIT terms"
    assert "provenance" not in credits and "resolution_provenance" not in credits
    assert all("fossa_evidence" not in item for item in credits["components"])
    assert (raw.read_bytes(), metadata.read_bytes()) == evidence


def test_latest_config_can_remove_a_prefilled_rule_without_leaking_old_values(tmp_path):
    write_rules(tmp_path, rule(), rule("changed", "2.0"), rule("new"))
    _, _, draft = release(tmp_path)
    assert resolve(draft) == 0
    write_rules(tmp_path, rule("changed", "2.0"), rule("new"))
    # Both final generation and the queue must rebuild from raw, not trust old prefill.
    assert generate_final(["--draft", str(draft), "--output-dir", str(tmp_path)]) == 1
    credits = json.loads((tmp_path / "Example_TA-1.2.4-credits.json").read_bytes())
    assert credits["components"][0]["license"] == "TBD"
    assert resolve(draft) == 1
    assert json.loads(draft.read_bytes())["components"][0]["license_text"] == "TBD"


@pytest.mark.parametrize("target", ["raw", "metadata"])
def test_tampering_is_rejected_and_previous_output_preserved(tmp_path, target):
    write_rules(tmp_path, rule())
    raw, metadata, draft = release(tmp_path)
    assert resolve(draft) == 1
    path = {"raw": raw, "metadata": metadata}[target]
    path.write_bytes(path.read_bytes() + b" ")
    if target == "metadata":
        data = json.loads(metadata.read_bytes())
        data["display_name"] = "changed"
        metadata.write_text(json.dumps(data))
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    assert resolve(draft) == 2
    assert generate_final(["--draft", str(draft), "--output-dir", str(tmp_path)]) == 2
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_missing_or_invalid_release_config_does_not_fall_back_or_modify_draft(tmp_path):
    write_rules(tmp_path, rule())  # A repository-root copy must not be selected.
    directory = tmp_path / "release"
    _, _, draft = release(directory)
    original = draft.read_bytes()
    assert resolve(draft) == 2
    rules = write_rules(directory, rule())
    rules.write_text('{"schema_version": 99}')
    assert resolve(draft) == 2
    assert draft.read_bytes() == original
    assert len(list(directory.iterdir())) == 4


def test_prefill_is_idempotent_and_keeps_notice_applicability_for_review(tmp_path, capsys):
    notice = {"name": "helper", "license": "MIT", "copyright_holder": "",
              "license_text": "Full helper terms", "reason": "Distribution pending",
              "source": "https://example.org/helper/LICENSE", "applicability": "unresolved"}
    write_rules(tmp_path, rule(notices=[notice]), rule("changed", "2.0"), rule("new"))
    _, _, draft = release(tmp_path)
    assert resolve(draft) == 1
    assert "applicability unresolved" in capsys.readouterr().out
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    assert resolve(draft) == 1
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before
    value = json.loads(draft.read_bytes())
    assert value["components"][0]["notices"][0] == notice


def test_symlinked_draft_and_input_collision_fail_without_overwriting(tmp_path):
    rules = write_rules(tmp_path, rule())
    _, _, draft = release(tmp_path)
    before = draft.read_bytes()
    assert resolve(draft, "--config", str(draft)) == 2
    assert draft.read_bytes() == before
    target = tmp_path / "target.json"
    draft.rename(target)
    draft.symlink_to(target)
    assert resolve(draft) == 2
    assert target.read_bytes() == before


def test_explicit_legacy_config_path_still_supported(tmp_path):
    write_rules(tmp_path, rule(), rule("changed", "2.0"), rule("new"))
    rules = tmp_path / "fossa-license-resolutions.json"
    legacy = tmp_path / "rules.json"
    rules.rename(legacy)
    _, _, draft = release(tmp_path)
    assert resolve(draft, "--config", str(legacy)) == 0
    assert generate_final(["--draft", str(draft), "--config", str(legacy),
                           "--output-dir", str(tmp_path)]) == 0


def test_manual_draft_prefill_is_accepted_but_never_leaks_into_final(tmp_path):
    write_rules(tmp_path, rule(), rule("changed", "2.0"), rule("new"))
    _, _, draft = release(tmp_path)
    value = json.loads(draft.read_bytes())
    value["components"][0]["license_text"] = "Stale or manually edited draft text"
    draft.write_text(json.dumps(value))
    assert generate_final(["--draft", str(draft), "--output-dir", str(tmp_path)]) == 0
    credits = json.loads((tmp_path / "Example_TA-1.2.4-credits.json").read_bytes())
    assert credits["components"][0]["license_text"] == "Complete reviewed MIT terms"


def test_direct_cli_and_agent_queue_use_release_local_latest_rules(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    write_rules(tmp_path, rule(), rule("changed", "2.0"), rule("new"))
    _, _, draft = release(tmp_path)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/resolve_credits_gaps.py"),
                             "--draft", str(draft)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    write_rules(tmp_path, rule("changed", "2.0"), rule("new"))
    queue = root / ".agents/skills/resolve-fossa-credits/scripts/list_review_queue.py"
    result = subprocess.run([sys.executable, str(queue), "--draft", str(draft)],
                            capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert "stable 1.0:" in result.stdout and "license_text" in result.stdout
    assert "changed 2.0:" not in result.stdout
