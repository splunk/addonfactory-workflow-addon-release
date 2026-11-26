#!/usr/bin/env python3
"""Pin pristine-cache.json from the HEAD of a local generator checkout.

Clone or update https://cd.splunkdev.com/hou/license-credits-generator first.
The Git object is read directly, so uncommitted checkout edits are never
recorded under the HEAD commit's provenance.
"""

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
REPOSITORY = "https://cd.splunkdev.com/hou/license-credits-generator"
SOURCE_PATH = "data/pristine-cache.json"


def write_snapshot(raw, commit, destination, retrieved_date):
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or any(not isinstance(v, list) for v in document.values()):
        raise ValueError("pristine cache has an invalid name/version structure")
    packed = gzip.compress(raw, compresslevel=9, mtime=0)
    manifest = {
        "repository": REPOSITORY,
        "commit": commit,
        "source_path": SOURCE_PATH,
        "retrieved_date": retrieved_date,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "compressed_sha256": hashlib.sha256(packed).hexdigest(),
        "names": len(document),
        "entries": sum(len(versions) for versions in document.values()),
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "pristine-cache.json.gz").write_bytes(packed)
    (destination / "pristine-cache-source.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path,
                        help="local license-credits-generator Git checkout")
    parser.add_argument("--destination", type=Path, default=SKILL_DIR / "data")
    args = parser.parse_args(argv)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=args.checkout, stderr=subprocess.PIPE
        ).decode("ascii").strip()
        raw = subprocess.check_output(
            ["git", "show", f"HEAD:{SOURCE_PATH}"],
            cwd=args.checkout, stderr=subprocess.PIPE,
        )
        manifest = write_snapshot(raw, commit, args.destination, date.today().isoformat())
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError,
            subprocess.CalledProcessError) as exc:
        print(f"pristine cache refresh: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
