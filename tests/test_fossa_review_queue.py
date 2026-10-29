"""The skill worklist groups all unresolved fields by exact component."""

import importlib.util
from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[1] / ".agents" / "skills"
          / "resolve-fossa-credits" / "scripts" / "list_review_queue.py")
SPEC = importlib.util.spec_from_file_location("list_review_queue", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_worklist_keeps_main_and_nested_gaps_in_component_order():
    credits = {"components": [
        {"name": "chalk", "version": "2.4.2", "license": "MIT",
         "copyright_holder": "TBD", "license_text": "TBD", "notices": []},
        {"name": "clear", "version": "1", "license": "MIT",
         "copyright_holder": "", "license_text": "complete", "notices": []},
        {"name": "arrow", "version": "v15", "license": "Apache-2.0",
         "copyright_holder": "Copyright Example", "license_text": "complete",
         "notices": [{"name": "subproject", "license": "TBD",
                      "copyright_holder": "", "license_text": "TBD"}]},
    ]}
    assert MODULE.pending_components(credits) == [
        ("chalk", "2.4.2", ["copyright_holder", "license_text"]),
        ("arrow", "v15", ["notice subproject: license",
                          "notice subproject: license_text"]),
    ]


def test_worklist_includes_diagnostic_only_review_blockers():
    credits = {"components": [
        {"name": "boto3", "version": "1.0", "license": "Apache-2.0",
         "copyright_holder": "", "license_text": "complete", "notices": []},
        {"name": "chalk", "version": "2.4.2", "license": "MIT",
         "copyright_holder": "TBD", "license_text": "complete", "notices": []},
        {"name": "arrow", "version": "v15", "license": "Apache-2.0",
         "copyright_holder": "Copyright Example", "license_text": "complete",
         "notices": [{"name": "NumPy", "license": "BSD-3-Clause",
                      "copyright_holder": "Copyright NumPy Developers",
                      "license_text": "complete"}]},
    ]}
    diagnostics = [
        "Unresolved boto3 1.0: notice review required: unignored FOSSA noticeFiles need an explicit notices rule",
        "Unresolved chalk 2.4.2: copyright_holder is TBD",
        "Unresolved arrow v15: notice NumPy: applicability unresolved: provenance pending",
        "Warning: unmatched resolution rule: old 1.0",
    ]
    assert MODULE.pending_reviews(credits, diagnostics) == [
        ("boto3", "1.0", ["notice review required: unignored FOSSA noticeFiles need an explicit notices rule"]),
        ("chalk", "2.4.2", ["copyright_holder"]),
        ("arrow", "v15", ["notice NumPy: applicability unresolved: provenance pending"]),
    ]


def test_cli_exits_one_and_prints_diagnostic_only_blockers(monkeypatch, capsys):
    credits = {"components": [{"name": "boto3", "version": "1.0",
                               "license": "Apache-2.0", "copyright_holder": "",
                               "license_text": "complete", "notices": []}]}
    monkeypatch.setattr(MODULE, "_read_json", lambda _: (b"", {}))
    monkeypatch.setattr(MODULE, "verify_provenance", lambda *_: None)
    monkeypatch.setattr(MODULE, "load_config", lambda _: {})
    monkeypatch.setattr(
        MODULE, "build_credits",
        lambda *_: (credits, ["Unresolved boto3 1.0: notice review required"], 1),
    )
    assert MODULE.main(["--draft", "draft.json", "--config", "rules.json"]) == 1
    assert "boto3 1.0: notice review required" in capsys.readouterr().out


def test_cli_exits_zero_when_generator_is_complete(monkeypatch, capsys):
    monkeypatch.setattr(MODULE, "_read_json", lambda _: (b"", {}))
    monkeypatch.setattr(MODULE, "verify_provenance", lambda *_: None)
    monkeypatch.setattr(MODULE, "load_config", lambda _: {})
    monkeypatch.setattr(MODULE, "build_credits", lambda *_: ({"components": []}, [], 0))
    assert MODULE.main(["--draft", "draft.json", "--config", "rules.json"]) == 0
    assert "0 components need review" in capsys.readouterr().err
