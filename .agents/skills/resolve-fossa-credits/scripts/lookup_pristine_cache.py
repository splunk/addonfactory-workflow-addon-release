#!/usr/bin/env python3
"""Inspect one exact package/version in the pinned license-credits-generator cache.

This is a research aid, not an automatic licensing decision. In particular,
``copyright_derived`` may have been extracted from curated rather than verbatim
upstream license text.
"""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = SKILL_DIR / "data" / "pristine-cache.json.gz"
DEFAULT_MANIFEST = SKILL_DIR / "data" / "pristine-cache-source.json"


def load_cache(cache_path=DEFAULT_CACHE, manifest_path=DEFAULT_MANIFEST):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    compressed = Path(cache_path).read_bytes()
    actual_compressed = hashlib.sha256(compressed).hexdigest()
    if actual_compressed != manifest["compressed_sha256"]:
        raise ValueError("pristine cache compressed SHA-256 differs from its manifest")
    raw = gzip.decompress(compressed)
    actual_raw = hashlib.sha256(raw).hexdigest()
    if actual_raw != manifest["sha256"]:
        raise ValueError("pristine cache SHA-256 differs from its manifest")
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("pristine cache must be an object keyed by package name")
    return document, manifest


def lookup(document, name, version):
    """Return only an exact name/version match; never borrow another release."""
    versions = document.get(name, [])
    if not isinstance(versions, list) or any(not isinstance(item, dict) for item in versions):
        raise ValueError(f"invalid pristine cache versions for {name}")
    matches = [item for item in versions if item.get("version") == version]
    if len(matches) > 1:
        raise ValueError(f"duplicate pristine cache entries for {name}@{version}")
    return matches[0] if matches else None


def summarize(name, version, entry, manifest, full=False):
    result = {
        "name": name,
        "version": version,
        "found": entry is not None,
        "cache_source": {
            "repository": manifest["repository"],
            "commit": manifest["commit"],
            "sha256": manifest["sha256"],
        },
    }
    if entry is None:
        return result
    result["license"] = entry.get("spdx_id")
    result["copyright_candidates"] = {
        field: entry[field]
        for field in ("copyright_override", "copyright", "copyright_derived")
        if entry.get(field)
    }
    for field in ("copyright_override", "copyright", "copyright_derived"):
        if entry.get(field):
            result["preferred_copyright"] = {"field": field, "value": entry[field]}
            break
    else:
        result["preferred_copyright"] = None
    result["license_text_sha256"] = (
        hashlib.sha256(entry["license_text"].encode("utf-8")).hexdigest()
        if isinstance(entry.get("license_text"), str) else None
    )
    result["provenance"] = {
        field: entry[field]
        for field in ("confidence", "source", "source_url", "picked_file",
                      "verified_against", "retrieved_date", "notes", "note",
                      "copyright_override_source", "copyright_override_note",
                      "copyright_derived_source", "purl", "purl_source")
        if field in entry
    }
    if full:
        result["entry"] = entry
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--full", action="store_true", help="include the full cache entry and license text")
    args = parser.parse_args(argv)
    try:
        document, manifest = load_cache(args.cache, args.manifest)
        entry = lookup(document, args.name, args.version)
        print(json.dumps(summarize(args.name, args.version, entry, manifest, args.full),
                         ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        print(f"pristine cache lookup: {exc}", file=sys.stderr)
        return 2
    return 0 if entry is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
