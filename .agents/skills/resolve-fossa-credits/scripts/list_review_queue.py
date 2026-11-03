#!/usr/bin/env python3
"""List unresolved fields from the verified draft and current reviewed rules."""

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.fossa_json_final import _read_json, build_credits, load_config, verify_provenance
from scripts.fossa_review_queue import pending_components, pending_reviews


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--config", type=Path,
                        help="Defaults to fossa-license-resolutions.json beside the draft")
    args = parser.parse_args(argv)
    try:
        _, draft = _read_json(args.draft)
        draft = verify_provenance(draft, args.draft)
        config_path = args.config or args.draft.parent / "fossa-license-resolutions.json"
        credits, diagnostics, status = build_credits(draft, load_config(config_path))
        pending = pending_reviews(credits, diagnostics)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        print(f"Cannot read review queue: {error}", file=sys.stderr)
        return 2
    for name, version, fields in pending:
        print(f"{name} {version}: {', '.join(fields)}")
    print(f"{len(pending)} components need review; {len(diagnostics)} generator diagnostics",
          file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
