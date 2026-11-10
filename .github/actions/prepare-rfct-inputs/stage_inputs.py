#!/usr/bin/env python3
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
"""Freeze and validate RFCT inputs without executing add-on code."""

import configparser
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

IMAGE_RE = re.compile(
    r"956110764581\.dkr\.ecr\.us-west-2\.amazonaws\.com/ta-automation/rfct@sha256:[0-9a-f]{64}"
)
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
MANIFESTS_REPOSITORY_API = "https://api.github.com/repos/splunk/ta-automation-k8s-manifests/"
MANIFESTS_API = MANIFESTS_REPOSITORY_API + "commits/"


def log(event, *, token="", **details):
    """Emit one bounded metadata record; never include payloads or credential fields."""
    record = {"event": event, **details}
    secrets = (token, os.environ.get("INPUT_GITHUB_TOKEN", ""))
    for key, value in record.items():
        if isinstance(value, str):
            for secret in secrets:
                if isinstance(secret, str) and secret:
                    value = value.replace(secret, "***")
            record[key] = value if len(value) <= 1024 else value[:1024] + "...[truncated]"
    # GitHub recognizes embedded command markers even inside a prefixed line.
    message = json.dumps(record, sort_keys=True).replace("::", r"\u003a\u003a")
    print("[rfct-inputs] " + message, file=sys.stderr, flush=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Credentials must only reach the fixed API endpoint, even on redirects.
        return None


def valid_manifests_ref(reference):
    if not isinstance(reference, str) or not reference or len(reference) > 1024:
        return False
    if re.search(r"[\x00-\x20\x7f~^:?*\[\\]", reference):
        return False
    if any(part in reference for part in ("..", "//", "@{")):
        return False
    if reference == "@" or reference.startswith("/") or reference.endswith(("/", ".")):
        return False
    return not any(part.startswith(".") or part.endswith(".lock") for part in reference.split("/"))


def github_data(url, token, accept, limit, purpose, missing_message=""):
    if not isinstance(token, str) or not token.isascii() or re.search(r"[\x00-\x20\x7f]", token):
        raise ValueError("Invalid GitHub API token")
    headers = {
        "Accept": accept, "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "addonfactory-rfct-inputs",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:  # nosec B310 - fixed GitHub API host
            if response.status != 200:
                log("github-api-failed", token=token, operation=purpose, status=response.status)
                raise ValueError(f"{purpose}: unexpected GitHub API status")
            data = response.read(limit + 1)
    except HTTPError as error:
        log("github-api-failed", token=token, operation=purpose, status=error.code)
        if error.code == 404 and missing_message:
            raise ValueError(missing_message) from None
        raise ValueError(f"{purpose}: GitHub API HTTP {error.code}") from None
    except (URLError, OSError):
        log("github-api-failed", token=token, operation=purpose, reason="transport-error")
        raise ValueError(f"{purpose}: GitHub API request failed") from None
    if len(data) > limit:
        log("github-api-failed", token=token, operation=purpose, reason="response-size-limit", limit_bytes=limit)
        raise ValueError(f"{purpose}: GitHub API response exceeds the size limit")
    log("github-api-response", token=token, operation=purpose, status=200, response_bytes=len(data))
    return data


def check_manifests_runtime(commit, token):
    """Reject old shared refs before provisioning; fetch metadata from the resolved commit."""
    message = f"Resolved manifests commit {commit} has no RFCT runtime at rfct/run.py"
    log("manifests-runtime-check", token=token, resolved_sha=commit, path="rfct/run.py")
    data = github_data(
        MANIFESTS_REPOSITORY_API + f"contents/rfct/run.py?ref={commit}", token,
        "application/vnd.github+json", 256 * 1024, "Cannot verify RFCT runtime", message,
    )
    try:
        metadata = json.loads(data)
    except (ValueError, UnicodeError):
        raise ValueError("GitHub API did not return valid RFCT runtime metadata") from None
    if (not isinstance(metadata, dict) or metadata.get("type") != "file"
            or metadata.get("path") != "rfct/run.py" or not COMMIT_RE.fullmatch(str(metadata.get("sha", "")))):
        raise ValueError(message)
    log("manifests-runtime-compatible", token=token, resolved_sha=commit, path="rfct/run.py")


def resolve_manifests_ref(reference, token=""):
    """Resolve the fixed manifests repository once; the staging contract stays SHA-only."""
    if isinstance(reference, str) and re.fullmatch(r"[0-9a-fA-F]{40}", reference):
        log("manifests-reference-frozen", token=token, requested_ref=reference,
            resolved_sha=reference.lower(), resolution="provided-sha", runtime_check="not-queried")
        return reference.lower()
    if not valid_manifests_ref(reference):
        raise ValueError("Invalid manifests Git reference")
    log("manifests-resolution-start", token=token, requested_ref=reference,
        repository="splunk/ta-automation-k8s-manifests")
    # Get-a-commit resolves annotated tags to the commit, unlike the git/refs API.
    api_ref = reference.removeprefix("refs/")
    data = github_data(
        MANIFESTS_API + quote(api_ref, safe=""), token, "application/vnd.github.sha", 128,
        "Cannot resolve manifests ref",
    )
    try:
        resolved = data.decode("ascii").strip()
    except UnicodeError:
        raise ValueError("GitHub API did not return a manifests commit SHA") from None
    if not COMMIT_RE.fullmatch(resolved):
        raise ValueError("GitHub API did not return a manifests commit SHA")
    check_manifests_runtime(resolved, token)
    log("manifests-reference-frozen", token=token, requested_ref=reference,
        resolved_sha=resolved, resolution="github-api", runtime_check="compatible")
    return resolved


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular file: {path.name}")
    return path


def package_file(path):
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        packages = list(path.glob("*.spl"))
        if len(packages) != 1:
            raise ValueError("Expected exactly one .spl package per side")
        path = packages[0]
    return regular_file(path)


def member_path(member):
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe package member path")
    if not (member.isdir() or member.isfile()):
        raise ValueError("Package links and special files are unsupported")
    return path


def inspect_package(filename):
    filename = regular_file(filename)
    if filename.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Package exceeds the input size limit")
    app_names, files, total_size = set(), {}, 0
    with tarfile.open(filename, "r:gz") as archive:
        for member in archive:
            path = member_path(member)
            app_names.add(path.parts[0])
            total_size += member.size
            if total_size > MAX_ARCHIVE_BYTES or len(files) > 100000:
                raise ValueError("Expanded package exceeds input limits")
            if member.isfile():
                if str(path) in files:
                    raise ValueError("Duplicate package file")
                files[str(path)] = member
        if len(app_names) != 1:
            raise ValueError("Expected exactly one app directory in package")
        app_name = app_names.pop()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", app_name):
            raise ValueError("Invalid app directory name")
        config = configparser.ConfigParser(interpolation=None, strict=True)
        for location in ("default", "local"):
            member = files.get(f"{app_name}/{location}/app.conf")
            if member:
                if member.size > 1024 * 1024:
                    raise ValueError("app.conf exceeds the input size limit")
                config.read_string(archive.extractfile(member).read().decode("utf-8"))
        version = config.get("launcher", "version", fallback="").strip()
        if not version:
            raise ValueError("Package has no launcher version")
    metadata = {"app_code_name": app_name, "version": version, "sha256": sha256(filename)}
    log("package-inspected", package_name=filename.name, package_bytes=filename.stat().st_size,
        files=len(files), expanded_bytes=total_size, **metadata)
    return metadata


def archive_source(source, target):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Candidate source must be a directory")
    if source.resolve() in Path(target).resolve().parents:
        raise ValueError("Source archive must be written outside candidate source")
    total_size, file_count, excluded_git_entries = 0, 0, 0
    log("source-archive-start", destination=str(target))
    # Fixed archive metadata makes the source checksum reproducible.
    with Path(target).open("wb") as output, gzip.GzipFile(
        filename="", fileobj=output, mode="wb", mtime=0
    ) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        for entry in sorted(source.rglob("*")):
            relative = entry.relative_to(source)
            if ".git" in relative.parts:
                excluded_git_entries += 1
                continue
            if entry.is_symlink():
                raise ValueError("Source symlinks are unsupported")
            if entry.is_dir():
                continue
            regular_file(entry)
            file_count += 1
            total_size += entry.stat().st_size
            if total_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Candidate source exceeds input limits")
            info = archive.gettarinfo(str(entry), arcname=str(relative))
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            with entry.open("rb") as stream:
                archive.addfile(info, stream)
    log("source-archive-created", destination=str(target), files=file_count, source_bytes=total_size,
        archive_bytes=Path(target).stat().st_size, excluded_git_entries=excluded_git_entries)


def validate_metadata(provenance, settings):
    """Reject requests the cluster/controller cannot accept before staging files."""
    run = provenance["run"]
    if not all(re.fullmatch(r"[0-9]+", str(run[key])) for key in ("id", "attempt")):
        raise ValueError("Invalid run identifier")
    if int(run["attempt"]) < 1 or len(f"rfct-{run['id']}-{run['attempt']}") > 40:
        raise ValueError("Run identity cannot form a valid RFCT job name")
    for side in ("baseline", "candidate"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(provenance[side].get("commit", ""))):
            raise ValueError(f"{side} source must be frozen to a commit SHA")
    if not VERSION_RE.fullmatch(settings["splunk_version"]):
        raise ValueError("Invalid Splunk version")
    sc4s = settings.get("sc4s_version", "")
    if sc4s and sc4s != "No" and not VERSION_RE.fullmatch(sc4s):
        raise ValueError("Invalid SC4S version")
    registry = settings.get("sc4s_registry", "")
    if registry and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*", registry):
        raise ValueError("Invalid SC4S registry")
    return run


def stage_inputs(provenance, baseline, candidate, source, directory, settings):
    log("input-validation-start")
    image = settings["image"]
    if not IMAGE_RE.fullmatch(image):
        raise ValueError("RFCT image must be an approved repository digest")
    if not COMMIT_RE.fullmatch(settings["manifests_ref"]):
        raise ValueError("RFCT manifests ref must be an immutable commit SHA")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", settings["bucket"]):
        raise ValueError("Invalid artifact bucket")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", settings["repository"]):
        raise ValueError("Invalid calling repository")
    run = validate_metadata(provenance, settings)
    log("input-metadata-validated", repository=settings["repository"], run_id=run["id"],
        attempt=run["attempt"], manifests_sha=settings["manifests_ref"], image=image)
    baseline, candidate = package_file(baseline), package_file(candidate)
    inspected = {"baseline": inspect_package(baseline), "candidate": inspect_package(candidate)}
    if inspected["baseline"]["app_code_name"] != inspected["candidate"]["app_code_name"]:
        raise ValueError("Baseline and candidate app identities differ")
    if any(not VERSION_RE.fullmatch(metadata["version"]) for metadata in inspected.values()):
        raise ValueError("Package version is not supported by the RFCT request contract")
    log("package-identities-verified", app_code_name=inspected["candidate"]["app_code_name"],
        baseline_version=inspected["baseline"]["version"], candidate_version=inspected["candidate"]["version"])
    directory = Path(directory)
    if Path(source).resolve() in directory.resolve().parents:
        raise ValueError("Staging directory must be outside candidate source")
    directory.mkdir(parents=True, exist_ok=False)
    prefix = f"s3://{settings['bucket']}/rfct/{settings['repository']}/{run['id']}/{run['attempt']}"
    request = {
        "schema_version": 1,
        "run": {**run, "repository": settings["repository"]},
        "app_code_name": inspected["candidate"]["app_code_name"],
        "rfct_image": image,
        "splunk_version": settings["splunk_version"],
        "sc4s_version": settings.get("sc4s_version", ""),
        "sc4s_docker_registry": settings.get("sc4s_registry", ""),
        "inputs_prefix": f"{prefix}/inputs",
        "output_prefix": f"{prefix}/outputs",
        "job_name": f"rfct-{run['id']}-{run['attempt']}",
        "manifests_ref": settings["manifests_ref"],
    }
    for side, package in (("baseline", baseline), ("candidate", candidate)):
        metadata = inspected[side]
        expected = provenance[side].get("version", "")
        if expected and expected != metadata["version"]:
            raise ValueError(f"{side} package version disagrees with provenance")
        target = directory / f"{side}.spl"
        shutil.copyfile(package, target)
        request[side] = {
            **provenance[side],
            "version": metadata["version"],
            "package_sha256": metadata["sha256"],
            "package_url": f"{prefix}/inputs/{target.name}",
        }
        log("package-staged", side=side, destination=str(target), package_bytes=target.stat().st_size,
            package_sha256=metadata["sha256"], package_url=request[side]["package_url"],
            package_version=metadata["version"], source_commit=provenance[side]["commit"])
    archive = directory / "candidate-source.tar.gz"
    archive_source(source, archive)
    request["candidate"].update(
        source_url=f"{prefix}/inputs/{archive.name}", source_sha256=sha256(archive),
        source_sha=provenance["candidate"]["commit"],
    )
    log("source-input-frozen", source_sha256=request["candidate"]["source_sha256"],
        source_commit=request["candidate"]["source_sha"], source_url=request["candidate"]["source_url"])
    log("inputs-frozen", job_name=request["job_name"], inputs_prefix=request["inputs_prefix"],
        output_prefix=request["output_prefix"], input_files=3,
        input_bytes=sum((directory / name).stat().st_size
                        for name in ("baseline.spl", "candidate.spl", "candidate-source.tar.gz")))
    return request


def main():
    env = os.environ
    provenance = json.loads(regular_file(env["INPUT_PROVENANCE_PATH"]).read_text())
    directory = Path(env["INPUT_STAGING_DIRECTORY"])
    settings = {key: env.get(f"INPUT_{key.upper()}", "") for key in (
        "image", "manifests_ref", "bucket", "repository", "splunk_version", "sc4s_version", "sc4s_registry"
    )}
    requested_ref = settings["manifests_ref"]
    settings["manifests_ref"] = resolve_manifests_ref(requested_ref, env.get("INPUT_GITHUB_TOKEN", ""))
    request = stage_inputs(
        provenance, env["INPUT_BASELINE_PACKAGE"], env["INPUT_CANDIDATE_PACKAGE"],
        env["INPUT_CANDIDATE_SOURCE"], directory, settings,
    )
    request["manifests_source_ref"] = requested_ref
    request_path = directory.parent / "request.json"
    request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    log("request-written", destination=str(request_path), request_bytes=request_path.stat().st_size,
        request_sha256=sha256(request_path), request_url=request["inputs_prefix"] + "/request.json",
        requested_manifests_ref=requested_ref, manifests_sha=request["manifests_ref"])
    outputs = {
        "request-path": str(request_path), "request-url": request["inputs_prefix"] + "/request.json",
        "inputs-directory": str(directory), "inputs-prefix": request["inputs_prefix"],
        "app-code-name": request["app_code_name"], "candidate-version": request["candidate"]["version"],
        "image-digest": request["rfct_image"].split("@", 1)[1],
        "manifests-ref": request["manifests_ref"],
    }
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
    log("action-outputs-written", outputs=len(outputs), request_path=str(request_path),
        inputs_directory=str(directory), job_name=request["job_name"])


def failure_reason(error):
    # Our validation messages are metadata-only. Dependency/parser messages may
    # embed source contents, so retain text only for ValueErrors raised here.
    origin = error.__traceback__
    while origin and origin.tb_next:
        origin = origin.tb_next
    if type(error) is ValueError and origin and origin.tb_frame.f_globals is globals():
        return str(error)
    if isinstance(error, tarfile.TarError):
        return "Cannot read the input package archive"
    if isinstance(error, OSError):
        if isinstance(error.errno, int):
            return f"Input I/O operation failed (errno {error.errno})"
        return "Input I/O operation failed"
    return "Unexpected input preparation failure"


def cli():
    try:
        main()
    except Exception as error:
        log("input-preparation-failed", error_type=type(error).__name__, reason=failure_reason(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
