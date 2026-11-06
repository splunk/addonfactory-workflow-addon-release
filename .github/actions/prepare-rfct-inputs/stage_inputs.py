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
import tarfile
from pathlib import Path, PurePosixPath

IMAGE_RE = re.compile(
    r"956110764581\.dkr\.ecr\.us-west-2\.amazonaws\.com/ta-automation/rfct@sha256:[0-9a-f]{64}"
)
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?")


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
    return {"app_code_name": app_name, "version": version, "sha256": sha256(filename)}


def archive_source(source, target):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Candidate source must be a directory")
    if source.resolve() in Path(target).resolve().parents:
        raise ValueError("Source archive must be written outside candidate source")
    total_size = 0
    # Fixed archive metadata makes the source checksum reproducible.
    with Path(target).open("wb") as output, gzip.GzipFile(
        filename="", fileobj=output, mode="wb", mtime=0
    ) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        for entry in sorted(source.rglob("*")):
            relative = entry.relative_to(source)
            if ".git" in relative.parts:
                continue
            if entry.is_symlink():
                raise ValueError("Source symlinks are unsupported")
            if entry.is_dir():
                continue
            regular_file(entry)
            total_size += entry.stat().st_size
            if total_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Candidate source exceeds input limits")
            info = archive.gettarinfo(str(entry), arcname=str(relative))
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            with entry.open("rb") as stream:
                archive.addfile(info, stream)


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
    image = settings["image"]
    if not IMAGE_RE.fullmatch(image):
        raise ValueError("RFCT image must be an approved repository digest")
    if not re.fullmatch(r"[0-9a-f]{40}", settings["manifests_ref"]):
        raise ValueError("RFCT manifests ref must be an immutable commit SHA")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", settings["bucket"]):
        raise ValueError("Invalid artifact bucket")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", settings["repository"]):
        raise ValueError("Invalid calling repository")
    run = validate_metadata(provenance, settings)
    baseline, candidate = package_file(baseline), package_file(candidate)
    inspected = {"baseline": inspect_package(baseline), "candidate": inspect_package(candidate)}
    if inspected["baseline"]["app_code_name"] != inspected["candidate"]["app_code_name"]:
        raise ValueError("Baseline and candidate app identities differ")
    if any(not VERSION_RE.fullmatch(metadata["version"]) for metadata in inspected.values()):
        raise ValueError("Package version is not supported by the RFCT request contract")
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
    archive = directory / "candidate-source.tar.gz"
    archive_source(source, archive)
    request["candidate"].update(
        source_url=f"{prefix}/inputs/{archive.name}", source_sha256=sha256(archive),
        source_sha=provenance["candidate"]["commit"],
    )
    return request


def main():
    env = os.environ
    provenance = json.loads(regular_file(env["INPUT_PROVENANCE_PATH"]).read_text())
    directory = Path(env["INPUT_STAGING_DIRECTORY"])
    settings = {key: env.get(f"INPUT_{key.upper()}", "") for key in (
        "image", "manifests_ref", "bucket", "repository", "splunk_version", "sc4s_version", "sc4s_registry"
    )}
    request = stage_inputs(
        provenance, env["INPUT_BASELINE_PACKAGE"], env["INPUT_CANDIDATE_PACKAGE"],
        env["INPUT_CANDIDATE_SOURCE"], directory, settings,
    )
    request_path = directory.parent / "request.json"
    request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    outputs = {
        "request-path": str(request_path), "request-url": request["inputs_prefix"] + "/request.json",
        "inputs-directory": str(directory), "inputs-prefix": request["inputs_prefix"],
        "app-code-name": request["app_code_name"], "candidate-version": request["candidate"]["version"],
        "image-digest": request["rfct_image"].split("@", 1)[1],
    }
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
