"""Exact-version cache lookup for the local FOSSA resolution skill."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = (Path(__file__).parents[1] / ".agents" / "skills" /
          "resolve-fossa-credits" / "scripts" / "lookup_pristine_cache.py")
SPEC = importlib.util.spec_from_file_location("lookup_pristine_cache", SCRIPT)
lookup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lookup)
REFRESH_SCRIPT = SCRIPT.with_name("refresh_pristine_cache.py")
REFRESH_SPEC = importlib.util.spec_from_file_location("refresh_pristine_cache", REFRESH_SCRIPT)
refresh = importlib.util.module_from_spec(REFRESH_SPEC)
REFRESH_SPEC.loader.exec_module(refresh)


def fixture_cache(tmp_path):
    document = {
        "widget": [
            {"version": "1.0", "spdx_id": "MIT", "license_text": "MIT text",
             "copyright": "Copyright 2025 Widget Authors"},
            {"version": "2.0", "spdx_id": "Apache-2.0",
             "copyright_derived": ["Copyright 2026 Contributors"],
             "copyright_override": ["Copyright 2026 Reviewed Owner"]},
        ]
    }
    raw = json.dumps(document).encode()
    compressed = gzip.compress(raw, mtime=0)
    cache = tmp_path / "cache.json.gz"
    cache.write_bytes(compressed)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "repository": "https://example.test/cache",
        "commit": "a" * 40,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
    }))
    return cache, manifest


def test_exact_version_and_holder_precedence(tmp_path):
    cache, manifest = fixture_cache(tmp_path)
    document, source = lookup.load_cache(cache, manifest)
    entry = lookup.lookup(document, "widget", "2.0")
    summary = lookup.summarize("widget", "2.0", entry, source)
    assert summary["license"] == "Apache-2.0"
    assert summary["preferred_copyright"] == {
        "field": "copyright_override", "value": ["Copyright 2026 Reviewed Owner"]}
    assert "entry" not in summary
    assert lookup.summarize("widget", "2.0", entry, source, full=True)["entry"] == entry
    assert lookup.lookup(document, "widget", "2.1") is None
    assert lookup.lookup(document, "Widget", "2.0") is None


def test_tampered_cache_is_rejected(tmp_path):
    cache, manifest = fixture_cache(tmp_path)
    cache.write_bytes(cache.read_bytes() + b"x")
    with pytest.raises(ValueError, match="compressed SHA-256"):
        lookup.load_cache(cache, manifest)


def test_pinned_cache_has_source_commit_and_exact_matches():
    document, manifest = lookup.load_cache()
    assert len(manifest["commit"]) == 40
    assert lookup.lookup(document, "@splunk/splunk-utils", "2.1.0") is None
    assert lookup.lookup(document, "certifi", "2025.10.5") is None
    assert lookup.lookup(document, "certifi", "2024.12.14") is not None


def test_cli_found_missing_and_invalid_cache(tmp_path, capsys):
    cache, manifest = fixture_cache(tmp_path)
    common = ["--cache", str(cache), "--manifest", str(manifest)]
    assert lookup.main(common + ["--name", "widget", "--version", "1.0", "--full"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["entry"]["license_text"] == "MIT text"
    assert found["license_text_sha256"] == hashlib.sha256(b"MIT text").hexdigest()
    assert found["preferred_copyright"]["field"] == "copyright"

    assert lookup.main(common + ["--name", "widget", "--version", "3.0"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["found"] is False

    cache.write_bytes(b"broken")
    assert lookup.main(common + ["--name", "widget", "--version", "1.0"]) == 2
    assert "SHA-256 differs" in capsys.readouterr().err


def test_duplicate_and_invalid_version_lists_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        lookup.lookup({"widget": [{"version": "1"}, {"version": "1"}]}, "widget", "1")
    with pytest.raises(ValueError, match="invalid"):
        lookup.lookup({"widget": "not a list"}, "widget", "1")


def test_refresh_writes_reproducible_verified_snapshot(tmp_path):
    raw = b'{"widget":[{"version":"1.0","spdx_id":"MIT"}]}'
    first = refresh.write_snapshot(raw, "a" * 40, tmp_path, "2026-10-29")
    compressed = (tmp_path / "pristine-cache.json.gz").read_bytes()
    second = refresh.write_snapshot(raw, "a" * 40, tmp_path, "2026-10-29")
    assert first == second
    assert (tmp_path / "pristine-cache.json.gz").read_bytes() == compressed
    document, source = lookup.load_cache(
        tmp_path / "pristine-cache.json.gz", tmp_path / "pristine-cache-source.json")
    assert source["entries"] == 1
    assert lookup.lookup(document, "widget", "1.0")["spdx_id"] == "MIT"


def test_refresh_rejects_invalid_cache(tmp_path):
    with pytest.raises(ValueError, match="invalid"):
        refresh.write_snapshot(b"[]", "a" * 40, tmp_path, "2026-10-29")
