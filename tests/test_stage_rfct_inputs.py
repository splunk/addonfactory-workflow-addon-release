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
import copy
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ACTION = Path(__file__).parents[1] / ".github/actions/prepare-rfct-inputs"
SPEC = importlib.util.spec_from_file_location("stage_rfct", ACTION / "stage_inputs.py")
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)
CONTROLLER_SPEC = importlib.util.spec_from_file_location(
    "stage_contract_controller", ACTION.parent / "run-rfct" / "run_rfct.py"
)
controller = importlib.util.module_from_spec(CONTROLLER_SPEC)
CONTROLLER_SPEC.loader.exec_module(controller)
IMAGE = "956110764581.dkr.ecr.us-west-2.amazonaws.com/ta-automation/rfct@sha256:" + "a" * 64


def package(path, app="TA_test", version="1.0.0", extras=()):
    with tarfile.open(path, "w:gz") as archive:
        content = f"[launcher]\nversion = {version}\n".encode()
        info = tarfile.TarInfo(f"{app}/default/app.conf")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
        for info, content in extras:
            archive.addfile(info, io.BytesIO(content) if content is not None else None)
    return path


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = package(self.root / "baseline.spl")
        self.candidate = package(self.root / "candidate.spl", version="2.0.0")
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "sample.xml").write_text("<sample>event</sample>")
        (self.source / ".git").mkdir()
        (self.source / ".git" / "config").write_text("private checkout config")
        self.provenance = {
            "run": {"id": "123", "attempt": "1", "event_name": "pull_request"},
            "baseline": {"version": "1.0.0", "commit": "b" * 40},
            "candidate": {"version": "2.0.0", "commit": "c" * 40},
        }
        self.settings = {
            "image": IMAGE, "manifests_ref": "d" * 40,
            "bucket": "ta-staging-artifacts", "repository": "splunk/test-addonfactory-repo",
            "splunk_version": "9.4.2", "sc4s_version": "", "sc4s_registry": "",
        }

    def call(self, **changes):
        values = {"provenance": self.provenance, "baseline": self.base,
                  "candidate": self.candidate, "source": self.source,
                  "directory": self.root / "staged", "settings": self.settings}
        values.update(changes)
        return stage.stage_inputs(**values)

    def test_stages_verified_packages_and_frozen_source(self):
        request = self.call()
        self.assertEqual(request["app_code_name"], "TA_test")
        self.assertEqual(request["candidate"]["version"], "2.0.0")
        self.assertEqual(request["baseline"]["package_sha256"], stage.sha256(self.base))
        self.assertTrue(request["candidate"]["package_url"].endswith("/inputs/candidate.spl"))
        self.assertIn("/123/1/", request["output_prefix"])
        with tarfile.open(self.root / "staged/candidate-source.tar.gz") as archive:
            self.assertEqual(archive.getnames(), ["sample.xml"])

    def test_source_archive_is_reproducible(self):
        first, second = self.root / "one.tgz", self.root / "two.tgz"
        stage.archive_source(self.source, first)
        os.utime(self.source / "sample.xml", (10, 10))
        stage.archive_source(self.source, second)
        self.assertEqual(stage.sha256(first), stage.sha256(second))

    def test_staged_request_satisfies_controller_contract(self):
        self.provenance["run"].update(
            artifact_name="rfct-cim-changes-123-1", workflow_ref="splunk/example/workflow.yml@refs/heads/main"
        )
        request = self.call()
        self.assertIs(controller.validate_request(request), request)
        self.assertEqual(request["candidate"]["source_sha"], self.provenance["candidate"]["commit"])

    def test_source_cannot_archive_itself_or_contain_staging(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            stage.archive_source(self.source, self.source / "source.tgz")
        with self.assertRaisesRegex(ValueError, "outside"):
            self.call(directory=self.source / "staging")

    def test_invalid_controller_metadata_fails_before_staging(self):
        for changes in ({"splunk_version": "latest"}, {"sc4s_version": "latest"},
                        {"sc4s_registry": "image;exec"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.call(settings={**self.settings, **changes})
        for run in ({"attempt": "0"}, {"id": "9" * 40}):
            provenance = copy.deepcopy(self.provenance)
            provenance["run"].update(run)
            with self.subTest(run=run), self.assertRaises(ValueError):
                self.call(provenance=provenance)
        provenance = copy.deepcopy(self.provenance)
        provenance["candidate"]["commit"] = "main"
        with self.assertRaisesRegex(ValueError, "commit SHA"):
            self.call(provenance=provenance)
        self.assertFalse((self.root / "staged").exists())

    def test_requires_digest_and_immutable_manifests(self):
        for name, value in (("image", IMAGE.split("@")[0] + ":latest"),
                            ("manifests_ref", "feature/branch"), ("bucket", "../bucket"),
                            ("repository", "splunk/../bad")):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.call(settings={**self.settings, name: value})

    def test_validates_run_ids(self):
        provenance = copy.deepcopy(self.provenance)
        provenance["run"]["id"] = "123\nspoofed=value"
        with self.assertRaises(ValueError):
            self.call(provenance=provenance)

    def test_rejects_different_app_identities(self):
        package(self.candidate, app="another", version="2.0.0")
        with self.assertRaisesRegex(ValueError, "identities"):
            self.call()

    def test_rejects_package_provenance_version_mismatch(self):
        package(self.candidate, version="3.0.0")
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.call()

    def test_package_directory_requires_exactly_one_package(self):
        with self.assertRaises(ValueError):
            stage.package_file(self.root)
        single = self.root / "single"
        single.mkdir()
        path = package(single / "only.spl")
        self.assertEqual(stage.package_file(single), path)

    def test_rejects_missing_files_and_symlinks(self):
        with self.assertRaises(ValueError):
            stage.regular_file(self.root / "absent")
        linked = self.root / "linked"
        linked.symlink_to(self.base)
        with self.assertRaises(ValueError):
            stage.regular_file(linked)
        (self.source / "symlink").symlink_to(self.base)
        with self.assertRaises(ValueError):
            self.call()
        with self.assertRaises(ValueError):
            stage.archive_source(self.base, self.root / "out.tgz")

    def test_rejects_unsafe_archive_paths_and_types(self):
        for name in ("/absolute", "../escape", "TA_test/../../escape", "."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                stage.member_path(tarfile.TarInfo(name))
        link = tarfile.TarInfo("TA_test/link")
        link.type = tarfile.SYMTYPE
        with self.assertRaises(ValueError):
            stage.member_path(link)

    def test_rejects_multiple_roots_and_duplicate_paths(self):
        for name in ("another/file", "TA_test/default/app.conf"):
            info = tarfile.TarInfo(name)
            package(self.base, extras=[(info, b"")])
            with self.subTest(name=name), self.assertRaises(ValueError):
                stage.inspect_package(self.base)

    def test_rejects_invalid_app_name_or_missing_version(self):
        for app, version in (("bad app", "1.0.0"), ("TA_test", "")):
            package(self.base, app=app, version=version)
            with self.subTest(app=app), self.assertRaises(ValueError):
                stage.inspect_package(self.base)

    def test_input_size_limits(self):
        with mock.patch.object(stage, "MAX_ARCHIVE_BYTES", 1):
            with self.assertRaises(ValueError):
                stage.inspect_package(self.base)
            with self.assertRaises(ValueError):
                stage.archive_source(self.source, self.root / "out.tgz")

    def test_main_writes_request_and_github_outputs(self):
        provenance = self.root / "result.json"
        provenance.write_text(json.dumps(self.provenance))
        outputs = self.root / "outputs"
        env = {f"INPUT_{key.upper()}": value for key, value in self.settings.items()}
        env.update(INPUT_PROVENANCE_PATH=str(provenance), INPUT_BASELINE_PACKAGE=str(self.base),
                   INPUT_CANDIDATE_PACKAGE=str(self.candidate), INPUT_CANDIDATE_SOURCE=str(self.source),
                   INPUT_STAGING_DIRECTORY=str(self.root / "staged"), GITHUB_OUTPUT=str(outputs))
        with mock.patch.dict(os.environ, env, clear=True):
            stage.main()
        self.assertIn("candidate-version=2.0.0", outputs.read_text())
        self.assertIn("request-url=s3://", outputs.read_text())
        self.assertEqual(json.loads((self.root / "request.json").read_text())["schema_version"], 1)

    def test_every_action_input_is_wired(self):
        action = yaml.safe_load((ACTION / "action.yml").read_text())
        wired = "\n".join(action["runs"]["steps"][0]["env"].values())
        for name in action["inputs"]:
            self.assertIn(f"inputs.{name}", wired)
