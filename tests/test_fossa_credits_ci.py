import json
from argparse import Namespace
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from scripts import ci_generate_fossa_credits as ci_credits
from scripts.fossa_json_final import build_credits, load_config, verify_provenance
from scripts.fossa_json_common import attribution_url
from scripts.generate_draft_credits import write_draft_artifacts
from scripts import publish_fossa_credits as publish_credits


class ScanIdentityTests(unittest.TestCase):
    def test_accepts_metadata_for_checked_out_revision(self):
        revision = "a" * 40
        locator = "custom+123/example-ta"
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path = Path(temporary) / "scan.json"
            metadata_path.write_text(json.dumps({
                "id": f"{locator}${revision}",
                "projectId": locator,
                "revision": revision,
                "url": "https://app.fossa.com/projects/example",
            }), encoding="utf-8")
            with patch.object(ci_credits.subprocess, "run") as run:
                run.return_value.stdout = revision + "\n"
                metadata, found_revision, found_locator = ci_credits._scan_identity(
                    metadata_path, Path(temporary)
                )

        self.assertEqual(found_revision, revision)
        self.assertEqual(found_locator, locator)
        self.assertEqual(metadata["url"], "https://app.fossa.com/projects/example")

    def test_rejects_locator_revision_identity_mismatch(self):
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path = Path(temporary) / "scan.json"
            metadata_path.write_text(json.dumps({
                "id": "custom+123/example-ta$" + "b" * 40,
                "projectId": "custom+123/example-ta",
                "revision": revision,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity is inconsistent"):
                ci_credits._scan_identity(metadata_path, Path(temporary))

    def test_rejects_checkout_revision_mismatch(self):
        revision = "a" * 40
        locator = "custom+123/example-ta"
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path = Path(temporary) / "scan.json"
            metadata_path.write_text(json.dumps({
                "id": f"{locator}${revision}",
                "projectId": locator,
                "revision": revision,
            }), encoding="utf-8")
            with patch.object(ci_credits.subprocess, "run") as run:
                run.return_value.stdout = "b" * 40
                with self.assertRaisesRegex(ValueError, "does not match"):
                    ci_credits._scan_identity(metadata_path, Path(temporary))


class CreditsGenerationTests(unittest.TestCase):
    def test_draft_provenance_binds_raw_report_to_exact_revision(self):
        revision = "a" * 40
        locator = "custom+123/example-ta"
        settings = {
            "schema_version": 1,
            "ta_name": "Example_TA",
            "display_name": "Example Add-on",
            "fossa_project_locator": locator,
        }
        report = {
            "project": {"name": "example-ta", "revision": revision},
            "directDependencies": [{
                "title": "example-library",
                "version": "1.2.3",
                "package": "example-library",
                "source": "pip",
                "downloadUrl": None,
                "projectUrl": "https://example.org/example-library",
                "dependencyPaths": ["example-library"],
                "licenses": [{"name": "MIT", "attribution": "MIT License"}],
                "otherLicenses": [],
                "noticeFiles": [],
            }],
            "deepDependencies": [],
        }
        raw = json.dumps(report).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            _, _, draft_path = write_draft_artifacts(
                raw, settings, "1.2.3", revision, output,
                attribution_url(locator, revision), "2026-11-25T00:00:00+00:00",
            )
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            canonical = verify_provenance(draft, draft_path)
            config_path = output / "fossa-license-resolutions.json"
            config_path.write_text(json.dumps({
                "schema_version": 1, "resolutions": [], "exclusions": [],
            }), encoding="utf-8")
            _, diagnostics, status = build_credits(canonical, load_config(config_path))

        self.assertEqual(draft["provenance"]["revision"], revision)
        self.assertIn("example-library", [item["name"] for item in canonical["components"]])
        self.assertEqual(status, 1)
        self.assertTrue(any(item.startswith("Unresolved example-library 1.2.3:") for item in diagnostics))

    def test_rejects_scope_changes_outside_ci_contract(self):
        with self.assertRaisesRegex(ValueError, "package inventory comparison"):
            ci_credits._validate_ci_config({
                "resolutions": [],
                "exclusions": [{"name": "not-shipped", "version": "1.0", "reason": "example"}],
                "additions": [],
            })

    def test_unresolved_result_keeps_review_artifacts(self):
        revision = "a" * 40
        locator = "custom+123/example-ta"
        canonical = {"name": "Example Add-on", "version": "1.2.3"}
        credits = {
            "name": "Example Add-on",
            "version": "1.2.3",
            "disclaimer": "Disclaimer",
            "components": [{"name": "sample", "version": "4.5.6"}],
        }
        diagnostics = ["Unresolved sample 4.5.6: license is TBD"]
        args = Namespace(
            output_dir=None,
            scan_metadata=Path("scan.json"),
            ta_repo=Path("ta"),
            ta_version="1.2.3",
            resolutions=Path("resolutions.json"),
            fossa_api_key="secret",
        )

        with tempfile.TemporaryDirectory() as temporary:
            args.output_dir = Path(temporary)
            with ExitStack() as stack:
                stack.enter_context(patch.object(ci_credits, "_scan_identity", return_value=({}, revision, locator)))
                stack.enter_context(patch.object(ci_credits, "load_settings", return_value={
                    "schema_version": 1,
                    "ta_name": "Example_TA",
                    "display_name": "Example Add-on",
                }))
                stack.enter_context(patch.object(ci_credits, "fetch_attribution", return_value=b"{}"))
                stack.enter_context(patch.object(ci_credits, "write_draft_artifacts", return_value=(Path("draft.json"), None, None)))
                stack.enter_context(patch.object(ci_credits, "_read_json", return_value=(b"{}", {})))
                stack.enter_context(patch.object(ci_credits, "verify_provenance", return_value=canonical))
                stack.enter_context(patch.object(ci_credits, "load_config", return_value={"schema_version": 1}))
                stack.enter_context(patch.object(ci_credits, "build_credits", return_value=(credits, diagnostics, 1)))
                stack.enter_context(patch.object(ci_credits, "pending_reviews", return_value=[
                    ("sample", "4.5.6", ["license"]),
                ]))
                status = ci_credits.generate(args)

            self.assertEqual(status, 1)
            self.assertTrue((args.output_dir / "credits-review-queue.txt").is_file())
            self.assertTrue((args.output_dir / "credits-diagnostics.txt").is_file())
            self.assertTrue((args.output_dir / "credits-summary.json").is_file())
            self.assertTrue((args.output_dir / "Example_TA-1.2.3-credits.json").is_file())
            summary = json.loads((args.output_dir / "credits-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "unresolved")
            self.assertEqual(summary["scan_revision"], revision)


class ReleaseDocsTests(unittest.TestCase):
    def _fixture(self, root: Path, generated: bytes = b'{"components": []}\n'):
        repo = root / "repo"
        artifacts = root / "artifacts"
        (repo / "docs/ReleaseNotes").mkdir(parents=True)
        artifacts.mkdir()
        (repo / ".fossa-credits.json").write_text(json.dumps({
            "schema_version": 1,
            "ta_name": "Example_TA",
            "display_name": "Example Add-on",
            "docs_index": "docs/ReleaseNotes/index.md",
        }), encoding="utf-8")
        index = repo / "docs/ReleaseNotes/index.md"
        index.write_text(
            "# Release notes\n\n## Third-party software attributions\n\n"
            "A complete listing is available as a PDF file for download:\n"
            "[Credits](../thirdpartycredits/Example%20old.pdf)\n",
            encoding="utf-8",
        )
        (artifacts / "credits-summary.json").write_text(json.dumps({
            "status": "complete",
            "scan_revision": "a" * 40,
            "ta_version": "1.2.3",
        }), encoding="utf-8")
        (artifacts / "Example_TA-1.2.3-credits.json").write_bytes(generated)
        return repo, artifacts, index

    def test_prepares_release_file_and_updates_link_idempotently(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, artifacts, index = self._fixture(Path(temporary))
            args = Namespace(
                version="1.2.3",
                expected_revision="a" * 40,
                ta_repo=repo,
                artifacts=artifacts,
            )
            publish_credits.prepare(args)
            destination = repo / "docs/thirdpartycredits/Example_TA-1.2.3-credits.json"
            self.assertEqual(destination.read_bytes(), b'{"components": []}\n')
            content = index.read_text(encoding="utf-8")
            self.assertIn("Example_TA-1.2.3-credits.json", content)
            self.assertNotIn("Example%20old.pdf", content)
            self.assertIn("available for download:", content)

            publish_credits.prepare(args)
            self.assertEqual(index.read_text(encoding="utf-8"), content)

    def test_refuses_different_same_version_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, artifacts, _ = self._fixture(Path(temporary))
            destination = repo / "docs/thirdpartycredits/Example_TA-1.2.3-credits.json"
            destination.parent.mkdir(parents=True)
            destination.write_text("{}\n", encoding="utf-8")
            args = Namespace(
                version="1.2.3",
                expected_revision="a" * 40,
                ta_repo=repo,
                artifacts=artifacts,
            )
            with self.assertRaisesRegex(ValueError, "already exists"):
                publish_credits.prepare(args)

    def test_uses_configured_section_heading_and_escapes_link_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, artifacts, index = self._fixture(Path(temporary))
            settings_path = repo / ".fossa-credits.json"
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            settings["display_name"] = "Example [Add-on]"
            settings["docs_section_heading"] = "Open source acknowledgements"
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            index.write_text(
                "# Release notes\n\n## Open source acknowledgements\n\nDetails.\n",
                encoding="utf-8",
            )
            args = Namespace(
                version="1.2.3",
                expected_revision="a" * 40,
                ta_repo=repo,
                artifacts=artifacts,
            )

            publish_credits.prepare(args)

            content = index.read_text(encoding="utf-8")
            self.assertIn(r"[Example \[Add-on\] third-party software credits (1.2.3)]", content)
            self.assertIn("## Open source acknowledgements", content)

    def test_rejects_non_object_release_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo, artifacts, _ = self._fixture(Path(temporary))
            (artifacts / "credits-summary.json").write_text("[]", encoding="utf-8")
            args = Namespace(
                version="1.2.3",
                expected_revision="a" * 40,
                ta_repo=repo,
                artifacts=artifacts,
            )
            with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                publish_credits.prepare(args)


if __name__ == "__main__":
    unittest.main()
