"""Real offline CLI, config and report integration, without external services."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_credits_validation import archive


class GeneratorModesTests(unittest.TestCase):
    def setUp(self):
        from scripts.fossa_json_draft import build_draft
        from scripts.generate_draft_credits import REQUEST_OPTIONS, attribution_url
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        revision = "a" * 40
        settings = dict(ta_name="Example_TA", display_name="Example TA", fossa_project_locator="custom+1/example")
        dep = dict(title="example", version="1.0", package="example", source="pip",
                   projectUrl="https://example.org", downloadUrl=None, dependencyPaths=["example"],
                   licenses=[dict(name="MIT", attribution="Copyright 2026 Example\nMIT terms")],
                   otherLicenses=[], noticeFiles=[])
        raw = json.dumps(dict(project=dict(name="example", revision=revision), directDependencies=[dep], deepDependencies=[])).encode()
        digest = hashlib.sha256(raw).hexdigest()
        names = ["Example_TA-1.0-fossa-attribution.json", "Example_TA-1.0-fossa-attribution.request.json"]
        metadata = dict(settings, ta_version="1.0", selected_revision=revision, response_revision=revision,
                        raw_sha256=digest, fetched_at="2000-01-01T00:00:00+00:00", request_options=REQUEST_OPTIONS,
                        direct_dependencies=1, deep_dependencies=0, api_url=attribution_url(settings["fossa_project_locator"], revision))
        self.draft = build_draft(json.loads(raw), settings, "1.0", revision, digest, *names)
        (self.root / names[0]).write_bytes(raw)
        (self.root / names[1]).write_text(json.dumps(metadata))
        self.path = self.root / "Example_TA-1.0-credits-draft.json"
        self.path.write_text(json.dumps(self.draft))
        self.config = dict(schema_version=1, resolutions=[], exclusions=[])
        self.rules = self.root / "fossa-license-resolutions.json"
        self.rules.write_text(json.dumps(self.config))
        self.artifact = self.root / "Example_TA-1.0.spl"
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n",
                               "Example_TA/lib/example-1.0.dist-info/METADATA": "Name: example\nVersion: 1.0\n"})

    def set_ecosystem(self, ecosystem):
        raw_path = self.root / self.draft["provenance"]["raw_report_file"]
        raw = json.loads(raw_path.read_text())
        raw["directDependencies"][0]["source"] = ecosystem
        payload = json.dumps(raw).encode()
        digest = hashlib.sha256(payload).hexdigest()
        raw_path.write_bytes(payload)
        request_path = self.root / self.draft["provenance"]["request_metadata_file"]
        request = json.loads(request_path.read_text())
        request["raw_sha256"] = digest
        request_path.write_text(json.dumps(request))
        self.draft["provenance"]["raw_sha256"] = digest
        self.draft["components"][0]["fossa_evidence"]["source"] = ecosystem
        self.path.write_text(json.dumps(self.draft))

    def args(self):
        return ["--draft", str(self.path), "--config", str(self.rules)]

    def test_validate_checks_real_inputs_and_writes_nothing(self):
        from scripts.generate_credits import main
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()}
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(["validate", *self.args(), "--artifact", str(self.artifact), "--json"]), 0)
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["scope"]["decisions"][0]["disposition"], "present")
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()})

    def test_unidentifiable_entry_is_retained_without_a_scope_blocker(self):
        self.set_ecosystem("npm")
        from scripts.generate_credits import main
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n",
                               "Example_TA/main.js": "bundled opaque content"})
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["generate", *self.args(), "--artifact", str(self.artifact),
                                   "--output-dir", str(self.root / "output"), "--json"]), 0)
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["scope"]["decisions"][0]["disposition"], "unverified")
        credits = json.loads((self.root / "output/Example_TA-1.0-credits.json").read_text())
        self.assertEqual(credits["components"][0]["name"], "example")

    def test_npm_version_provenance_and_extra_attribution_do_not_block_presence(self):
        self.set_ecosystem("npm")
        from scripts.generate_credits import main
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n",
                               "Example_TA/licenses.txt": "Name: example\nVersion: 2.0\n\nName: other\nVersion: 4.0\n"})
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["validate", *self.args(), "--artifact", str(self.artifact), "--json"]), 0)
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["scope"]["decisions"][0]["disposition"], "present")
        self.assertEqual(receipt["scope"]["decisions"][0]["version"], "1.0")
        self.assertFalse(any("SCOPE_MISSING_COMPONENT" in d or "SCOPE_ATTRIBUTION_CANDIDATE" in d
                             for d in receipt["diagnostics"]))

    def test_missing_python_version_is_omitted_without_legal_or_scope_blocker(self):
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        archive(self.artifact, {"Example_TA/main.py": "pass"})
        self.draft["components"][0]["license"] = "TBD"
        self.config["exclusions"] = [dict(name="example", version="1.0", reason="Legacy release review")]
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inspect_artifact(self.artifact))
        self.assertEqual((credits["components"], status), ([], 0), diagnostics)
        self.assertTrue(any(d.startswith("Excluded example 1.0:") for d in diagnostics))

    def test_explicit_python_additions_follow_metadata_membership(self):
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        from test_credits_validation import component
        self.config["additions"] = [dict(component(name="extra", version=v), ecosystem="pip",
                                               reason="Reviewed upstream", source="exact source")
                                    for v in ("1.0", "2.0", "3.0")]
        archive(self.artifact, {"Example_TA/lib/example-1.0.dist-info/METADATA": "Name: example\nVersion: 1.0\n",
                               "Example_TA/lib/extra-1.0.dist-info/METADATA": "Name: extra\nVersion: 1.0\n",
                               "Example_TA/platform/extra-2.0.dist-info/METADATA": "Name: extra\nVersion: 2.0\n"})
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inspect_artifact(self.artifact))
        self.assertEqual(status, 0, diagnostics)
        self.assertEqual([(c["name"], c["version"]) for c in credits["components"]],
                         [("example", "1.0"), ("extra", "1.0"), ("extra", "2.0")])
        self.assertTrue(all("ecosystem" not in c for c in credits["components"]))

    def test_retained_python_row_ignores_old_exclusion_and_receipt_agrees(self):
        from scripts.generate_credits import main
        self.config["exclusions"] = [dict(name="example", version="1.0", reason="Legacy release review")]
        self.rules.write_text(json.dumps(self.config))
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            status = main(["generate", *self.args(), "--artifact", str(self.artifact),
                           "--output-dir", str(self.root / "output"), "--json"])
        receipt = json.loads(stdout.getvalue())
        credits = json.loads((self.root / "output/Example_TA-1.0-credits.json").read_text())
        self.assertEqual(status, 0, receipt["diagnostics"])
        self.assertEqual(credits["components"][0]["name"], "example")
        self.assertEqual(receipt["scope"]["decisions"][0]["disposition"], "present")
        self.assertEqual(receipt["scope"]["decisions"][0]["policy"], "python-dist-info")

    def test_metadata_only_python_versions_get_review_entries_and_receipts(self):
        from scripts.generate_credits import main
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n",
                               "Example_TA/lib/example-2.0.dist-info/METADATA": "Name: example\nVersion: 2.0\n",
                               "Example_TA/platform/example-2.0.dist-info/METADATA": "Name: example\nVersion: 2.0\n"})
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            status = main(["generate", *self.args(), "--artifact", str(self.artifact),
                           "--output-dir", str(self.root / "output"), "--json"])
        credits = json.loads((self.root / "output/Example_TA-1.0-credits.json").read_text())
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual([(c["name"], c["version"]) for c in credits["components"]], [("example", "2.0")])
        self.assertEqual(credits["components"][0]["license"], "TBD")
        self.assertEqual([(c["version"], c["disposition"]) for c in receipt["scope"]["decisions"]],
                         [("1.0", "not_shipped"), ("2.0", "present")])
        self.assertEqual(len(receipt["scope"]["decisions"][1]["evidence"]), 2)

    def test_metadata_only_version_reuses_only_its_exact_review(self):
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        self.config["resolutions"] = [dict(name="example", version="2.0", reason="Exact upstream reviewed",
                                            source="source", license="MIT", license_text="exact 2.0 MIT terms",
                                            copyright_holder="Copyright 2026 Exact 2.0", project_url="https://example.org/2")]
        archive(self.artifact, {"Example_TA/lib/example-2.0.dist-info/METADATA": "Name: example\nVersion: 2.0\n"})
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inspect_artifact(self.artifact))
        self.assertEqual(status, 0, diagnostics)
        self.assertEqual(credits["components"][0]["version"], "2.0")
        self.assertEqual(credits["components"][0]["license_text"], "exact 2.0 MIT terms")

    def test_matching_python_distribution_is_not_added_again_under_normalized_name(self):
        from scripts.generate_credits import main
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n",
                               "Example_TA/lib/example-1.0.dist-info/METADATA": "Name: EXAMPLE\nVersion: 1.0\n"})
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            status = main(["generate", *self.args(), "--artifact", str(self.artifact),
                           "--output-dir", str(self.root / "output"), "--json"])
        credits = json.loads((self.root / "output/Example_TA-1.0-credits.json").read_text())
        self.assertEqual(status, 0)
        self.assertEqual(len(credits["components"]), 1)
        self.assertEqual(len(json.loads(stdout.getvalue())["scope"]["decisions"]), 1)

    def test_ecosystem_only_rule_can_classify_git_sourced_python(self):
        from scripts.generate_credits import main
        self.set_ecosystem("git")
        self.config["resolutions"] = [dict(name="example", version="1.0", ecosystem="pip",
                                            reason="Python distribution", source="exact artifact metadata")]
        self.rules.write_text(json.dumps(self.config))
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            status = main(["validate", *self.args(), "--artifact", str(self.artifact), "--json"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["scope"]["decisions"][0]["policy"], "python-dist-info")

    def test_metadata_only_alias_is_shared_by_report_and_receipt(self):
        from scripts.generate_credits import main
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n",
                               "Example_TA/lib/extra-1.0.dist-info/METADATA": "Name: extra\nVersion: 1.0\n"})
        self.config["resolutions"] = [dict(name="extra", version="1.0", public_name="public-extra",
                                            reason="Exact review", source="source", license="MIT",
                                            copyright_holder="Copyright 2026 Example", license_text="MIT terms",
                                            project_url="https://example.org")]
        self.rules.write_text(json.dumps(self.config))
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            status = main(["generate", *self.args(), "--artifact", str(self.artifact),
                           "--output-dir", str(self.root / "output"), "--json"])
        credits = json.loads((self.root / "output/Example_TA-1.0-credits.json").read_text())
        self.assertEqual(status, 0)
        self.assertEqual(credits["components"][0]["name"], "public-extra")
        self.assertEqual(json.loads(stdout.getvalue())["scope"]["decisions"][-1]["disposition"], "present")

    def test_metadata_only_python_row_cannot_reuse_explicit_npm_resolution(self):
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        self.draft["components"] = []
        self.config["resolutions"] = [dict(name="example", version="1.0", ecosystem="npm",
                                            reason="npm review", source="npm source", license="MIT",
                                            license_text="NPM ONLY TERMS", copyright_holder="Copyright npm",
                                            project_url="https://example.org")]
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inspect_artifact(self.artifact))
        self.assertEqual(status, 1, diagnostics)
        self.assertEqual(credits["components"][0]["license_text"], "TBD")

    def test_python_npm_identity_collision_remains_inspectable_with_separate_facts(self):
        from scripts.generate_credits import main
        self.set_ecosystem("npm")
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            status = main(["generate", *self.args(), "--artifact", str(self.artifact),
                           "--output-dir", str(self.root / "output"), "--json"])
        self.assertEqual(status, 1)
        credits = json.loads((self.root / "output/Example_TA-1.0-credits.json").read_text())
        self.assertEqual(len(credits["components"]), 2)
        self.assertEqual(credits["components"][0]["license_text"], "Copyright 2026 Example\nMIT terms")
        self.assertEqual(credits["components"][1]["license_text"], "TBD")
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["scope"]["decisions"][0]["disposition"], "unverified")
        self.assertEqual(receipt["scope"]["decisions"][1]["policy"], "python-dist-info")
        self.assertTrue(any("PYTHON_IDENTITY_COLLISION" in d for d in receipt["diagnostics"]))

    def test_python_specific_alias_resolves_cross_ecosystem_collision(self):
        from scripts.generate_credits import main
        self.set_ecosystem("npm")
        self.config["resolutions"] = [dict(name="example", version="1.0", ecosystem="pip",
                                            public_name="python-example", reason="Python review", source="python source",
                                            license="MIT", license_text="PYTHON ONLY TERMS", copyright_holder="Copyright Python",
                                            project_url="https://example.org/python")]
        self.rules.write_text(json.dumps(self.config))
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            status = main(["generate", *self.args(), "--artifact", str(self.artifact),
                           "--output-dir", str(self.root / "output"), "--json"])
        credits = json.loads((self.root / "output/Example_TA-1.0-credits.json").read_text())
        self.assertEqual(status, 0)
        self.assertEqual([(c["name"], c["license_text"]) for c in credits["components"]],
                         [("example", "Copyright 2026 Example\nMIT terms"), ("python-example", "PYTHON ONLY TERMS")])
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["scope"]["decisions"][0]["disposition"], "unverified")
        self.assertEqual(receipt["scope"]["decisions"][1]["disposition"], "present")

    def test_explicit_generate_and_legacy_are_identical(self):
        from scripts.generate_credits import main
        one, two = self.root / "one", self.root / "two"
        self.assertEqual(main([*self.args(), "--output-dir", str(one)]), 0)
        self.assertEqual(main(["generate", *self.args(), "--output-dir", str(two)]), 0)
        self.assertEqual(next(one.iterdir()).read_bytes(), next(two.iterdir()).read_bytes())

    def test_validate_rejects_output_directory_without_creating_it(self):
        from scripts.generate_credits import main
        output = self.root / "never"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["validate", *self.args(), "--output-dir", str(output)]), 2)
        self.assertFalse(output.exists())

    def test_scope_override_and_public_identity_are_private(self):
        from scripts.fossa_json_final import build_credits, load_config
        scope = dict(disposition="present", artifact_sha256=hashlib.sha256(self.artifact.read_bytes()).hexdigest(), reason="Exact files reviewed", references=["artifact evidence"])
        rule = dict(name="example", version="1.0", reason="Reviewed identity", source="exact metadata",
                    public_name="public-example", project_url="https://example.org/pkg", scope=scope)
        self.config["resolutions"] = [rule]
        self.rules.write_text(json.dumps(self.config))
        loaded = load_config(self.rules)
        credits, _, status = build_credits(self.draft, loaded)
        self.assertEqual(status, 0)
        self.assertEqual(credits["components"][0]["name"], "public-example")
        self.assertNotIn("scope", credits["components"][0])

    def test_public_alias_does_not_lose_original_artifact_identity(self):
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        self.config["resolutions"] = [dict(name="example", version="1.0", reason="Public spelling",
                                            source="metadata", public_name="public-example")]
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inspect_artifact(self.artifact))
        self.assertEqual(status, 0, diagnostics)
        self.assertEqual(credits["components"][0]["name"], "public-example")

    def test_malformed_artifact_app_conf_returns_two(self):
        from scripts.generate_credits import main
        archive(self.artifact, {"Example_TA/default/app.conf": "not an INI section"})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["validate", *self.args(), "--artifact", str(self.artifact)]), 2)

    def test_alias_cannot_bypass_mapped_presence_contradiction(self):
        self.set_ecosystem("npm")
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        sm = dict(version=3, sources=["../node_modules/example/index.js"], sourcesContent=["export default 1"], names=[], mappings="AAAA")
        archive(self.artifact, {"Example_TA/main.js": "console.log(1)", "Example_TA/main.js.map": json.dumps(sm)})
        inv = inspect_artifact(self.artifact)
        scope = dict(disposition="not_shipped", artifact_sha256=inv["sha256"], reason="old review", references=["ref"])
        self.config["resolutions"] = [dict(name="example", version="1.0", reason="alias", source="metadata",
                                            public_name="public-example", scope=scope)]
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inv)
        self.assertEqual((len(credits["components"]), status), (1, 1))
        self.assertTrue(any("contradict" in d for d in diagnostics))

    def test_status_one_generates_but_validate_preserves_every_file(self):
        from scripts.generate_credits import main
        self.config["resolutions"] = [dict(name="example", version="1.0", reason="Reviewed absence", source="source", copyright_holder="")]
        self.rules.write_text(json.dumps(self.config))
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()}
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["validate", *self.args(), "--artifact", str(self.artifact)]), 1)
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()})
            output = self.root / "generated"
            self.assertEqual(main(["generate", *self.args(), "--artifact", str(self.artifact), "--output-dir", str(output)]), 1)
        result = json.loads((output / "Example_TA-1.0-credits.json").read_text())
        self.assertEqual(result["components"][0]["copyright_holder"], "")

    def test_stale_exclusion_cannot_silently_remove_a_component(self):
        self.set_ecosystem("npm")
        archive(self.artifact, {"Example_TA/main.js": "opaque bundle"})
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        self.config["exclusions"] = [dict(name="example", version="1.0", reason="Old exclusion")]
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inspect_artifact(self.artifact))
        self.assertEqual(len(credits["components"]), 1)
        self.assertEqual(status, 1)
        self.assertTrue(any("SCOPE_EXCLUSION" in d for d in diagnostics))

    def test_reviewed_not_shipped_is_applied_only_for_matching_artifact(self):
        self.set_ecosystem("npm")
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n"})
        inv = inspect_artifact(self.artifact)
        scope = dict(disposition="not_shipped", artifact_sha256=inv["sha256"], reason="Exact artifact reviewed", references=["ref"])
        self.config["resolutions"] = [dict(name="example", version="1.0", reason="scope", source="artifact", scope=scope)]
        credits, _, status = build_credits(self.draft, self.config, artifact=inv)
        self.assertEqual((len(credits["components"]), status), (0, 0))
        scope["artifact_sha256"] = "0" * 64
        credits, _, status = build_credits(self.draft, self.config, artifact=inv)
        self.assertEqual((len(credits["components"]), status), (1, 1))


if __name__ == "__main__":
    unittest.main()
