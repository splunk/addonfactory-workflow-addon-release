"""Controller tests use fake Argo/AWS commands; no cluster resources are created."""

import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).parents[1] / ".github" / "actions" / "run-rfct" / "run_rfct.py"
SPEC = importlib.util.spec_from_file_location("run_rfct", MODULE_PATH)
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)

IMAGE = "956110764581.dkr.ecr.us-west-2.amazonaws.com/ta-automation/rfct@sha256:" + "a" * 64
WORKFLOW = {"name": "rfct-123-1-12345678", "uid": "abcd-1234"}


def request_data():
    return {
        "schema_version": 1,
        "app_code_name": "Splunk_TA_test",
        "job_name": "rfct-123-1",
        "baseline": {"version": "1.0.0", "package_url": "s3://test-bucket/run/baseline.spl", "package_sha256": "b" * 64},
        "candidate": {"version": "1.1.0", "package_url": "s3://test-bucket/run/candidate.spl", "package_sha256": "c" * 64,
                      "source_url": "s3://test-bucket/run/source.tgz", "source_sha256": "d" * 64, "source_sha": "e" * 40},
        "rfct_image": IMAGE,
        "splunk_version": "9.4.1",
        "run": {"repository": "splunk/test-addonfactory-repo", "id": "123", "attempt": 1, "event_name": "pull_request"},
        "output_prefix": "s3://test-bucket/rfct/123/1",
    }


@pytest.fixture
def config(tmp_path, monkeypatch):
    request = request_data()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    output = tmp_path / "output"
    values = {"REQUEST_PATH": str(request_path), "REQUEST_URL": "s3://test-bucket/run/request.json",
              "MANIFESTS_REF": "f" * 40, "OUTPUT_DIRECTORY": str(output)}
    for name, value in values.items():
        monkeypatch.setenv("INPUT_" + name, value)
    monkeypatch.setenv("ARGO_TOKEN", "argo-secret-value")
    monkeypatch.setenv("ARGO_SERVER", "argo.staging.wfe.splgdi.com:443")
    return controller.validate_inputs()


def result_data(status="changes"):
    return {"schema_version": 1, "workflow_uid": WORKFLOW["uid"], "status": status,
            "candidate_version": "1.1.0", "rfct_image": IMAGE}


def make_archive(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, content in entries.items():
            raw = content.encode()
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))


def test_valid_inputs_and_optional_corpus(config):
    assert config["namespace"] == "workflows"
    assert config["timeout"] == 3600
    request = config["request"]
    request["corpus"] = {"url": "s3://test-bucket/run/corpus.jsonl", "sha256": "1" * 64}
    assert controller.validate_request(request) is request


@pytest.mark.parametrize("url", ["https://example.com/file", "s3://bucket", "s3://bucket/path?token=secret", "s3://bucket/../file", "s3://bucket/path\n", None])
def test_reject_invalid_s3_references(url):
    with pytest.raises(controller.ControllerError):
        controller.s3_url(url)


@pytest.mark.parametrize("key,value", [("schema_version", 2), ("app_code_name", "../../app"), ("rfct_image", IMAGE.replace("@sha256:", ":")),
                                       ("splunk_version", "latest"), ("baseline", {}), ("run", {}), ("output_prefix", "s3://bucket")])
def test_request_contract_rejects_invalid_fields(key, value):
    request = request_data()
    request[key] = value
    with pytest.raises(controller.ControllerError):
        controller.validate_request(request)


def test_nested_credentials_and_source_validation():
    request = request_data()
    request["extensions"] = [{"password": "secret"}]
    with pytest.raises(controller.ControllerError, match="credentials"):
        controller.validate_request(request)
    request = request_data()
    request["candidate"]["source_sha"] = "main"
    with pytest.raises(controller.ControllerError, match="commit SHA"):
        controller.validate_request(request)
    request["candidate"]["source_sha256"] = "bad"
    with pytest.raises(controller.ControllerError, match="checksum"):
        controller.validate_request(request)


def test_invalid_corpus_and_run_identity():
    with pytest.raises(controller.ControllerError):
        controller.validate_corpus({"url": "s3://bucket/path", "sha256": "bad"})
    with pytest.raises(controller.ControllerError):
        controller.validate_corpus("bad")
    for run in (None, {"repository": "invalid"}, {"repository": "owner/repo", "id": "x"}, {"repository": "owner/repo", "id": 1, "attempt": 0}):
        with pytest.raises(controller.ControllerError):
            controller.validate_run(run)


@pytest.mark.parametrize("fields", [{"sc4s_version": "latest"}, {"sc4s_docker_registry": "image;exec"}])
def test_invalid_optional_sc4s_inputs(fields):
    with pytest.raises(controller.ControllerError):
        controller.validate_sc4s(fields)


def test_optional_sc4s_inputs_are_explicit():
    controller.validate_sc4s({"sc4s_version": "3.28.1", "sc4s_docker_registry": "ghcr.io/splunk/sc4s"})
    controller.validate_sc4s({"sc4s_version": "No"})


@pytest.mark.parametrize("env,value", [("MANIFESTS_REF", "main"), ("NAMESPACE", "../default"), ("TIMEOUT_SECONDS", "1"), ("TIMEOUT_SECONDS", "bad")])
def test_input_settings_are_validated(config, monkeypatch, env, value):
    monkeypatch.setenv("INPUT_" + env, value)
    with pytest.raises(controller.ControllerError):
        controller.validate_inputs()


def test_required_auth_and_request_path(config, monkeypatch):
    monkeypatch.delenv("ARGO_TOKEN")
    with pytest.raises(controller.ControllerError, match="ARGO_TOKEN"):
        controller.validate_inputs()
    monkeypatch.setenv("INPUT_REQUEST_PATH", "relative.json")
    with pytest.raises(controller.ControllerError, match="request-path"):
        controller.validate_inputs()


def test_dirty_output_is_not_overwritten(config):
    (config["output"] / "existing.txt").write_text("keep")
    with pytest.raises(controller.ControllerError, match="empty"):
        controller.validate_inputs()


def test_command_redacts_secrets_and_keeps_return_code(monkeypatch):
    monkeypatch.setenv("ARGO_TOKEN", "secret-token-value")
    monkeypatch.setattr(controller.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=7, stdout="", stderr="bad secret-token-value password=hello"))
    with pytest.raises(controller.ControllerError) as error:
        controller.command(["argo", "get"])
    assert error.value.exit_code == 7
    assert "secret-token-value" not in str(error.value)
    assert "hello" not in str(error.value)
    monkeypatch.setattr(controller.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok"))
    assert controller.command(["argo"]) == "ok"


def test_command_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("argo", 10)
    monkeypatch.setattr(controller.subprocess, "run", timeout)
    with pytest.raises(controller.ControllerError, match="deadline"):
        controller.command(["argo"])


def test_submit_uses_frozen_explicit_parameters(config, monkeypatch):
    calls = []
    monkeypatch.setattr(controller.uuid, "uuid4", lambda: SimpleNamespace(hex="12345678" + "a" * 24))
    config["request"]["sc4s_version"] = "3.28.1"
    config["request"]["sc4s_docker_registry"] = "ghcr.io/splunk/sc4s"
    def invoke(argv, timeout=120):
        calls.append(argv)
        return json.dumps({"metadata": {"name": WORKFLOW["name"], "uid": WORKFLOW["uid"]}})
    monkeypatch.setattr(controller, "command", invoke)
    assert controller.submit(config) == WORKFLOW
    command = calls[0]
    assert "k8s-manifests-ref=" + "f" * 40 in command
    assert "output-bucket=test-bucket" in command
    assert "output-key=rfct/123/1" in command
    assert "candidate-source-url=s3://test-bucket/run/source.tgz" in command
    assert "job-name=rfct-123-1" in command
    assert "sc4s-version=3.28.1" in command
    assert "argo-secret-value" not in " ".join(command)


def test_submit_rejects_unexpected_identity(config, monkeypatch):
    monkeypatch.setattr(controller, "argo", lambda *args: json.dumps({"metadata": {"name": "unexpected", "uid": "a"}}))
    with pytest.raises(controller.ControllerError, match="identity"):
        controller.submit(config)


def test_wait_polls_until_terminal(config, monkeypatch):
    states = iter(["Running", "Succeeded"])
    monkeypatch.setattr(controller, "argo", lambda *args: json.dumps({"metadata": {"uid": WORKFLOW["uid"]}, "status": {"phase": next(states)}}))
    monkeypatch.setattr(controller.time, "sleep", lambda seconds: None)
    assert controller.wait(config, WORKFLOW, [False]) == "Succeeded"


def test_wait_rejects_replaced_workflow(config, monkeypatch):
    monkeypatch.setattr(controller, "argo", lambda *args: json.dumps({"metadata": {"uid": "other"}}))
    with pytest.raises(controller.ControllerError, match="UID changed"):
        controller.wait(config, WORKFLOW, [False])


def test_wait_timeout_and_cancellation(config, monkeypatch):
    with pytest.raises(controller.ControllerError) as error:
        controller.wait(config, WORKFLOW, [True])
    assert error.value.exit_code == 130
    moments = iter([0, 4000])
    monkeypatch.setattr(controller.time, "monotonic", lambda: next(moments))
    with pytest.raises(controller.ControllerError) as error:
        controller.wait(config, WORKFLOW, [False])
    assert error.value.exit_code == 124


@pytest.mark.parametrize("name", ["/absolute", "../escape", "docs/../../escape"])
def test_archive_traversal_is_rejected(tmp_path, name):
    archive = tmp_path / "archive.tgz"
    make_archive(archive, {name: "bad"})
    with pytest.raises(controller.ControllerError):
        controller.extract_artifacts(archive, tmp_path / "out")


def test_archive_links_limits_and_unexpected_roots(tmp_path, monkeypatch):
    member = tarfile.TarInfo("docs/link")
    member.type = tarfile.SYMTYPE
    with pytest.raises(controller.ControllerError, match="links"):
        controller.checked_members(SimpleNamespace(getmembers=lambda: [member]))
    monkeypatch.setattr(controller, "MAX_ARCHIVE_MEMBERS", 0)
    with pytest.raises(controller.ControllerError, match="Too many"):
        controller.checked_members(SimpleNamespace(getmembers=lambda: [member]))
    monkeypatch.setattr(controller, "MAX_ARCHIVE_MEMBERS", 100)
    member.size = controller.MAX_ARCHIVE_BYTES + 1
    with pytest.raises(controller.ControllerError, match="too large"):
        controller.checked_members(SimpleNamespace(getmembers=lambda: [member]))
    archive = tmp_path / "archive.tgz"
    make_archive(archive, {"config/secret.json": "bad"})
    with pytest.raises(controller.ControllerError, match="root"):
        controller.extract_artifacts(archive, tmp_path / "out")


def test_archive_accepts_single_argo_wrapper_directory(tmp_path):
    archive = tmp_path / "archive.tgz"
    make_archive(archive, {"output/execution.json": "{}", "output/docs/index.md": "page"})
    output = tmp_path / "out"
    controller.extract_artifacts(archive, output)
    assert (output / "execution.json").read_text() == "{}"
    assert (output / "docs/index.md").read_text() == "page"


def test_archive_explicit_directories(tmp_path):
    path = tmp_path / "directories.tgz"
    with tarfile.open(path, "w:gz") as archive:
        for name in ("output", "output/docs"):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
    controller.extract_artifacts(path, tmp_path / "out")
    assert (tmp_path / "out/docs").is_dir()


@pytest.mark.parametrize("key,value", [("schema_version", 9), ("workflow_uid", "other"), ("status", "unknown"), ("candidate_version", "2.0.0"), ("rfct_image", "other")])
def test_result_provenance_is_checked(config, key, value):
    result = result_data()
    result[key] = value
    with pytest.raises(controller.ControllerError):
        controller.validate_result(result, config, WORKFLOW)


def test_collect_validates_archive_and_result(config, monkeypatch):
    def download(argv, timeout=120):
        target = Path(argv[4])
        if argv[3].endswith("/result.json"):
            target.write_text(json.dumps(result_data()))
        else:
            make_archive(target, {"execution.json": "{}", "comparison/report.json": "{}", "docs/CIMChanges/1.1.0/index.md": "page"})
        return ""
    monkeypatch.setattr(controller, "command", download)
    assert controller.collect(config, WORKFLOW)["status"] == "changes"
    assert not (config["output"] / ".artifacts.tgz").exists()


def test_success_requires_docs_and_report(config, monkeypatch):
    def download(argv, timeout=120):
        target = Path(argv[4])
        if argv[3].endswith("/result.json"):
            target.write_text(json.dumps(result_data("no-changes")))
        else:
            make_archive(target, {"execution.json": "{}"})
        return ""
    monkeypatch.setattr(controller, "command", download)
    with pytest.raises(controller.ControllerError, match="comparison/report"):
        controller.collect(config, WORKFLOW)


def state_data():
    return {"schema_version": 1, "workflow": None, "phase": None, "status": "failed", "cancelled": [False]}


def test_run_controller_records_success(config, monkeypatch):
    monkeypatch.setattr(controller, "command", lambda *args: "")
    monkeypatch.setattr(controller, "submit", lambda value: WORKFLOW)
    monkeypatch.setattr(controller, "wait", lambda *args: "Succeeded")
    monkeypatch.setattr(controller, "collect", lambda *args: result_data("no-changes"))
    state = state_data()
    controller.run_controller(config, state)
    assert state["status"] == "no-changes"
    assert len(state["request_sha256"]) == 64


def test_submission_checkpoint_survives_loss_during_wait(config, monkeypatch, tmp_path):
    output_file = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setattr(controller, "command", lambda *args: "")
    monkeypatch.setattr(controller, "submit", lambda value: WORKFLOW)
    def interrupted_wait(*args):
        checkpoint = json.loads((config["output"] / "controller.json").read_text())
        assert checkpoint["workflow"] == WORKFLOW
        assert checkpoint["status"] == "running"
        values = output_file.read_text()
        assert "workflow-name=" + WORKFLOW["name"] in values
        assert "workflow-uid=" + WORKFLOW["uid"] in values
        assert "status=running" in values
        raise SystemExit(137)
    monkeypatch.setattr(controller, "wait", interrupted_wait)
    with pytest.raises(SystemExit):
        controller.run_controller(config, state_data())
    assert (config["output"] / "controller.json").is_file()


def test_failed_workflow_preserves_failure_when_artifacts_missing(config, monkeypatch):
    monkeypatch.setattr(controller, "command", lambda *args: "")
    monkeypatch.setattr(controller, "submit", lambda value: WORKFLOW)
    monkeypatch.setattr(controller, "wait", lambda *args: "Failed")
    def missing(*args):
        raise controller.ControllerError("artifact-missing", "missing")
    monkeypatch.setattr(controller, "collect", missing)
    state = state_data()
    with pytest.raises(controller.ControllerError) as error:
        controller.run_controller(config, state)
    assert error.value.reason == "workflow-failed"
    assert state["collection_error"] == "missing"


def test_failed_report_fails_successful_argo_workflow(config, monkeypatch):
    monkeypatch.setattr(controller, "command", lambda *args: "")
    monkeypatch.setattr(controller, "submit", lambda value: WORKFLOW)
    monkeypatch.setattr(controller, "wait", lambda *args: "Succeeded")
    monkeypatch.setattr(controller, "collect", lambda *args: result_data("failed"))
    with pytest.raises(controller.ControllerError, match="successfully"):
        controller.run_controller(config, state_data())


def test_successful_workflow_still_fails_if_artifacts_invalid(config, monkeypatch):
    monkeypatch.setattr(controller, "command", lambda *args: "")
    monkeypatch.setattr(controller, "submit", lambda value: WORKFLOW)
    monkeypatch.setattr(controller, "wait", lambda *args: "Succeeded")
    def invalid(*args):
        raise ValueError("invalid result JSON")
    monkeypatch.setattr(controller, "collect", invalid)
    with pytest.raises(ValueError, match="invalid result"):
        controller.run_controller(config, state_data())


def test_finish_stops_and_preserves_original_failure(config, monkeypatch, tmp_path):
    output_file = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    calls = []
    monkeypatch.setattr(controller, "argo", lambda *args: calls.append(args))
    state = state_data()
    state.update({"workflow": WORKFLOW, "reason": "workflow-timeout", "phase": "Running"})
    controller.finish(config, state)
    assert calls[0][1:3] == ("stop", WORKFLOW["name"])
    assert "workflow-uid=" + WORKFLOW["uid"] in output_file.read_text()
    assert json.loads((config["output"] / "controller.json").read_text())["reason"] == "workflow-timeout"


def test_cleanup_failure_is_secondary(config, monkeypatch):
    def failed_stop(*args):
        raise controller.ControllerError("stop-failed", "stop unavailable")
    monkeypatch.setattr(controller, "stop", failed_stop)
    state = state_data()
    state.update({"workflow": WORKFLOW, "reason": "workflow-timeout"})
    controller.finish(config, state)
    assert state["reason"] == "workflow-timeout"
    assert state["cleanup_error"] == "stop unavailable"


def test_lost_submission_response_still_requests_cleanup(config, monkeypatch):
    config["submitted_name"] = WORKFLOW["name"]
    calls = []
    monkeypatch.setattr(controller, "stop", lambda _config, workflow: calls.append(workflow))
    state = state_data()
    controller.finish(config, state)
    assert calls == [{"name": WORKFLOW["name"], "uid": ""}]


def test_main_success_and_safe_failure(config, monkeypatch, capsys):
    monkeypatch.setattr(controller, "validate_inputs", lambda: config)
    def success(config, state):
        state.update({"status": "changes", "workflow": WORKFLOW, "phase": "Succeeded"})
    monkeypatch.setattr(controller, "run_controller", success)
    assert controller.main() == 0
    def failure(config, state):
        raise controller.ControllerError("failed", "token=do-not-print", 4)
    monkeypatch.setattr(controller, "run_controller", failure)
    assert controller.main() == 4
    assert "do-not-print" not in capsys.readouterr().err


def test_main_handles_invalid_inputs_without_cluster_calls(monkeypatch):
    def invalid():
        raise ValueError("invalid JSON")
    monkeypatch.setattr(controller, "validate_inputs", invalid)
    assert controller.main() == 1


def test_finalization_failure_does_not_replace_original_exit_code(config, monkeypatch):
    monkeypatch.setattr(controller, "validate_inputs", lambda: config)
    def failed_run(*args):
        raise controller.ControllerError("failed", "original failure", 9)
    def failed_finish(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(controller, "run_controller", failed_run)
    monkeypatch.setattr(controller, "finish", failed_finish)
    assert controller.main() == 9
