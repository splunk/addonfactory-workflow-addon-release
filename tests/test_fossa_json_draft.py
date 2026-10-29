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
"""Contracts for local FOSSA settings, release identity, and authenticated fetch."""

import importlib
import json
import subprocess
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit

import pytest


SETTINGS = {
    "schema_version": 1,
    "fossa_project_locator": "custom+8617/splunk-add-on-for-amazon-web-services",
    "ta_name": "Splunk_TA_aws",
    "display_name": "Splunk Add-on for Amazon Web Services",
}


def module():
    return importlib.import_module("scripts.generate_draft_credits")


def converter():
    return importlib.import_module("scripts.fossa_json_draft")


REVISION = "03aa8ea33a87169beb2c97b1399effe280684fd0"


def dependency(name="example", version="1.0", licenses=None, other=None, notices=None):
    return {
        "title": name, "version": version, "package": name, "source": "pip",
        "projectUrl": "https://example.test/" + name,
        "downloadUrl": "https://example.test/" + name + "/1.0.tar.gz",
        "dependencyPaths": [name], "licenses": licenses if licenses is not None else [],
        "otherLicenses": other if other is not None else [],
        "noticeFiles": notices if notices is not None else [],
    }


def attribution(name, text):
    return {"name": name, "attribution": text}


def report(*direct, deep=()):
    return {
        "project": {"name": "aws", "revision": REVISION},
        "directDependencies": list(direct), "deepDependencies": list(deep),
        "copyrightsByLicense": {"MIT": ["Copyright 2000 Global Owner"]},
    }


def draft(source):
    return converter().build_draft(
        source, SETTINGS, "8.2.2", REVISION, "a" * 64,
        "raw.json", "request.json",
    )


def test_requests_notice_sets_holder():
    license_text = "Apache License\nVersion 2.0\nfull original text\n"
    notice = {"path": "NOTICE", "contents": "Requests\nCopyright 2019 Kenneth Reitz\n",
              "copyrights": ["2019 Kenneth Reitz"], "ignored": False,
              "corrected": False}
    result = draft(report(dependency(
        "requests", "2.33.1", [attribution("Apache-2.0", license_text)],
        notices=[notice],
    )))
    assert set(result) == {"name", "version", "disclaimer", "components", "provenance"}
    assert result["provenance"]["revision"] == REVISION
    component = result["components"][0]
    assert component["license"] == "Apache-2.0"
    assert component["license_text"] == license_text
    assert component["copyright_holder"] == "Copyright 2019 Kenneth Reitz"
    assert component["notices"] == []
    assert component["fossa_evidence"]["noticeFiles"] == [notice]
    assert component["fossa_evidence"]["dependency_kind"] == "direct"
    assert component["licenses_to_verify"] == {"licenses": [], "otherLicenses": []}


def test_lodash_and_arrow_keep_named_candidates():
    lodash = dependency("lodash", "4.18.1", [attribution("MIT", "full MIT text"),
                                               attribution("CC0-1.0", "full CC0 text")])
    arrow = dependency("github.com/apache/arrow/go/v15", "v15.0.2", other=[
        attribution("Apache-2.0", "full Apache text"),
        attribution("MIT", "full MIT text"),
        attribution("public-domain", ""),
    ])
    components = draft(report(lodash, deep=[arrow]))["components"]
    assert [item["name"] for item in components] == ["lodash", "github.com/apache/arrow/go/v15"]
    for component, source in zip(components, (lodash, arrow)):
        assert component["license"] == component["license_text"] == "TBD"
        assert component["licenses_to_verify"] == {
            "licenses": [entry["name"] for entry in source["licenses"]],
            "otherLicenses": [entry["name"] for entry in source["otherLicenses"]],
        }
        assert component["licenses_text_to_verify"] == {
            key: [{"name": entry["name"], "text": entry["attribution"]}
                  for entry in source[key] if entry["attribution"]]
            for key in ("licenses", "otherLicenses")
        }
        assert component["fossa_evidence"]["licenses"] == source["licenses"]
        assert component["fossa_evidence"]["otherLicenses"] == source["otherLicenses"]
    assert components[1]["fossa_evidence"]["dependency_kind"] == "deep"


def test_singleton_and_with_exception():
    item = dependency(licenses=[attribution(" Apache-2.0 WITH LLVM-exception ", "Terms\r\nLine\r\n")],
                      other=[attribution("apache-2.0 with llvm-exception", "Terms\nLine\n")])
    component = converter().build_component(item, "direct")
    assert component["license"] == "Apache-2.0 WITH LLVM-exception"
    assert component["license_text"] == "Terms\r\nLine\r\n"
    assert component["licenses_to_verify"] == {"licenses": [], "otherLicenses": []}
    assert component["licenses_text_to_verify"] == {"licenses": [], "otherLicenses": []}
    compound = dependency(licenses=[attribution("MIT OR Apache-2.0", "terms")])
    assert converter().build_component(compound, "direct")["license"] == "TBD"


def test_conflicting_or_missing_attribution_text():
    for entries in (
        [attribution("MIT", "Terms A"), attribution("mit", "Terms B")],
        [attribution("MIT", "")],
    ):
        component = converter().build_component(dependency(licenses=entries), "direct")
        assert component["license"] == "MIT"
        assert component["license_text"] == "TBD"
        assert component["licenses_text_to_verify"]["licenses"] == [
            {"name": entry["name"], "text": entry["attribution"]}
            for entry in entries if entry["attribution"]
        ]


def test_unfilled_copyright_in_fossa_license_text_requires_review():
    generic = "MIT License\nCopyright (c) <year> <copyright holders>\nPermission is granted.\n"
    component = converter().build_component(
        dependency("ansi-styles", "3.2.1", [attribution("MIT", generic)]), "deep"
    )
    assert component["license"] == "MIT"
    assert component["copyright_holder"] == "TBD"
    assert component["license_text"] == "TBD"
    assert component["licenses_text_to_verify"]["licenses"] == [
        {"name": "MIT", "text": generic}
    ]
    assert component["fossa_evidence"]["licenses"][0]["attribution"] == generic


def test_apache_appendix_example_is_not_an_unfilled_grant():
    text = ("Apache License\nVersion 2.0\n"
            "APPENDIX: How to apply the Apache License to your work.\n"
            "Copyright [yyyy] [name of copyright owner]\n"
            "Licensed under the Apache License, Version 2.0\n")
    component = converter().build_component(
        dependency(licenses=[attribution("Apache-2.0", text)]), "direct"
    )
    assert component["license_text"] == text


def test_license_liability_clause_is_not_a_copyright_holder():
    text = ("The MIT License (MIT)\nPermission is granted.\n"
            "COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER\n")
    component = converter().build_component(
        dependency("react-event-listener", "0.6.6", [attribution("MIT", text)]), "deep"
    )
    assert component["copyright_holder"] == "TBD"
    assert component["license_text"] == text


def test_ignored_notice_and_global_copyright_not_assigned():
    item = dependency(licenses=[attribution("MIT", "MIT terms without copyright")], notices=[
        {"path": "NOTICE", "contents": "Copyright 2020 Ignored", "copyrights": ["2020 Ignored"],
         "ignored": True, "corrected": True},
        {"path": "EMPTY", "contents": "", "copyrights": ["2021 Empty"],
         "ignored": False, "corrected": False},
    ])
    component = draft(report(item))["components"][0]
    assert component["copyright_holder"] == "TBD"
    assert component["fossa_evidence"]["noticeFiles"] == item["noticeFiles"]
    item["noticeFiles"].append({"path": "NOTICE2", "contents": "No copyright line",
                                "copyrights": ["2022 Structured"], "ignored": False,
                                "corrected": False})
    assert converter().build_component(item, "direct")["copyright_holder"] == "Copyright 2022 Structured"


def test_notice_and_selected_attribution_conflict_is_unresolved():
    notice = {"path": "NOTICE", "contents": "Copyright 2020 Helper Contributors\n",
              "copyrights": ["2020 Helper Contributors"], "ignored": False,
              "corrected": False}
    item = dependency(licenses=[attribution(
        "MIT", "Copyright 2024 Main Authors\nMIT license terms\n",
    )], notices=[notice])
    assert converter().build_component(item, "direct")["copyright_holder"] == "TBD"


def test_structured_notice_copyright_adds_unmatched_holder():
    notice = {"path": "NOTICE", "contents": "Copyright 2020 Alice\n",
              "copyrights": ["2020 Alice", "2021 Bob"], "ignored": False,
              "corrected": False}
    item = dependency(licenses=[attribution("MIT", "MIT license terms")],
                      notices=[notice])
    assert converter().build_component(item, "direct")["copyright_holder"] == "TBD"


def test_yearless_notice_holder_is_selected():
    notice = {"path": "NOTICE", "contents": "Copyright Example Corp\n",
              "copyrights": [], "ignored": False, "corrected": False}
    item = dependency(licenses=[attribution("MIT", "MIT license terms")],
                      notices=[notice])
    assert converter().build_component(item, "direct")["copyright_holder"] == "Copyright Example Corp"


def test_yearless_notice_conflicts_with_selected_attribution():
    notice = {"path": "NOTICE", "contents": "Copyright Example Corp\n",
              "copyrights": [], "ignored": False, "corrected": False}
    item = dependency(licenses=[attribution("MIT", "Copyright 2024 Other Corp\nMIT terms")],
                      notices=[notice])
    assert converter().build_component(item, "direct")["copyright_holder"] == "TBD"


def test_named_holder_prefix_without_year_is_selected():
    notice = {"path": "NOTICE", "contents": "Copyright holder: Example Corp\n",
              "copyrights": ["Example Corp"], "ignored": False, "corrected": False}
    item = dependency(licenses=[attribution("MIT", "MIT terms")], notices=[notice])
    assert converter().build_component(item, "direct")["copyright_holder"] == (
        "Copyright holder: Example Corp"
    )


def test_named_holder_prefix_conflicts_with_attribution():
    notice = {"path": "NOTICE", "contents": "Copyright owner: Example Corp\n",
              "copyrights": [], "ignored": False, "corrected": False}
    item = dependency(licenses=[attribution("MIT", "Copyright 2024 Other Corp\nMIT terms")],
                      notices=[notice])
    assert converter().build_component(item, "direct")["copyright_holder"] == "TBD"


def test_duplicate_across_sections_is_invalid():
    item = dependency(licenses=[attribution("MIT", "MIT terms")])
    with pytest.raises(ValueError, match="duplicate"):
        draft(report(item, deep=[item]))
    malformed = [
        {}, {**item, "title": " "}, {**item, "version": None},
        {**item, "licenses": None}, {**item, "licenses": [{}]},
        {**item, "noticeFiles": [{"path": "NOTICE", "contents": 3}]},
    ]
    for row in malformed:
        with pytest.raises(ValueError):
            draft(report(row))
    for bad_report in ([], {}, {**report(), "directDependencies": None},
                       {**report(), "project": {"revision": "bad"}}):
        with pytest.raises(ValueError):
            draft(bad_report)


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def release_repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Credits Tests")
    git(tmp_path, "config", "user.email", "credits@example.test")
    # Fixture-only commits are never published and do not need a signing agent.
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "tag.gpgsign", "false")
    app = tmp_path / "package/default/app.conf"
    app.parent.mkdir(parents=True)
    app.write_text("[id]\nversion = 8.2.2\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "Release 8.2.2")
    sha = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "tag", "-a", "v8.2.2", "-m", "Release")
    return tmp_path, app, sha


def test_settings_contract(tmp_path):
    path = tmp_path / ".fossa-credits.json"
    path.write_text(json.dumps(SETTINGS), encoding="utf-8")
    assert module().load_settings(path) == SETTINGS
    invalid = [[], {}, {**SETTINGS, "extra": "value"}]
    invalid += [{**SETTINGS, "schema_version": value} for value in (True, "1", 2)]
    invalid += [
        {**SETTINGS, key: value} for key in SETTINGS for value in (None, "", "  ")
    ]
    invalid += [
        {**SETTINGS, "ta_name": value}
        for value in ("../aws", "/aws", "a/b", "a\\b", ".", "..", "aws\n", "aws name")
    ]
    for settings in invalid:
        path.write_text(json.dumps(settings), encoding="utf-8")
        with pytest.raises(ValueError):
            module().load_settings(path)


def test_tag_and_override_must_agree(release_repo):
    repo, app, sha = release_repo
    assert module().resolve_revision(repo, "8.2.2", None) == sha
    assert module().resolve_revision(repo, "8.2.2", sha) == sha
    app.write_text("[id]\nversion = 8.2.3\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Next release")
    other = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="agree|match"):
        module().resolve_revision(repo, "8.2.2", other)
    git(repo, "tag", "-d", "v8.2.2")
    assert module().resolve_revision(repo, "8.2.2", sha) == sha
    with pytest.raises(ValueError):
        module().resolve_revision(repo, "8.2.2", None)
    for override in ("HEAD", "bad", "f" * 40):
        with pytest.raises(ValueError):
            module().resolve_revision(repo, "8.2.2", override)


def test_revision_checks_committed_app_version(release_repo):
    repo, app, sha = release_repo
    app.write_text("[id]\nversion = 99.0\n", encoding="utf-8")
    assert module().resolve_revision(repo, "8.2.2", None) == sha
    git(repo, "tag", "-d", "v8.2.2")
    for content in (
        "[id]\nversion = 8.2.3\n",
        "[other]\nversion = 8.2.2\n",
        "[id]\n",
        "broken",
    ):
        app.write_text(content, encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "Invalid release metadata")
        revision = git(repo, "rev-parse", "HEAD")
        with pytest.raises(ValueError):
            module().resolve_revision(repo, "8.2.2", revision)
    with pytest.raises(ValueError, match="version|app.conf"):
        module().resolve_revision(repo, "8.2.2", revision)


def test_fetch_options_and_redirect_redaction(monkeypatch):
    api = module()
    token = "secret-token-never-log"
    revision = "03aa8ea33a87169beb2c97b1399effe280684fd0"
    requests = []

    class Response(BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            return Response(b'{"project": {}}\n')

    assert (
        api.fetch_attribution(
            SETTINGS["fossa_project_locator"], revision, token, Opener()
        )
        == b'{"project": {}}\n'
    )
    request, timeout = requests[0]
    url = urlsplit(request.full_url)
    assert url.scheme == "https" and url.netloc == "app.fossa.com"
    assert (
        url.path
        == "/api/v2/revisions/"
        + quote(SETTINGS["fossa_project_locator"] + "$" + revision, safe="")
        + "/attribution/json"
    )
    assert parse_qs(url.query) == {
        "includeDeepDependencies": ["true"],
        "includeCopyrightList": ["true"],
        "includeNoticeFiles": ["true"],
        "includeFileMatches": ["true"],
        "includeHashAndVersionData": ["true"],
        "excludeUnknownDependencies": ["false"],
    }
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer " + token
    assert timeout == 120

    class ErrorOpener:
        def open(self, request, timeout):
            raise HTTPError(
                "https://evil.test/" + token,
                302,
                token,
                {"Location": "https://evil.test/" + token},
                None,
            )

    for code in (302, 401, 500):

        class StatusResponse(Response):
            status = code

        class StatusOpener:
            def open(self, request, timeout):
                return StatusResponse(token.encode())

        with pytest.raises(ValueError) as error:
            api.fetch_attribution("project", revision, token, StatusOpener())
        assert token not in str(error.value)
    with pytest.raises(ValueError) as error:
        api.fetch_attribution("project", revision, token, ErrorOpener())
    assert token not in str(error.value)

    class NetworkErrorOpener:
        def open(self, request, timeout):
            raise URLError(token)

    with pytest.raises(ValueError) as error:
        api.fetch_attribution("project", revision, token, NetworkErrorOpener())
    assert token not in str(error.value)

    # Exercise the real default opener's redirect handler without a network call.
    import urllib.request

    real_build = urllib.request.build_opener

    def inspect_opener(*handlers):
        opener = real_build(*handlers)
        redirects = [
            h
            for h in opener.handlers
            if isinstance(h, urllib.request.HTTPRedirectHandler)
        ]
        assert len(redirects) == 1
        for code in (301, 302, 303, 307, 308):
            with pytest.raises(HTTPError):
                redirects[0].redirect_request(
                    request, None, code, token, {}, "https://evil.test/" + token
                )
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", inspect_opener)
    api.fetch_attribution("project", revision, token)


def artifact_paths(output):
    prefix = "Splunk_TA_aws-8.2.2-"
    return tuple(output / (prefix + suffix) for suffix in (
        "fossa-attribution.json", "fossa-attribution.request.json", "credits-draft.json",
    ))


def cli_setup(release_repo, monkeypatch):
    repo, _, sha = release_repo
    (repo / ".fossa-credits.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
    monkeypatch.setenv("FOSSA_API_KEY", "secret-never-disclose")
    source = report(dependency(), deep=[dependency("deep")])
    source["project"]["revision"] = sha
    raw = (json.dumps(source, indent=3) + "\n").encode()
    return repo, sha, raw


def test_draft_cli_writes_three_named_artifacts(release_repo, monkeypatch, capsys):
    import hashlib
    from datetime import datetime

    repo, sha, raw = cli_setup(release_repo, monkeypatch)
    calls = []

    def fetch(locator, revision, token):
        calls.append((locator, revision, token))
        return raw

    monkeypatch.setattr(module(), "fetch_attribution", fetch)
    output = repo / "artifacts"
    assert module().main(["--ta-repo", str(repo), "--ta-version", "8.2.2",
                          "--output-dir", str(output)]) == 0
    raw_path, metadata_path, draft_path = artifact_paths(output)
    assert set(output.iterdir()) == {raw_path, metadata_path, draft_path}
    assert raw_path.read_bytes() == raw
    metadata = json.loads(metadata_path.read_bytes())
    result = json.loads(draft_path.read_bytes())
    assert metadata["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["selected_revision"] == metadata["response_revision"] == sha
    assert metadata["ta_name"] == SETTINGS["ta_name"]
    assert metadata["display_name"] == result["name"] == SETTINGS["display_name"]
    assert metadata["ta_version"] == result["version"] == "8.2.2"
    assert metadata["direct_dependencies"] == metadata["deep_dependencies"] == 1
    assert len(result["components"]) == 2
    assert result["components"][0]["license"] == "TBD"
    assert result["provenance"] == {
        "revision": sha, "raw_sha256": metadata["raw_sha256"],
        "fossa_project_locator": SETTINGS["fossa_project_locator"],
        "raw_report_file": raw_path.name, "request_metadata_file": metadata_path.name,
    }
    assert metadata["request_options"] == module().REQUEST_OPTIONS
    assert quote(SETTINGS["fossa_project_locator"] + "$" + sha, safe="") in metadata["api_url"]
    assert datetime.fromisoformat(metadata["fetched_at"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
    assert len(calls) == 1
    assert "secret-never-disclose" not in metadata_path.read_text() + draft_path.read_text()
    assert "secret-never-disclose" not in str(capsys.readouterr())


def test_wrong_revision_and_bad_json_preserve_previous_artifacts(tmp_path):
    paths = artifact_paths(tmp_path)
    for path in paths:
        path.write_bytes(b"previous successful artifact")
    invalid = [b"not JSON", json.dumps({**report(), "project": {"name": "aws", "revision": "f" * 40}}).encode(),
               json.dumps(report(dependency(), deep=[dependency()])).encode()]
    for raw in invalid:
        with pytest.raises(ValueError):
            module().write_draft_artifacts(raw, SETTINGS, "8.2.2", REVISION,
                                           tmp_path, "https://app.fossa.com/test", "2026-10-27T00:00:00Z")
        assert set(tmp_path.iterdir()) == set(paths)
        assert all(path.read_bytes() == b"previous successful artifact" for path in paths)
    empty = tmp_path / "new"
    with pytest.raises(ValueError, match="revision"):
        module().write_draft_artifacts(invalid[1], SETTINGS, "8.2.2", REVISION,
                                       empty, "https://app.fossa.com/test", "2026-10-27T00:00:00Z")
    assert not empty.exists()


def test_missing_token_never_writes_artifacts(release_repo, monkeypatch, capsys):
    repo, _, _ = cli_setup(release_repo, monkeypatch)
    monkeypatch.delenv("FOSSA_API_KEY")
    output = repo / "artifacts"
    assert module().main(["--ta-repo", str(repo), "--ta-version", "8.2.2",
                          "--output-dir", str(output)]) == 2
    assert not output.exists()
    assert "Traceback" not in capsys.readouterr().err


def test_cli_api_errors_redacted_and_previous_artifacts_preserved(release_repo, monkeypatch, capsys):
    repo, _, _ = cli_setup(release_repo, monkeypatch)
    output = repo / "artifacts"
    output.mkdir()
    for path in artifact_paths(output):
        path.write_bytes(b"old")

    def failed_fetch(*args):
        raise ValueError("secret-never-disclose")

    monkeypatch.setattr(module(), "fetch_attribution", failed_fetch)
    assert module().main(["--ta-repo", str(repo), "--ta-version", "8.2.2",
                          "--output-dir", str(output)]) == 2
    assert all(path.read_bytes() == b"old" for path in artifact_paths(output))
    error = capsys.readouterr().err
    assert error and "secret-never-disclose" not in error and "Traceback" not in error


def test_token_file_and_default_paths(release_repo, monkeypatch):
    repo, _, raw = cli_setup(release_repo, monkeypatch)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("FOSSA_API_KEY")
    token_file = repo / "token"
    token_file.write_text("file-secret\n")

    def fetch(locator, revision, token):
        assert token == "file-secret"
        return raw

    monkeypatch.setattr(module(), "fetch_attribution", fetch)
    assert module().main(["--ta-version", "8.2.2", "--token-file", str(token_file)]) == 0
    assert all(path.exists() for path in artifact_paths(repo))


def test_write_failure_keeps_previous_artifacts(tmp_path, monkeypatch):
    import os

    paths = artifact_paths(tmp_path)
    for path in paths:
        path.write_bytes(b"old")
    real_replace = os.replace
    calls = []

    def fail_second(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("write failed")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError):
        module().write_draft_artifacts(json.dumps(report(dependency())).encode(), SETTINGS,
                                       "8.2.2", REVISION, tmp_path,
                                       module().attribution_url(SETTINGS["fossa_project_locator"], REVISION),
                                       "2026-10-27T00:00:00Z")
    assert set(tmp_path.iterdir()) == set(paths)
    assert all(path.read_bytes() == b"old" for path in paths)


def test_cli_rejects_overwriting_token_input(release_repo, monkeypatch):
    repo, _, raw = cli_setup(release_repo, monkeypatch)
    token = artifact_paths(repo)[0]
    token.write_bytes(b"input-secret")
    monkeypatch.setattr(module(), "fetch_attribution", lambda *args: raw)
    assert module().main(["--ta-repo", str(repo), "--ta-version", "8.2.2",
                          "--token-file", str(token), "--output-dir", str(repo)]) == 2
    assert token.read_bytes() == b"input-secret"


@pytest.mark.parametrize("args", [[], ["--ta-version", "8.2.2", "--unexpected", "secret-never-disclose"]])
def test_cli_invalid_arguments_return_two_without_disclosure(args, capsys):
    assert module().main(args) == 2
    error = capsys.readouterr().err
    assert error and "secret-never-disclose" not in error and "Traceback" not in error


def test_artifact_symlink_is_rejected_without_modifying_target(tmp_path):
    target = tmp_path / "input"
    target.write_bytes(b"protected")
    paths = artifact_paths(tmp_path)
    paths[1].symlink_to(target)
    with pytest.raises(ValueError):
        module().write_draft_artifacts(json.dumps(report(dependency())).encode(), SETTINGS,
                                       "8.2.2", REVISION, tmp_path,
                                       module().attribution_url(SETTINGS["fossa_project_locator"], REVISION),
                                       "2026-10-27T00:00:00Z")
    assert target.read_bytes() == b"protected"
    assert paths[1].is_symlink()
    assert not paths[0].exists() and not paths[2].exists()
    assert set(tmp_path.iterdir()) == {target, paths[1]}


@pytest.mark.parametrize("mutation", ["utf16", "duplicate_root", "duplicate_native", "nan", "infinity", "overflow"])
def test_strict_raw_json_rejection_preserves_valid_artifacts(tmp_path, mutation):
    from scripts.fossa_json_final import verify_provenance

    api = module()
    raw = json.dumps(report(dependency())).encode("utf-8")
    api_url = api.attribution_url(SETTINGS["fossa_project_locator"], REVISION)
    paths = api.write_draft_artifacts(raw, SETTINGS, "8.2.2", REVISION, tmp_path,
                                     api_url, "2026-10-27T00:00:00Z")
    prior = {path: path.read_bytes() for path in paths}
    verify_provenance(json.loads(prior[paths[2]]), paths[2])
    if mutation == "utf16":
        invalid = raw.decode("utf-8").encode("utf-16")
    elif mutation == "duplicate_root":
        invalid = b'{"deepDependencies": [],' + raw[1:]
    elif mutation == "duplicate_native":
        invalid = raw.replace(b'"title":', b'"title": "shadowed", "title":', 1)
    else:
        invalid = b'{"extra": ' + {"nan": b'NaN', "infinity": b'Infinity',
                                    "overflow": b'1e999'}[mutation] + b',' + raw[1:]
    with pytest.raises(ValueError):
        api.write_draft_artifacts(invalid, SETTINGS, "8.2.2", REVISION, tmp_path,
                                  api_url, "2026-10-27T00:00:00Z")
    assert set(tmp_path.iterdir()) == set(paths)
    assert {path: path.read_bytes() for path in paths} == prior
    verify_provenance(json.loads(paths[2].read_bytes()), paths[2])


@pytest.mark.parametrize("api_url,fetched_at", [
    (None, "garbage"), (None, "2026-10-27T00:00:00"),
    ("https://app.fossa.com/test", "2026-10-27T00:00:00Z"),
])
def test_invalid_generated_metadata_preserves_valid_artifacts(tmp_path, api_url, fetched_at):
    from scripts.fossa_json_final import verify_provenance

    api = module()
    raw = json.dumps(report(dependency())).encode("utf-8")
    valid_url = api.attribution_url(SETTINGS["fossa_project_locator"], REVISION)
    paths = api.write_draft_artifacts(raw, SETTINGS, "8.2.2", REVISION, tmp_path,
                                     valid_url, "2026-10-27T00:00:00Z")
    prior = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError):
        api.write_draft_artifacts(raw, SETTINGS, "8.2.2", REVISION, tmp_path,
                                  api_url or valid_url, fetched_at)
    assert set(tmp_path.iterdir()) == set(paths)
    assert {path: path.read_bytes() for path in paths} == prior
    verify_provenance(json.loads(paths[2].read_bytes()), paths[2])
