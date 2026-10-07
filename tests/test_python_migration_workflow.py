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

import pytest

from scripts.check_workflow_hygiene import load_workflow


WORKFLOW = load_workflow(Path(".github/workflows/reusable-build-test-release.yml"))
JOBS = WORKFLOW["jobs"]
REPORTS = [
    (name, job) for name, job in JOBS.items()
    if any(step.get("name") == "Combine summaries into a table" for step in job.get("steps", []))
]


def run_shell(script, directory, **environment):
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script], cwd=directory,
        env={**os.environ, **environment}, text=True, capture_output=True,
    )


@pytest.mark.parametrize("version", ["3.9", "3.13"])
def test_python_guard_accepts_supported_versions(tmp_path, version):
    script = JOBS["setup-workflow"]["steps"][0]["run"]
    result = run_shell(script, tmp_path, PYTHON_VERSION=version)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("version", ["", "3.10", "3.13.5", "3.9 ", "$(touch injected)", "3.13; touch injected"])
def test_python_guard_rejects_unsupported_values_without_execution(tmp_path, version):
    script = JOBS["setup-workflow"]["steps"][0]["run"]
    result = run_shell(script, tmp_path, PYTHON_VERSION=version)
    assert result.returncode != 0
    assert "::error::Supported Python versions" in result.stdout
    assert not (tmp_path / "injected").exists()


def ancestors(job_name):
    needs = JOBS[job_name].get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    return set(needs).union(*(ancestors(name) for name in needs))


def test_selected_runtime_consumers_wait_for_validation():
    # Any job that executes with the selected runtime must depend on the guard.
    consumers = [name for name, job in JOBS.items() if "env.PYTHON_VERSION" in str(job)]
    assert {"pre-commit", "build", "run-unit-tests", "run-knowledge-tests"} <= set(consumers)
    for name in consumers:
        assert "setup-workflow" in ancestors(name), name


def test_build_cannot_bypass_failed_python_guard():
    # !cancelled() permits failed dependencies: build must explicitly require setup success.
    assert "needs.setup-workflow.result == 'success'" in JOBS["build"]["if"]


@pytest.mark.parametrize("report_name,job", REPORTS, ids=[name for name, _ in REPORTS])
def test_summary_aggregation_sorted_and_scoped(tmp_path, report_name, job):
    download = next(step for step in job["steps"] if step.get("name") == "Download all summaries")
    pattern = download["with"]["pattern"]
    root = tmp_path / download["with"]["path"]
    root.mkdir()
    for name, text in [(pattern.rstrip("*") + "-z", "row-z"), (pattern.rstrip("*") + "-a", "row-a"), ("unrelated-artifact", "WRONG")]:
        artifact = root / name
        artifact.mkdir()
        (artifact / "job_summary.txt").write_text(text + "\n")
    # Flat extraction is valid too; deeper files are not downloaded summary artifacts.
    (root / "job_summary.txt").write_text("row-flat\n")
    deep = root / (pattern.rstrip("*") + "-deep") / "nested"
    deep.mkdir(parents=True)
    (deep / "job_summary.txt").write_text("WRONG-DEEP\n")
    summary = tmp_path / "result.md"
    script = next(step["run"] for step in job["steps"] if step.get("name") == "Combine summaries into a table")
    result = run_shell(script, tmp_path, GITHUB_STEP_SUMMARY=str(summary))
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary.read_text().splitlines()[2:] == ["row-flat", "row-a", "row-z"], report_name


@pytest.mark.parametrize("report_name,job", REPORTS, ids=[name for name, _ in REPORTS])
@pytest.mark.parametrize("layout", ["missing", "empty", "wrong-prefix", "one-flat", "one-nested"])
def test_summary_aggregation_fails_when_no_matching_artifact(tmp_path, report_name, job, layout):
    download = next(step for step in job["steps"] if step.get("name") == "Download all summaries")
    root = tmp_path / download["with"]["path"]
    if layout != "missing":
        root.mkdir()
    if layout in {"wrong-prefix", "one-flat", "one-nested"}:
        directory = root
        if layout != "one-flat":
            directory = root / ("unrelated" if layout == "wrong-prefix" else download["with"]["pattern"].rstrip("*") + "-one")
            directory.mkdir()
        (directory / "job_summary.txt").write_text("one-row\n")
    script = next(step["run"] for step in job["steps"] if step.get("name") == "Combine summaries into a table")
    summary = tmp_path / "result.md"
    result = run_shell(script, tmp_path, GITHUB_STEP_SUMMARY=str(summary))
    if layout in {"one-flat", "one-nested"}:
        assert result.returncode == 0, result.stdout + result.stderr
        assert summary.read_text().splitlines()[2:] == ["one-row"]
    else:
        assert result.returncode != 0, report_name
