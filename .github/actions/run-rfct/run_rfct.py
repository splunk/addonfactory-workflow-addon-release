"""Invoke the published RFCT image against prepared Splunk environments.

This helper is deliberately narrow. The calling workflow owns event routing,
release selection, package acquisition, cloud credentials, environment
provisioning, and ingestion. Here we only validate the prepared inputs, reject an
image whose formatter cannot produce the agreed CIM tables, run ``compare
report`` followed by ``format-report docs-markdown``, and preserve the execution
status with sanitized diagnostics.
"""

import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed docker argv, never a shell string
import sys
from pathlib import Path

SCHEMA_VERSION = 1
# Overrides are restricted to the approved RFCT repository and must be pinned by
# digest, so a mutable tag can never reach the comparison.
IMAGE_RE = re.compile(
    r"\A\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/ta-automation/rfct"
    r"@sha256:[0-9a-f]{64}\Z"
)
APP_CODE_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
# beta.7 predates the Table 1/Table 2 formatter and has no --ta-repo-path. Its
# Markdown must never be published as a conforming CIM changes result.
REQUIRED_FORMATTER_FLAG = "--ta-repo-path"
SECRET_RE = re.compile(
    r"(?i)\b(pass(?:word|wd)?|token|secret|api[_-]?key|authorization)\b"
    r"(\s*[:=]\s*|\s+)(\S+)"
)
MAX_DIAGNOSTIC_CHARS = 4000

CONTAINER_CONFIG = "/rfct/config.json"
CONTAINER_CANDIDATE = "/rfct/candidate"
CONTAINER_OUTPUT = "/rfct/output"
REPORT_RELATIVE = "comparison/report.json"
DOCS_RELATIVE = "docs"


class ExecutionError(RuntimeError):
    """A phase failed; the action reports the reason and fails its step."""

    def __init__(self, reason, message, exit_code=1):
        super().__init__(message)
        self.reason = reason
        self.exit_code = exit_code


def sanitize(text):
    """Redact credential-shaped values so diagnostics can be uploaded."""
    if not text:
        return ""
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    if len(redacted) > MAX_DIAGNOSTIC_CHARS:
        return redacted[:MAX_DIAGNOSTIC_CHARS] + "\n[truncated]"
    return redacted


def _input(name):
    return (os.environ.get(f"INPUT_{name}") or "").strip()


def _workflow_message(level, message):
    escaped = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


def _require_local_path(value, name, kind):
    if not value:
        raise ExecutionError("invalid-input", f"Missing required input {name}")
    path = Path(value)
    if not path.is_absolute():
        raise ExecutionError("invalid-input", f"{name} must be an absolute path, got '{value}'")
    if path.is_symlink():
        raise ExecutionError("invalid-input", f"{name} must not be a symbolic link")
    if kind == "file" and not path.is_file():
        raise ExecutionError("invalid-input", f"{name} must be an existing regular file: {value}")
    if kind == "directory" and not path.is_dir():
        raise ExecutionError("invalid-input", f"{name} must be an existing directory: {value}")
    return path


def validate_inputs():
    """Validate the prepared configuration, local paths, and image reference."""
    image = _input("RFCT_IMAGE")
    if not IMAGE_RE.match(image):
        raise ExecutionError(
            "invalid-input",
            "rfct-image must reference ta-automation/rfct in ECR pinned by digest",
        )

    app_code_name = _input("APP_CODE_NAME")
    if not APP_CODE_NAME_RE.match(app_code_name):
        raise ExecutionError("invalid-input", f"app-code-name is not a valid app name: '{app_code_name}'")

    config_path = _require_local_path(_input("CONFIG_PATH"), "config-path", "file")
    candidate_source = _require_local_path(_input("CANDIDATE_SOURCE"), "candidate-source", "directory")
    if (candidate_source / ".git").exists():
        raise ExecutionError(
            "invalid-input",
            "candidate-source must not contain .git; pass a checkout exported without history",
        )

    output_directory = Path(_input("OUTPUT_DIRECTORY") or "")
    if not output_directory.is_absolute():
        raise ExecutionError("invalid-input", "output-directory must be an absolute path")
    if output_directory.is_symlink():
        raise ExecutionError("invalid-input", "output-directory must not be a symbolic link")

    return {
        "image": image,
        "app_code_name": app_code_name,
        "config_path": config_path,
        "candidate_source": candidate_source,
        "output_directory": output_directory,
    }


def prepare_output_directory(output_directory):
    """Create the evidence tree writable by the image's default UID 10001."""
    for relative in ("", "comparison", DOCS_RELATIVE, "logs"):
        directory = output_directory / relative if relative else output_directory
        directory.mkdir(parents=True, exist_ok=True)
        # The RFCT image runs as a fixed non-root UID that does not exist on the
        # runner, so the writable evidence tree has to be world-writable.
        directory.chmod(0o777)  # nosec B103 - ephemeral runner evidence directory


def run_phase(name, argv, logs_directory):
    """Run one docker phase, recording its sanitized output and exit code."""
    completed = subprocess.run(  # nosec B603 - fixed argv list, shell is never used
        argv,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = sanitize(f"$ {' '.join(argv)}\n{completed.stdout}\n{completed.stderr}")
    log_path = logs_directory / f"{name}.log"
    log_path.write_text(combined + "\n", encoding="utf-8")
    return completed.returncode, sanitize(completed.stdout), sanitize(completed.stderr)


def docker_available():
    if shutil.which("docker") is None:
        raise ExecutionError("invalid-input", "docker is required on the runner but was not found")


def preflight_argv(image):
    return ["docker", "run", "--rm", "--network", "none", image, "format-report", "docs-markdown", "--help"]


# The compare and format flag names below follow the documented RFCT CLI
# contract. Re-confirm them against the first stable image before switching the
# rfct-image default away from the beta digest; beta.7 is rejected by preflight.
def compare_argv(settings, container_name):
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "-v",
        f"{settings['config_path']}:{CONTAINER_CONFIG}:ro",
        "-v",
        f"{settings['output_directory']}:{CONTAINER_OUTPUT}",
        settings["image"],
        "compare",
        "report",
        "--config",
        CONTAINER_CONFIG,
        "--output",
        f"{CONTAINER_OUTPUT}/{REPORT_RELATIVE}",
    ]


def format_argv(settings, container_name):
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "-v",
        f"{settings['candidate_source']}:{CONTAINER_CANDIDATE}:ro",
        "-v",
        f"{settings['output_directory']}:{CONTAINER_OUTPUT}",
        settings["image"],
        "format-report",
        "docs-markdown",
        "--report",
        f"{CONTAINER_OUTPUT}/{REPORT_RELATIVE}",
        REQUIRED_FORMATTER_FLAG,
        CONTAINER_CANDIDATE,
        "--app-code-name",
        settings["app_code_name"],
        "--output",
        f"{CONTAINER_OUTPUT}/{DOCS_RELATIVE}",
    ]


def check_formatter_compatibility(settings, execution, logs_directory):
    """Reject an image whose formatter cannot produce the agreed CIM tables."""
    exit_code, stdout, stderr = run_phase("preflight", preflight_argv(settings["image"]), logs_directory)
    execution["phases"]["preflight"] = exit_code
    if exit_code != 0:
        raise ExecutionError(
            "image-preflight-failed",
            f"The RFCT image could not report formatter capabilities: {stderr or stdout}",
            exit_code,
        )
    if REQUIRED_FORMATTER_FLAG not in stdout:
        raise ExecutionError(
            "incompatible-formatter",
            f"The RFCT image formatter does not support {REQUIRED_FORMATTER_FLAG}; "
            "it predates the required CIM changes tables and must not be used",
            exit_code,
        )


def read_outcome(report_path):
    """Derive the outcome from validated RFCT output, never from missing output."""
    if not report_path.is_file():
        raise ExecutionError("invalid-report", f"RFCT produced no comparison report at {report_path}")
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutionError("invalid-report", f"The comparison report is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExecutionError("invalid-report", "The comparison report must be a JSON object")

    candidate = data.get("candidate") if isinstance(data.get("candidate"), dict) else {}
    candidate_version = candidate.get("version") or data.get("candidate_version")
    if not candidate_version:
        raise ExecutionError("invalid-report", "The comparison report records no candidate version")

    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    if isinstance(summary.get("has_changes"), bool):
        has_changes = summary["has_changes"]
    elif isinstance(data.get("changes"), (list, dict)):
        has_changes = bool(data["changes"])
    else:
        raise ExecutionError(
            "invalid-report",
            "The comparison report declares no change outcome; a missing outcome "
            "must never be reported as no-changes",
        )
    return ("changes" if has_changes else "no-changes"), str(candidate_version)


def write_execution(path, execution):
    path.write_text(json.dumps(execution, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_outputs(outputs):
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def remove_invocation_containers(names):
    """Clean up only this action's own containers."""
    for name in names:
        subprocess.run(  # nosec B603 - fixed argv list, shell is never used
            ["docker", "rm", "--force", name],
            capture_output=True,
            text=True,
            check=False,
        )


def _invocation_suffix():
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return f"{run_id}-{attempt}"


def execute():
    docker_available()
    settings = validate_inputs()
    prepare_output_directory(settings["output_directory"])

    output_directory = settings["output_directory"]
    logs_directory = output_directory / "logs"
    report_path = output_directory / REPORT_RELATIVE
    execution_path = output_directory / "execution.json"
    suffix = _invocation_suffix()
    container_names = [f"rfct-compare-{suffix}", f"rfct-format-{suffix}"]

    execution = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "reason": "",
        "image": settings["image"],
        "image_digest": settings["image"].partition("@")[2],
        "app_code_name": settings["app_code_name"],
        "candidate_version": "",
        "phases": {},
        "produced": {
            "comparison_report": str(report_path),
            "docs_directory": str(output_directory / DOCS_RELATIVE),
        },
        "diagnostics": [],
    }
    # Written before comparison so a later crash still leaves evidence behind.
    write_execution(execution_path, execution)

    try:
        check_formatter_compatibility(settings, execution, logs_directory)

        exit_code, stdout, stderr = run_phase(
            "compare", compare_argv(settings, container_names[0]), logs_directory
        )
        execution["phases"]["compare"] = exit_code
        if exit_code != 0:
            raise ExecutionError("comparison-failed", stderr or stdout, exit_code)

        exit_code, stdout, stderr = run_phase(
            "format", format_argv(settings, container_names[1]), logs_directory
        )
        execution["phases"]["format"] = exit_code
        if exit_code != 0:
            raise ExecutionError("formatter-failed", stderr or stdout, exit_code)

        status, candidate_version = read_outcome(report_path)
        execution["status"] = status
        execution["candidate_version"] = candidate_version
    except ExecutionError as error:
        execution["status"] = "failed"
        execution["reason"] = error.reason
        execution["diagnostics"].append(sanitize(str(error)))
        raise
    finally:
        remove_invocation_containers(container_names)
        write_execution(execution_path, execution)
        write_outputs(
            {
                "status": execution["status"],
                "candidate-version": execution["candidate_version"],
                "image-digest": execution["image_digest"],
                "execution-result-path": str(execution_path),
                "artifact-directory": str(output_directory),
            }
        )

    return execution


def main():
    execution = execute()
    print(json.dumps(execution, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except ExecutionError as exc:
        _workflow_message("error", f"{exc.reason}: {exc}")
        sys.exit(exc.exit_code or 1)
    except OSError as exc:
        _workflow_message("error", exc)
        sys.exit(str(exc))
