"""Conservative presence reconciliation against an existing SPL, without extraction.

Python membership follows packaged dist-info identities. Other ecosystems use
package presence without requiring exact version or code provenance.
"""
import configparser
import hashlib
import json
import re
import tarfile
from collections import Counter
from email.parser import BytesParser
from pathlib import PurePosixPath
from urllib.parse import urlparse

BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _mapped_sources(mappings):
    source, counts = 0, Counter()
    for line in mappings.split(";"):
        for segment in line.split(","):
            values, value, shift = [], 0, 0
            for char in segment:
                digit = BASE64.index(char)
                value |= (digit & 31) << shift
                if digit & 32:
                    shift += 5
                    if shift > 63:
                        raise ValueError("Invalid source-map VLQ")
                    continue
                values.append(-(value >> 1) if value & 1 else value >> 1)
                value, shift = 0, 0
            if shift:
                raise ValueError("Incomplete source-map VLQ")
            if len(values) >= 4:
                source += values[1]
                counts[source] += 1
    return counts


def _package_name(source):
    source = source.replace("\\", "/")
    if "node_modules/" not in source:
        return None
    tail = source.rsplit("node_modules/", 1)[1]
    return "/".join(tail.split("/")[:2]) if tail.startswith("@") else tail.split("/")[0]


def _map_observations(path, raw, paths):
    if path[:-4] not in paths:
        return []
    value = json.loads(raw)
    if value.get("sections"):
        raise ValueError("Indexed source-map coverage is unsupported")
    sources = value.get("sources", [])
    counts = _mapped_sources(value.get("mappings", ""))
    if any(i < 0 or i >= len(sources) for i in counts):
        raise ValueError("Invalid source-map source index")
    content = value.get("sourcesContent") or []
    result = []
    for index, source in enumerate(sources):
        name = _package_name(source)
        if not name or not counts[index]:
            continue
        item = dict(name=name, version=None, kind="mapped-source", path=path,
                    source=source, mapped_segments=counts[index])
        if index < len(content) and isinstance(content[index], str):
            item["content_sha256"] = hashlib.sha256(content[index].encode()).hexdigest()
        result.append(item)
    return result


def _observe(path, raw):
    result = []
    if path.endswith(".dist-info/METADATA"):
        metadata = BytesParser().parsebytes(raw)
        name, version = metadata.get_all("Name", []), metadata.get_all("Version", [])
        if len(name) != 1 or len(version) != 1 or not name[0].strip() or not version[0].strip():
            raise ValueError(f"Invalid Python distribution metadata: {path}")
        result.append(dict(name=name[0].strip(), version=version[0].strip(), kind="python-metadata", path=path))
    if PurePosixPath(path).name.lower() == "licenses.txt":
        text = raw.decode("utf-8", errors="replace")
        for name, version in re.findall(r"^Name: ([^\n]+)\nVersion: ([^\n]+)", text, re.M):
            result.append(dict(name=name.strip(), version=version.strip(), kind="attribution", path=path))
    return result


def _check_release(files, expected_version, expected_app):
    if expected_version is None:
        return
    paths = [p for p in files if p.endswith("/default/app.conf")]
    if expected_app:
        paths = [p for p in paths if p == f"{expected_app}/default/app.conf"]
    if len(paths) != 1:
        raise ValueError("Artifact does not identify the expected TA application")
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(files[paths[0]].decode("utf-8"))
    except configparser.Error:
        raise ValueError("Invalid artifact app.conf") from None
    if parser.get("id", "version", fallback=None) != expected_version:
        raise ValueError("Artifact release version differs from draft")


def inspect_artifact(path, expected_version=None, expected_app=None):
    """Read all members; never extract, execute or alter the supplied archive."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    files = {}
    with tarfile.open(path) as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or member.issym() or member.islnk():
                raise ValueError("Unsafe artifact member")
            if member.isfile():
                if name in files:
                    raise ValueError("Duplicate artifact member")
                files[name] = archive.extractfile(member).read()
    _check_release(files, expected_version, expected_app)
    observations, warnings, bound = [], [], 0
    for name, raw in files.items():
        observations.extend(_observe(name, raw))
        if not name.endswith(".map"):
            continue
        try:
            observations.extend(_map_observations(name, raw, files))
            bound += int(name[:-4] in files)
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            warnings.append(f"{name}: incomplete map coverage: {error}")
    js = sum(n.endswith((".js", ".mjs", ".cjs")) for n in files)
    return dict(sha256=digest.hexdigest(), observations=observations,
                coverage=dict(regular_files=len(files), javascript_files=js,
                              python_metadata_files=sum(o["kind"] == "python-metadata" for o in observations),
                              bound_source_maps=bound, warnings=warnings,
                              native_binaries="not assessed; exact identities require review"))


def _same_name(name, observation):
    if observation["kind"] == "python-metadata":
        return re.sub(r"[-_.]+", "-", name).lower() == re.sub(r"[-_.]+", "-", observation["name"]).lower()
    return name == observation["name"]


def component_ecosystem(component):
    """Use explicit/reported ecosystem, with PyPI URLs for legacy additions."""
    ecosystem = (component.get("_ecosystem") or component.get("ecosystem")
                 or component.get("fossa_evidence", {}).get("source"))
    if ecosystem:
        return ecosystem
    try:
        host = urlparse(component.get("project_url") or "").hostname
    except ValueError:
        return None
    if host in ("pypi.org", "pypi.python.org"):
        return "pip"
    return None


def _python_decision(component, inventory):
    names = [component["name"]] + component.get("_source_names", [])
    observations = [o for o in inventory["observations"] if o["kind"] == "python-metadata"
                    and any(_same_name(name, o) for name in names)]
    version = component["version"].removeprefix("v")
    exact = [o for o in observations if o["version"].removeprefix("v") == version]
    return dict(name=component["name"], version=component["version"],
                disposition="present" if exact else "not_shipped", policy="python-dist-info",
                evidence=exact, observed_versions=sorted({o["version"] for o in observations}),
                other_version_evidence=[o for o in observations if o not in exact],
                reason=("Python name/version found in packaged .dist-info/METADATA" if exact else
                        "Python name/version absent from packaged .dist-info/METADATA; omitted by policy"))


def python_metadata_additions(components, inventory):
    """Return unique packaged Python identities not represented by input rows."""
    observations = [o for o in inventory["observations"] if o["kind"] == "python-metadata"]
    represented = set()
    for component in components:
        if component_ecosystem(component) not in (None, "pip", "git"):
            continue
        represented.update(o["path"] for o in _python_decision(component, inventory)["evidence"])
    added = {}
    for observation in observations:
        if observation["path"] in represented:
            continue
        identity = (re.sub(r"[-_.]+", "-", observation["name"]).lower(), observation["version"].removeprefix("v"))
        if identity not in added:
            added[identity] = dict(name=observation["name"], version=observation["version"], evidence=[])
        added[identity]["evidence"].append(observation)
    return list(added.values())


def scope_decision(component, inventory, reviewed=None):
    if component_ecosystem(component) == "pip":
        return _python_decision(component, inventory)
    name, version = component["name"], component["version"]
    matches = [o for o in inventory["observations"] if _component_matches(component, o)]
    result = dict(name=name, version=version, disposition="unverified", evidence=matches)
    if reviewed and reviewed["artifact_sha256"] != inventory["sha256"]:
        result.update(disposition="unknown", reason="reviewed scope belongs to a different artifact checksum")
    elif reviewed and reviewed["disposition"] == "not_shipped" and matches:
        result.update(disposition="unknown", reason="reviewed absence contradicts artifact presence evidence")
    elif reviewed:
        result.update(disposition=reviewed["disposition"], reason=reviewed["reason"], references=reviewed["references"])
    elif matches:
        result.update(disposition="present", reason="package identity found in the artifact; exact version/code provenance is outside this check")
    else:
        result["reason"] = "available checks cannot identify this package; retained without a scope blocker"
    return result


def reconcile_scope(components, inventory, reviewed=None):
    reviewed = reviewed or {}
    decisions = [scope_decision(c, inventory, reviewed.get((c["name"], c["version"]))) for c in components]
    return dict(artifact_sha256=inventory["sha256"], coverage=inventory["coverage"],
                decisions=decisions)


def _component_matches(component, observation):
    if observation["kind"] == "python-metadata" and component_ecosystem(component) not in (None, "pip", "git"):
        return False
    names = [component["name"]] + component.get("_source_names", [])
    return any(_same_name(name, observation) for name in names)
