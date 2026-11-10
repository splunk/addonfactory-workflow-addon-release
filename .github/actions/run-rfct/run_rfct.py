"""Submit a frozen RFCT request to WFE and collect workflow-scoped evidence."""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess  # nosec B404 - fixed argv, never shell commands
import sys
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

IMAGE_RE = re.compile(
    r"\A956110764581\.dkr\.ecr\.us-west-2\.amazonaws\.com/ta-automation/rfct"
    r"@sha256:[0-9a-f]{64}\Z"
)
SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
CHECKSUM_RE = re.compile(r"\A[0-9a-f]{64}\Z")
NAME_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
APP_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
VERSION_RE = re.compile(r"\A[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
SECRET_KEYS = {"password", "token", "secret", "authorization", "credentials", "aws_access_key_id", "aws_secret_access_key"}
SECRET_RE = re.compile(r"(?i)\b(password|token|secret|authorization)(\s*[:=]\s*)(\S+)")
TERMINAL_PHASES = {"Succeeded", "Failed", "Error"}
ALLOWED_ROOTS = {"result.json", "execution.json", "comparison", "docs", "logs"}
MAX_REQUEST_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000
POLL_SECONDS = 5
MAX_POLL_FAILURES = 6
ARGO_REQUEST_SECONDS = 30


class ControllerError(RuntimeError):
    """A failed controller phase with a safe, machine-readable reason."""

    def __init__(self, reason, message, exit_code=1):
        super().__init__(message)
        self.reason = reason
        self.exit_code = exit_code


def sanitize(message):
    """Keep runtime credentials out of persisted command diagnostics."""
    text = str(message)
    for name in ("ARGO_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "***")
    return SECRET_RE.sub(lambda match: match[1] + match[2] + "***", text)[:4000]


def progress(config, event, **fields):
    """Emit bounded diagnostics without exposing credentials or workflow commands."""
    record = {"component": "rfct-controller", "event": event}
    record.update({key: value if isinstance(value, (bool, int, float)) or value is None else sanitize(value)
                   for key, value in fields.items()})
    line = json.dumps(record, sort_keys=True, ensure_ascii=True).replace("::", "\\u003a\\u003a")
    print(line, file=sys.stderr, flush=True)
    if config and not config.get("progress_file_unavailable"):
        try:
            with (config["output"] / "controller-events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            # A diagnostic write must not replace the original execution error.
            config["progress_file_unavailable"] = True
            print('{"component":"rfct-controller","event":"progress-file-unavailable"}', file=sys.stderr)


def warning(message):
    text = sanitize(message).replace("::", ": :").replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print("::warning::" + text, file=sys.stderr, flush=True)


def _input(name, default=""):
    return os.environ.get("INPUT_" + name, default).strip()


def require(condition, message):
    if not condition:
        raise ControllerError("invalid-input", message)


def s3_url(value):
    """Validate an S3 object/prefix without accepting signed HTTP credentials."""
    require(isinstance(value, str) and not re.search(r"[\x00-\x20]", value), "Invalid S3 reference")
    parsed = urlsplit(value)
    require(parsed.scheme == "s3" and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", parsed.netloc), "Expected an S3 bucket reference")
    require(not parsed.query and not parsed.fragment and bool(parsed.path.strip("/")), "Expected a run-scoped S3 key")
    require(".." not in PurePosixPath(parsed.path).parts, "S3 references must not contain parent path components")
    return value.rstrip("/")


def reject_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            require(str(key).lower() not in SECRET_KEYS, "Request must contain references, not credentials")
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)


def validate_package(package):
    require(isinstance(package, dict), "Expected package metadata")
    require(bool(VERSION_RE.fullmatch(str(package.get("version", "")))), "Invalid package version")
    s3_url(package.get("package_url"))
    require(bool(CHECKSUM_RE.fullmatch(str(package.get("package_sha256", "")))), "Missing package checksum")


def validate_request(request):
    require(isinstance(request, dict) and request.get("schema_version") == 1, "Unsupported request schema")
    reject_secrets(request)
    job_name = str(request.get("job_name", ""))
    require(bool(NAME_RE.fullmatch(job_name)) and len(job_name) <= 40, "Invalid frozen RFCT job name")
    require(bool(APP_RE.fullmatch(str(request.get("app_code_name", "")))), "Invalid app code name")
    require(bool(IMAGE_RE.fullmatch(str(request.get("rfct_image", "")))), "RFCT image must use the approved ECR repository and digest")
    require(bool(VERSION_RE.fullmatch(str(request.get("splunk_version", "")))), "Invalid Splunk version")
    validate_package(request.get("baseline"))
    validate_package(request.get("candidate"))
    validate_source(request["candidate"])
    validate_run(request.get("run"))
    s3_url(request.get("output_prefix"))
    if request.get("corpus"):
        validate_corpus(request["corpus"])
    validate_sc4s(request)
    return request


def validate_sc4s(request):
    version = request.get("sc4s_version")
    if version:
        require(version == "No" or bool(VERSION_RE.fullmatch(str(version))), "Invalid SC4S version")
    registry = request.get("sc4s_docker_registry")
    if registry:
        require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*", str(registry))), "Invalid SC4S image registry")


def validate_source(candidate):
    s3_url(candidate.get("source_url"))
    require(bool(CHECKSUM_RE.fullmatch(str(candidate.get("source_sha256", "")))), "Missing candidate source checksum")
    require(bool(SHA_RE.fullmatch(str(candidate.get("source_sha", "")))), "Candidate source must be frozen to a commit SHA")


def validate_run(run):
    require(isinstance(run, dict), "Missing workflow run identity")
    require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(run.get("repository", "")))), "Invalid calling repository")
    require(str(run.get("id", "")).isdigit(), "Invalid workflow run ID")
    require(str(run.get("attempt", "")).isdigit() and int(run["attempt"]) > 0, "Invalid workflow attempt")


def validate_corpus(corpus):
    require(isinstance(corpus, dict), "Invalid corpus reference")
    s3_url(corpus.get("url"))
    require(bool(CHECKSUM_RE.fullmatch(str(corpus.get("sha256", "")))), "Missing corpus checksum")


def validate_inputs():
    output = Path(_input("OUTPUT_DIRECTORY"))
    require(output.is_absolute() and not output.is_symlink(), "output-directory must be an absolute non-symlink path")
    require(not re.search(r"[\r\n]", str(output)), "output-directory must not contain newlines")
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())), "output-directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    request_path = Path(_input("REQUEST_PATH"))
    require(request_path.is_absolute() and request_path.is_file() and not request_path.is_symlink(), "request-path must be an absolute regular file")
    require(request_path.stat().st_size <= MAX_REQUEST_BYTES, "Request is too large")
    request = validate_request(json.loads(request_path.read_text(encoding="utf-8")))
    manifests_ref = _input("MANIFESTS_REF")
    require(bool(SHA_RE.fullmatch(manifests_ref)), "manifests-ref must be an immutable commit SHA")
    require(request.get("manifests_ref", manifests_ref) == manifests_ref, "Request manifests revision mismatch")
    namespace, template = _input("NAMESPACE", "workflows"), _input("ARGO_TEMPLATE", "rfct-comparison")
    require(bool(NAME_RE.fullmatch(namespace)) and bool(NAME_RE.fullmatch(template)), "Invalid Argo namespace/template")
    timeout = _input("TIMEOUT_SECONDS", "3600")
    require(timeout.isdigit() and 60 <= int(timeout) <= 14400, "timeout-seconds must be between 60 and 14400")
    require(bool(os.environ.get("ARGO_TOKEN")) and bool(os.environ.get("ARGO_SERVER")), "ARGO_TOKEN and ARGO_SERVER are required")
    return {"request": request, "request_path": request_path, "request_url": s3_url(_input("REQUEST_URL")), "output": output,
            "manifests_ref": manifests_ref, "namespace": namespace, "template": template, "timeout": int(timeout)}


def command(argv, timeout=120):
    """Capture and redact subprocess output; do not print request/config contents."""
    prefixes = (("aws", "s3", "cp"), ("argo", "submit"), ("argo", "get"), ("argo", "stop"))
    prefix = next((value for value in prefixes if tuple(argv[:len(value)]) == value), None)
    require(prefix is not None, "Controller executable/subcommand is not allowlisted")
    operation = " ".join(prefix)
    options = {"capture_output": True, "text": True, "check": False, "timeout": timeout, "shell": False}
    try:
        if prefix == ("aws", "s3", "cp"):
            completed = subprocess.run(["aws", "s3", "cp", *argv[3:]], **options)
        elif prefix == ("argo", "submit"):
            completed = subprocess.run(["argo", "submit", *argv[2:]], **options)
        elif prefix == ("argo", "get"):
            completed = subprocess.run(["argo", "get", *argv[2:]], **options)
        else:
            completed = subprocess.run(["argo", "stop", *argv[2:]], **options)
    except subprocess.TimeoutExpired as error:
        raise ControllerError("command-timeout", operation + " exceeded its command deadline", 124) from error
    if completed.returncode:
        raise ControllerError("command-failed", operation + " failed: " + sanitize(completed.stderr or completed.stdout or "no diagnostics"), completed.returncode)
    return completed.stdout


def argo(config, *args):
    request_seconds, process_seconds = ARGO_REQUEST_SECONDS, ARGO_REQUEST_SECONDS + 5
    if args[0] == "get" and config.get("poll_deadline"):
        remaining = max(1, config["poll_deadline"] - time.monotonic())
        request_seconds = min(request_seconds, max(1, int(remaining)))
        process_seconds = min(process_seconds, remaining)
    return command(["argo", *args, "--namespace", config["namespace"], "--request-timeout", str(request_seconds) + "s"], timeout=process_seconds)


def submit(config):
    request = config["request"]
    workflow_name = request["job_name"] + "-" + uuid.uuid4().hex[:8]
    params = {
        "job-name": request["job_name"],
        "baseline-addon-url": request["baseline"]["package_url"],
        "candidate-addon-url": request["candidate"]["package_url"],
        "candidate-source-url": request["candidate"]["source_url"],
        "request-url": config["request_url"],
        "output-prefix": s3_url(request["output_prefix"]),
        "rfct-image": request["rfct_image"],
        "splunk-version": request["splunk_version"],
        "k8s-manifests-ref": config["manifests_ref"],
    }
    output_url = urlsplit(params["output-prefix"])
    params.update({"output-bucket": output_url.netloc, "output-key": output_url.path.strip("/")})
    for key in ("sc4s_version", "sc4s_docker_registry"):
        if request.get(key):
            params[key.replace("_", "-")] = str(request[key])
    arguments = ["submit", "--from", "workflowtemplate/" + config["template"], "--name", workflow_name, "--output", "json"]
    for key, value in params.items():
        arguments.extend(["--parameter", key + "=" + value])
    # Retain the intended identity before the network request. A server may
    # accept the workflow even when its response is lost or malformed.
    config["submitted_name"] = workflow_name
    progress(config, "workflow-submit-start", workflow_name=workflow_name,
             template=config["template"], namespace=config["namespace"])
    workflow = json.loads(argo(config, *arguments))
    metadata = workflow.get("metadata", {})
    require(metadata.get("name") == workflow_name and bool(NAME_RE.fullmatch(str(metadata.get("uid", "")))), "Argo submission returned an unexpected workflow identity")
    progress(config, "workflow-submitted", workflow_name=workflow_name, workflow_uid=metadata["uid"])
    return {"name": workflow_name, "uid": metadata["uid"]}


def stop(config, workflow):
    """Graceful stop preserves paired environment cleanup through onExit."""
    progress(config, "workflow-stop-start", workflow_name=workflow["name"])
    argo(config, "stop", workflow["name"], "--message", "RFCT controller cancelled or timed out")
    progress(config, "workflow-stop-requested", workflow_name=workflow["name"])


def transient_poll_error(error):
    """Only transport/service failures are retryable; auth and contract errors fail."""
    message = str(error).lower()
    if re.search(r"\b(401|403|404)\b|unauthori[sz]ed|unauthenticated|forbidden|permission denied|not found|x509|certificate", message):
        return False
    if error.reason == "command-timeout":
        return True
    return error.reason == "command-failed" and bool(re.search(
        r"\b(408|429|500|502|503|504)\b|unavailable|deadline.?exceeded|timed? ?out|timeout|"
        r"connection (reset|refused|closed)|unexpected eof|\beof\b|broken pipe|temporary failure|"
        r"bad gateway|gateway timeout|internal server error|code\s*=\s*internal", message
    ))


def poll_state(config, workflow):
    try:
        return json.loads(argo(config, "get", workflow["name"], "--output", "json"))
    except json.JSONDecodeError as error:
        raise ControllerError("invalid-response", "argo get returned invalid JSON") from error


def log_node_changes(config, state, previous):
    nodes = state.get("status", {}).get("nodes") or {}
    if not isinstance(nodes, dict):
        return
    for node_id, node in list(nodes.items())[:100]:
        if not isinstance(node, dict):
            continue
        signature = (node.get("phase"), node.get("message", ""))
        if previous.get(node_id) != signature:
            progress(config, "workflow-node", node_id=node_id, node_name=node.get("displayName", ""),
                     phase=signature[0], message=signature[1])
            previous[node_id] = signature


def wait(config, workflow, cancelled):
    config["poll_deadline"] = time.monotonic() + config["timeout"]
    try:
        return poll_until_terminal(config, workflow, cancelled)
    finally:
        config.pop("poll_deadline", None)


def poll_until_terminal(config, workflow, cancelled):
    failures = 0
    last_phase, last_heartbeat = None, 0
    node_states = {}
    while True:
        if cancelled[0]:
            progress(config, "workflow-cancelled", workflow_name=workflow["name"])
            raise ControllerError("cancelled", "RFCT comparison cancelled", 130)
        now = time.monotonic()
        if now >= config["poll_deadline"]:
            progress(config, "workflow-deadline-exceeded", workflow_name=workflow["name"],
                     timeout_seconds=config["timeout"])
            raise ControllerError("workflow-timeout", "RFCT workflow exceeded its deadline", 124)
        try:
            state = poll_state(config, workflow)
        except ControllerError as error:
            if not transient_poll_error(error):
                raise
            failures += 1
            progress(config, "workflow-poll-retry", workflow_name=workflow["name"],
                     consecutive_failures=failures, maximum_failures=MAX_POLL_FAILURES,
                     reason=error.reason, message=str(error))
            if failures >= MAX_POLL_FAILURES:
                raise ControllerError("argo-poll-unavailable", f"argo get failed after {failures} consecutive transient errors: {sanitize(error)}", error.exit_code) from error
            pause_poll(config)
            continue
        if failures:
            progress(config, "workflow-poll-recovered", workflow_name=workflow["name"],
                     recovered_failures=failures)
        failures = 0
        require(state.get("metadata", {}).get("uid") == workflow["uid"], "Argo workflow UID changed")
        log_node_changes(config, state, node_states)
        phase = state.get("status", {}).get("phase")
        elapsed = max(0, int(config["timeout"] - (config["poll_deadline"] - now)))
        if phase != last_phase or elapsed - last_heartbeat >= 60:
            progress(config, "workflow-phase" if phase != last_phase else "workflow-waiting",
                     workflow_name=workflow["name"], phase=phase or "Pending",
                     elapsed_seconds=elapsed, remaining_seconds=max(0, int(config["poll_deadline"] - now)))
            last_phase, last_heartbeat = phase, elapsed
        if phase in TERMINAL_PHASES:
            return phase
        pause_poll(config)


def pause_poll(config):
    remaining = config["poll_deadline"] - time.monotonic()
    if remaining <= 0:
        raise ControllerError("workflow-timeout", "RFCT workflow exceeded its deadline", 124)
    time.sleep(min(POLL_SECONDS, remaining))


def checked_members(archive):
    """Reject traversal, links, devices and oversized artifact archives."""
    members = archive.getmembers()
    require(len(members) <= MAX_ARCHIVE_MEMBERS, "Too many artifact archive members")
    require(sum(member.size for member in members) <= MAX_ARCHIVE_BYTES, "Artifact archive is too large")
    for member in members:
        path = PurePosixPath(member.name)
        require(not path.is_absolute() and ".." not in path.parts, "Unsafe artifact path")
        require(member.isfile() or member.isdir(), "Artifact links/devices are forbidden")
    return members


def extract_artifacts(archive_path, output):
    require(archive_path.stat().st_size <= MAX_ARCHIVE_BYTES, "Compressed artifact archive is too large")
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in checked_members(archive):
            parts = [part for part in PurePosixPath(member.name).parts if part != "."]
            if parts and parts[0] not in ALLOWED_ROOTS:
                parts = parts[1:]
            if not parts:
                continue
            require(parts[0] in ALLOWED_ROOTS, "Unexpected artifact root")
            target = output.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)


def validate_result(result, config, workflow):
    require(isinstance(result, dict) and result.get("schema_version") == 1, "Unsupported cluster result schema")
    require(result.get("workflow_uid") == workflow["uid"], "Cluster result belongs to a different workflow")
    require(result.get("status") in {"changes", "no-changes", "failed"}, "Invalid cluster outcome")
    require(result.get("candidate_version") == config["request"]["candidate"]["version"], "Cluster result candidate version mismatch")
    require(result.get("rfct_image") == config["request"]["rfct_image"], "Cluster result image mismatch")
    return result


def collect(config, workflow):
    prefix = s3_url(config["request"]["output_prefix"]) + "/" + workflow["uid"]
    output = config["output"]
    result_path, archive_path = output / "result.json", output / ".artifacts.tgz"
    progress(config, "artifact-collection-start", workflow_name=workflow["name"],
             workflow_uid=workflow["uid"], source_prefix=prefix)
    command(["aws", "s3", "cp", prefix + "/result.json", str(result_path), "--only-show-errors"])
    result = validate_result(json.loads(result_path.read_text(encoding="utf-8")), config, workflow)
    progress(config, "cluster-result-validated", status=result["status"],
             candidate_version=result["candidate_version"], image=result["rfct_image"])
    command(["aws", "s3", "cp", prefix + "/artifacts.tgz", str(archive_path), "--only-show-errors"])
    archive_bytes = archive_path.stat().st_size
    try:
        extract_artifacts(archive_path, output)
    finally:
        archive_path.unlink(missing_ok=True)
    # Validate again because the result also travels in the artifact archive.
    validate_result(json.loads(result_path.read_text(encoding="utf-8")), config, workflow)
    require((output / "execution.json").is_file(), "Cluster evidence is missing execution.json")
    if result["status"] != "failed":
        require((output / "comparison" / "report.json").is_file(), "Cluster evidence is missing comparison/report.json")
        require((output / "docs" / "CIMChanges" / result["candidate_version"] / "index.md").is_file(), "Cluster evidence is missing versioned documentation")
    progress(config, "artifact-collection-complete", status=result["status"],
             archive_bytes=archive_bytes, files=sum(path.is_file() for path in output.rglob("*")),
             output_directory=str(output))
    return result


def outputs(config, workflow, status):
    values = {"status": status}
    if config:
        values.update({"candidate-version": config["request"]["candidate"]["version"],
                       "image-digest": config["request"]["rfct_image"].split("@", 1)[1],
                       "execution-result-path": str(config["output"] / "execution.json"),
                       "result-path": str(config["output"] / "result.json"), "artifact-directory": str(config["output"])})
    if workflow:
        values.update({"workflow-name": workflow["name"], "workflow-uid": workflow["uid"]})
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")


def run_controller(config, state):
    progress(config, "request-upload-start", destination=config["request_url"])
    command(["aws", "s3", "cp", str(config["request_path"]), config["request_url"], "--only-show-errors"])
    state["request_sha256"] = hashlib.sha256(config["request_path"].read_bytes()).hexdigest()
    progress(config, "request-upload-complete", request_sha256=state["request_sha256"])
    state["workflow"] = submit(config)
    state["status"] = "running"
    # Publish the server identity before waiting. If the runner is forcibly
    # killed, a following always/cancelled step can still request Argo cleanup.
    checkpoint(config, state)
    state["phase"] = wait(config, state["workflow"], state["cancelled"])
    progress(config, "workflow-terminal", workflow_name=state["workflow"]["name"], phase=state["phase"])
    try:
        result = collect(config, state["workflow"])
    except (ControllerError, OSError, ValueError, tarfile.TarError) as error:
        if state["phase"] != "Succeeded":
            state["collection_error"] = sanitize(error)
            progress(config, "artifact-collection-incomplete", reason=type(error).__name__, message=str(error))
            raise ControllerError("workflow-failed", "RFCT workflow failed before complete evidence was available") from error
        raise
    state["status"] = result["status"]
    progress(config, "comparison-result", status=state["status"], phase=state["phase"])
    if state["phase"] != "Succeeded" or result["status"] == "failed":
        raise ControllerError("comparison-failed", "RFCT workflow did not complete successfully")


def checkpoint(config, state):
    evidence = {key: value for key, value in state.items() if key != "cancelled"}
    (config["output"] / "controller.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    outputs(config, state["workflow"], state["status"])


def finish(config, state):
    if config and not state["workflow"] and config.get("submitted_name"):
        state["workflow"] = {"name": config["submitted_name"], "uid": ""}
    if config and state["workflow"] and state["phase"] not in TERMINAL_PHASES:
        try:
            stop(config, state["workflow"])
        except (ControllerError, OSError) as error:
            state["cleanup_error"] = sanitize(error)
            progress(config, "workflow-stop-failed", message=str(error))
            warning("Could not request RFCT cleanup; server deadline and namespace TTL remain active: " + str(error))
    if config:
        checkpoint(config, state)
        progress(config, "controller-finished", status=state["status"], phase=state["phase"],
                 reason=state.get("reason", ""), cleanup_error=state.get("cleanup_error", ""))
    else:
        outputs(config, state["workflow"], state["status"])


def main():
    config = None
    state = {"schema_version": 1, "workflow": None, "phase": None, "status": "failed", "cancelled": [False]}
    handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        handlers[signum] = signal.signal(signum, lambda _signum, _frame: state["cancelled"].__setitem__(0, True))
    exit_code = 0
    try:
        config = validate_inputs()
        request = config["request"]
        progress(config, "request-validated", repository=request["run"]["repository"],
                 run_id=request["run"]["id"], attempt=request["run"]["attempt"],
                 baseline_version=request["baseline"]["version"], candidate_version=request["candidate"]["version"],
                 manifests_ref=config["manifests_ref"], image=request["rfct_image"], timeout_seconds=config["timeout"])
        run_controller(config, state)
    except (ControllerError, OSError, ValueError, tarfile.TarError) as error:
        if state["cancelled"][0]:
            # GitHub can signal the entire process group. Its Argo child may
            # then exit before the polling loop observes our cancellation flag.
            state["interrupted_operation"] = sanitize(error)
            error = ControllerError("cancelled", "RFCT comparison cancelled", 130)
        state.update({"status": "failed", "reason": getattr(error, "reason", "controller-failed"), "message": sanitize(error)})
        exit_code = getattr(error, "exit_code", 1)
        progress(config, "controller-failed", reason=state["reason"], message=state["message"], exit_code=exit_code)
    finally:
        try:
            finish(config, state)
        except OSError as error:
            progress(config, "controller-finalization-failed", message=str(error))
            exit_code = exit_code or 1
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
