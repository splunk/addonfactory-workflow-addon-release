#!/usr/bin/env python3
"""List unresolved fields from the verified draft and current reviewed rules."""

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.fossa_json_final import _read_json, build_credits, load_config, verify_provenance


FIELDS = ("license", "copyright_holder", "license_text")


def pending_components(credits):
    if not isinstance(credits, dict) or not isinstance(credits.get("components"), list):
        raise ValueError("Expected a credits object with components")
    pending = []
    for component in credits["components"]:
        if not isinstance(component, dict):
            raise ValueError("Invalid component")
        name, version = component.get("name"), component.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("Invalid component identity")
        fields = [field for field in FIELDS if component.get(field) == "TBD"]
        notices = component.get("notices", [])
        if not isinstance(notices, list):
            raise ValueError("Invalid notices")
        for notice in notices:
            if not isinstance(notice, dict) or not isinstance(notice.get("name"), str):
                raise ValueError("Invalid notice")
            fields.extend(
                f"notice {notice['name']}: {field}"
                for field in (*FIELDS, "notice_text") if notice.get(field) == "TBD"
            )
        if fields:
            pending.append((name, version, fields))
    return pending


def pending_reviews(credits, diagnostics):
    """Include generator blockers that have no TBD field, in component order."""
    fields_by_identity = {
        (name, version): list(fields)
        for name, version, fields in pending_components(credits)
    }
    for component in credits["components"]:
        name, version = component["name"], component["version"]
        prefix = f"Unresolved {name} {version}: "
        for diagnostic in diagnostics:
            if diagnostic.startswith(prefix):
                reason = diagnostic[len(prefix):]
                fields = fields_by_identity.setdefault((name, version), [])
                if reason not in fields and reason.removesuffix(" is TBD") not in fields:
                    fields.append(reason)
    return [
        (component["name"], component["version"],
         fields_by_identity[(component["name"], component["version"])])
        for component in credits["components"]
        if (component["name"], component["version"]) in fields_by_identity
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        _, draft = _read_json(args.draft)
        verify_provenance(draft, args.draft)
        credits, diagnostics, status = build_credits(draft, load_config(args.config))
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
