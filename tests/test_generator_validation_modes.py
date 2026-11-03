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

    def test_version_provenance_and_reverse_inventory_do_not_block_presence(self):
        from scripts.generate_credits import main
        archive(self.artifact, {"Example_TA/default/app.conf": "[id]\nversion=1.0\n",
                               "Example_TA/lib/example-2.0.dist-info/METADATA": "Name: example\nVersion: 2.0\n",
                               "Example_TA/lib/extra-3.0.dist-info/METADATA": "Name: extra\nVersion: 3.0\n",
                               "Example_TA/licenses.txt": "Name: other\nVersion: 4.0\n"})
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["validate", *self.args(), "--artifact", str(self.artifact), "--json"]), 0)
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["scope"]["decisions"][0]["disposition"], "present")
        self.assertEqual(receipt["scope"]["decisions"][0]["version"], "1.0")
        self.assertFalse(any("SCOPE_MISSING_COMPONENT" in d or "SCOPE_ATTRIBUTION_CANDIDATE" in d
                             for d in receipt["diagnostics"]))

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
        from scripts.fossa_json_final import build_credits
        from scripts.fossa_artifact_scope import inspect_artifact
        self.config["exclusions"] = [dict(name="example", version="1.0", reason="Old exclusion")]
        credits, diagnostics, status = build_credits(self.draft, self.config, artifact=inspect_artifact(self.artifact))
        self.assertEqual(len(credits["components"]), 1)
        self.assertEqual(status, 1)
        self.assertTrue(any("SCOPE_EXCLUSION" in d for d in diagnostics))

    def test_reviewed_not_shipped_is_applied_only_for_matching_artifact(self):
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
