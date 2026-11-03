"""The built code and SAM template must move together without any file changes."""

import importlib.util
from pathlib import Path
import shutil

import pytest

spec = importlib.util.spec_from_file_location(
    "artifact_manifest", Path(__file__).parents[2] / "scripts/artifact_manifest.py"
)
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


@pytest.fixture
def artifact(tmp_path):
    root = tmp_path / "artifact"
    code = root / ".build/lambda/harvest"
    code.mkdir(parents=True)
    (code / "__init__.py").write_text("version = 1\n")
    (code / "handler.py").write_text("def handler(event, context): return {}\n")
    template = root / ".aws-sam/build/template.yaml"
    template.parent.mkdir(parents=True)
    template.write_text(
        "Resources:\n  App:\n    Type: AWS::Serverless::Function\n    Properties:\n      CodeUri: ../../.build/lambda\n"
    )
    manifest.create(root)
    return root


def test_artifact_can_be_relocated_without_changing_its_manifest(artifact, tmp_path):
    relocated = tmp_path / "downloaded-ci-artifact"
    shutil.copytree(artifact, relocated)
    manifest.verify(relocated)


@pytest.mark.parametrize("change", ["missing", "extra", "same-size-edit"])
def test_manifest_rejects_incomplete_or_mixed_code(artifact, change):
    code = artifact / ".build/lambda/harvest/__init__.py"
    if change == "missing":
        code.unlink()
    elif change == "extra":
        (code.parent / "unexpected.py").write_text("# wrong build\n")
    else:
        code.write_text("version = 2\n")
    with pytest.raises(ValueError, match="differs from its manifest"):
        manifest.verify(artifact)


def test_manifest_rejects_symlink_outside_artifact(artifact, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("not a build artifact")
    (artifact / ".build/lambda/escape").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        manifest.verify(artifact)


def test_creation_rejects_template_pointing_at_a_different_code_tree(artifact):
    template = artifact / ".aws-sam/build/template.yaml"
    template.write_text(
        template.read_text().replace("../../.build/lambda", "../../../another-build")
    )
    with pytest.raises(ValueError, match="Every function must reference"):
        manifest.create(artifact)
