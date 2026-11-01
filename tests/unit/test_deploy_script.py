"""Deployment wrapper tests; fake uv ensures these never call AWS or install tooling."""

import json
import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy.sh"


@pytest.fixture
def deployment(tmp_path):
    artifact = tmp_path / "artifact"
    template = artifact / ".aws-sam/build/template.yaml"
    template.parent.mkdir(parents=True)
    template.write_text("Resources: {}\n")
    (artifact / ".build/lambda/harvest").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.json"
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['DEPLOY_TEST_LOG']).write_text(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))\n"
        "sys.exit(int(os.environ.get('DEPLOY_TEST_EXIT', '0')))\n"
    )
    fake_uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DEPLOY_TEST_LOG": str(log)}
    args = [
        "bash",
        str(SCRIPT),
        "--artifact",
        str(artifact),
        "--stack-name",
        "harvest-staging",
        "--s3-bucket",
        "example-artifacts",
        "--region",
        "ap-northeast-1",
    ]
    return artifact, log, env, args


def test_dry_run_does_not_run_uv_or_rebuild(deployment):
    _, log, env, args = deployment
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert not log.exists()
    assert "sam deploy" in result.stdout
    assert "--confirm-changeset" in result.stdout


def test_execute_uses_saved_artifact_with_explicit_region_and_no_rebuild(deployment):
    artifact, log, env, args = deployment
    result = subprocess.run(
        args + ["--execute", "--parameter-overrides", "EnvironmentName=staging"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    call = json.loads(log.read_text())
    assert call["args"][:5] == ["tool", "run", "--from", "aws-sam-cli==1.169.0", "sam"]
    assert call["args"][5] == "deploy"
    assert str(artifact / ".aws-sam/build/template.yaml") in call["args"]
    assert call["args"][-2:] == ["--parameter-overrides", "EnvironmentName=staging"]
    assert call["cwd"] != str(artifact)
    assert not Path(call["cwd"]).exists()


def test_command_failure_is_propagated_and_temporary_directory_removed(deployment):
    _, log, env, args = deployment
    result = subprocess.run(
        args + ["--execute"],
        env={**env, "DEPLOY_TEST_EXIT": "17"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 17
    assert not Path(json.loads(log.read_text())["cwd"]).exists()


def test_incomplete_artifact_is_rejected_before_any_tool_invocation(deployment):
    artifact, log, env, args = deployment
    (artifact / ".aws-sam/build/template.yaml").unlink()
    result = subprocess.run(
        args + ["--execute"], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert not log.exists()


def test_unrestricted_sam_options_cannot_override_the_selected_artifact(deployment):
    _, log, env, args = deployment
    result = subprocess.run(
        args + ["--execute", "--template-file", "other.yaml"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert not log.exists()
