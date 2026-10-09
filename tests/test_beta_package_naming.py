#
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
#
import os
import subprocess
from pathlib import Path
from typing import Optional

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-build-test-release.yml"


def _beta_naming_shell(version: str) -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["build"]["steps"]
    slim_step = next(step for step in steps if step.get("name") == "Slim Package")
    shell = slim_step["run"]
    shell = "BETA_VERSION=" + shell.split("BETA_VERSION=", 1)[1]
    shell = shell.split("chmod -R +r build", 1)[0]
    return shell.replace("${{ steps.BuildVersion.outputs.VERSION }}", version)


def _run_naming(
    tmp_path: Path,
    version: str,
    build: str,
    launcher_version: Optional[str] = None,
    package_version: Optional[str] = None,
):
    source = tmp_path / "output" / "Splunk_TA_example"
    app_conf = source / "default" / "app.conf"
    app_conf.parent.mkdir(parents=True)
    app_conf.write_text(
        f"[install]\nbuild = {build}\n[launcher]\nversion = {launcher_version or version}\n"
    )
    package = tmp_path / f"Splunk_TA_example-{package_version or version}.spl"
    package.write_bytes(b"package content")
    env = os.environ | {"TEST_PACKAGE": str(package), "TEST_SOURCE": str(source)}
    result = subprocess.run(
        [
            "bash",
            "-e",
            "-c",
            'PACKAGE="$TEST_PACKAGE"; INPUT_SOURCE="$TEST_SOURCE"\n'
            + _beta_naming_shell(version)
            + '\nprintf "%s" "$PACKAGE"',
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, package


def test_beta_archive_name_matches_puppet_installed_version(tmp_path: Path):
    result, original = _run_naming(tmp_path, "5.2.1-B1", "1790684208")

    expected = tmp_path / "Splunk_TA_example-5.2.1-B1-1790684208.spl"
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(expected)
    assert expected.read_bytes() == b"package content"
    assert not original.exists()


def test_stable_archive_name_is_unchanged(tmp_path: Path):
    result, package = _run_naming(tmp_path, "5.2.1", "1790684208")

    assert result.returncode == 0, result.stderr
    assert result.stdout == str(package)
    assert package.exists()


@pytest.mark.parametrize(
    ("build", "launcher_version"),
    [("0", None), ("not-a-number", None), ("1790684208", "5.2.0")],
)
def test_invalid_beta_metadata_is_rejected(
    tmp_path: Path, build: str, launcher_version: Optional[str]
):
    result, package = _run_naming(tmp_path, "5.2.1-B1", build, launcher_version)

    assert result.returncode != 0
    assert package.exists()


def test_beta_archive_with_wrong_filename_is_rejected(tmp_path: Path):
    result, package = _run_naming(
        tmp_path, "5.2.1-B1", "1790684208", package_version="5.2.0"
    )

    assert result.returncode != 0
    assert package.exists()
