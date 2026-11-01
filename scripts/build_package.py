"""Build one Lambda artifact from the frozen lock, never from the host environment."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".build" / "lambda"
REQUIREMENTS = ROOT / ".build" / "requirements.txt"


def main() -> None:
    OUTPUT.parent.mkdir(exist_ok=True)
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(REQUIREMENTS),
        ],
        cwd=ROOT,
        check=True,
    )
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--require-hashes",
            "--no-deps",
            "--python-version",
            "3.14",
            "--python-platform",
            "x86_64-manylinux_2_28",
            "--only-binary",
            ":all:",
            "--target",
            str(OUTPUT),
            "--requirements",
            str(REQUIREMENTS),
        ],
        cwd=ROOT,
        check=True,
    )
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "requirements.txt")
    shutil.copytree(ROOT / "src", OUTPUT, dirs_exist_ok=True, ignore=ignore)
    if (OUTPUT / "zipfile.py").exists():
        raise RuntimeError("The artifact must use Python's maintained standard zipfile module")
    template = yaml.safe_load((ROOT / "template.yaml").read_text())
    handlers = [
        resource["Properties"]["Handler"]
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::Serverless::Function"
    ]
    # -S disables the host's site-packages, including the editable application.
    # Every handler must load using only stdlib plus the deployable artifact.
    smoke = """
import importlib, json, socket, sys
import boto3
def forbidden(*args, **kwargs):
    raise AssertionError('Handler imports must not create AWS clients or network connections')
socket.create_connection = forbidden
socket.socket.connect = forbidden
boto3.client = forbidden
boto3.resource = forbidden
boto3.session.Session.client = forbidden
boto3.session.Session.resource = forbidden
for handler in json.loads(sys.argv[1]):
    module, name = handler.rsplit('.', 1)
    assert callable(getattr(importlib.import_module(module), name)), handler
"""
    subprocess.run(
        [sys.executable, "-S", "-c", smoke, json.dumps(handlers)],
        cwd=OUTPUT,
        env={**os.environ, "PYTHONPATH": str(OUTPUT), "AWS_EC2_METADATA_DISABLED": "true"},
        check=True,
    )
    print(f"Built Lambda artifact: {OUTPUT}")


if __name__ == "__main__":
    main()
