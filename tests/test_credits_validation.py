"""Publication and artifact scope regressions; runnable with stdlib unittest."""
import copy
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


def component(**fields):
    value = dict(name="example", version="1.0", license="MIT",
                 copyright_holder="Copyright 2026 Example", license_text="MIT terms",
                 project_url="https://example.org", notices=[])
    value.update(fields)
    return value


def report(*components):
    return dict(name="Example", version="1.0", disclaimer="Example disclaimer",
                components=list(components or [component()]))


def archive(path, entries):
    with tarfile.open(path, "w:gz") as stream:
        for name, content in entries.items():
            raw = content.encode() if isinstance(content, str) else content
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            stream.addfile(member, io.BytesIO(raw))


class PublicationTests(unittest.TestCase):
    def codes(self, value):
        from scripts.fossa_credits_validation import validate_public_credits
        return [f.code for f in validate_public_credits(value)]

    def test_valid_single_spdx_report_passes(self):
        self.assertEqual(self.codes(report()), [])

    def test_empty_holders_and_transport_names_are_findings(self):
        value = report(component(name="ssh://git@github.com/example/pkg.git",
                                 copyright_holder="", project_url=None))
        self.assertEqual(set(self.codes(value)), {"PUBLIC_NAME", "PROJECT_URL", "COPYRIGHT_EMPTY"})

    def test_unknown_and_composite_licenses_are_rejected(self):
        for license_id in ["Not-A-License", "MIT OR Apache-2.0", "MIT AND BSD-3-Clause"]:
            with self.subTest(license_id=license_id):
                self.assertIn("SPDX_IDENTIFIER", self.codes(report(component(license=license_id))))

    def test_nested_notice_is_checked(self):
        notice = dict(name="helper", license="wrong", copyright_holder="", license_text="https://example.org/LICENSE")
        codes = self.codes(report(component(notices=[notice])))
        self.assertEqual(set(codes), {"SPDX_IDENTIFIER", "COPYRIGHT_EMPTY", "LICENSE_URL_ONLY"})

    def test_apache_body_without_appendix_passes_but_changed_terms_fail(self):
        path = Path(__file__).resolve().parents[1] / "scripts/data/credits-validation/Apache-2.0.txt"
        body = path.read_text().split("END OF TERMS AND CONDITIONS")[0].strip()
        self.assertEqual(self.codes(report(component(license="Apache-2.0", license_text=body))), [])
        wrapped = body.replace("\n", "  \n\t")
        self.assertEqual(self.codes(report(component(license="Apache-2.0", license_text=wrapped))), [])
        changed = body.replace("perpetual", "temporary", 1)
        self.assertIn("APACHE_TERMS", self.codes(report(component(license="Apache-2.0", license_text=changed))))

    def test_appendix_cleanup_is_not_missing_copyright(self):
        path = Path(__file__).resolve().parents[1] / "scripts/data/credits-validation/Apache-2.0.txt"
        self.assertEqual(self.codes(report(component(license="Apache-2.0", license_text=path.read_text()))),
                         ["APACHE_APPENDIX"])

    def test_versions_warn_but_exact_duplicates_fail(self):
        from scripts.fossa_credits_validation import validate_public_credits
        findings = validate_public_credits(report(component(), component(version="2.0")))
        self.assertEqual([(f.code, f.severity) for f in findings], [("VERSION_DISCLOSURE", "warning")])
        self.assertIn("DUPLICATE_COMPONENT", self.codes(report(component(), component())))

    def test_malformed_shapes_produce_findings_without_crashing(self):
        for value in [None, {}, report(component(notices=[None])), report(component(version=[]))]:
            with self.subTest(value=value):
                self.assertIn("PUBLIC_SCHEMA", self.codes(value))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "example.spl"

    def inspect(self, entries):
        from scripts.fossa_artifact_scope import inspect_artifact
        archive(self.path, entries)
        return inspect_artifact(self.path)

    def test_python_metadata_confirms_presence_only_for_draft_entries(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        inv = self.inspect({"Example/lib/pkg-1.0.dist-info/METADATA": "Name: pkg\nVersion: 1.0\n"})
        result = reconcile_scope([component(name="pkg")], inv)
        self.assertEqual(result["decisions"][0]["disposition"], "present")
        self.assertNotIn("missing_from_draft", result)

    def test_unidentifiable_compiler_and_runtime_are_retained_without_scope_review(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        inv = self.inspect({"Example/main.js": "console.log(1)"})
        result = reconcile_scope([component(name="@babel/parser"), component(name="@babel/runtime")], inv)
        self.assertEqual([d["disposition"] for d in result["decisions"]], ["unverified", "unverified"])

    def test_mapped_package_confirms_presence_without_a_version_check(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        sm = dict(version=3, sources=["../node_modules/@babel/runtime/helpers/x.js"],
                  sourcesContent=["export default 1"], names=[], mappings="AAAA")
        inv = self.inspect({"Example/main.js": "console.log(1)", "Example/main.js.map": json.dumps(sm)})
        result = reconcile_scope([component(name="@babel/runtime")], inv)
        decision = result["decisions"][0]
        self.assertEqual(decision["disposition"], "present")
        self.assertTrue(decision["evidence"])

    def test_unmapped_and_dangling_sources_do_not_confirm_presence(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        sm = dict(version=3, sources=["../node_modules/pkg/x.js"], sourcesContent=["x"], names=[], mappings="")
        inv = self.inspect({"Example/main.js.map": json.dumps(sm)})
        self.assertEqual(inv["observations"], [])
        self.assertEqual(reconcile_scope([component(name="pkg")], inv)["decisions"][0]["disposition"], "unverified")

    def test_packaged_attribution_satisfies_presence_without_proving_code_provenance(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        inv = self.inspect({"Example/licenses.txt": "Name: pkg\nVersion: 2.0\nLicense: MIT\n"})
        result = reconcile_scope([component(name="pkg", version="2.0")], inv)
        self.assertEqual(result["decisions"][0]["disposition"], "present")
        self.assertEqual(result["decisions"][0]["evidence"][0]["kind"], "attribution")

    def test_different_metadata_version_does_not_block_package_presence(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        inv = self.inspect({"Example/lib/pkg-2.0.dist-info/METADATA": "Name: pkg\nVersion: 2.0\n"})
        decision = reconcile_scope([component(name="pkg", version="1.0")], inv)["decisions"][0]
        self.assertEqual(decision["disposition"], "present")
        self.assertEqual(decision["version"], "1.0")
        self.assertEqual(decision["evidence"][0]["version"], "2.0")

    def test_reviewed_absence_is_bound_to_exact_artifact(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        inv = self.inspect({"Example/main.js": "console.log(1)"})
        scope = dict(disposition="not_shipped", artifact_sha256=inv["sha256"],
                     reason="Reviewed all build entry points", references=["exact build evidence"])
        reviews = {("example", "1.0"): scope}
        self.assertEqual(reconcile_scope([component()], inv, reviews)["decisions"][0]["disposition"], "not_shipped")
        scope["artifact_sha256"] = "0" * 64
        self.assertEqual(reconcile_scope([component()], inv, reviews)["decisions"][0]["disposition"], "unknown")

    def test_review_cannot_hide_positive_metadata(self):
        from scripts.fossa_artifact_scope import reconcile_scope
        inv = self.inspect({"Example/lib/pkg-1.0.dist-info/METADATA": "Name: pkg\nVersion: 1.0\n"})
        review = dict(disposition="not_shipped", artifact_sha256=inv["sha256"], reason="old review", references=["ref"])
        decision = reconcile_scope([component(name="pkg")], inv, {("pkg", "1.0"): review})["decisions"][0]
        self.assertEqual(decision["disposition"], "unknown")
        self.assertIn("contradict", decision["reason"])

    def test_unsafe_archive_rejected_and_release_version_checked(self):
        from scripts.fossa_artifact_scope import inspect_artifact
        archive(self.path, {"../escape": "data"})
        with self.assertRaises(ValueError):
            inspect_artifact(self.path)
        archive(self.path, {"Example/default/app.conf": "[id]\nversion=2.0\n"})
        with self.assertRaises(ValueError):
            inspect_artifact(self.path, expected_version="1.0", expected_app="Example")


if __name__ == "__main__":
    unittest.main()
