"""Seal a built Lambda tree and SAM template; verify without importing app dependencies."""

import argparse
from hashlib import sha256
import json
from pathlib import Path


MANIFEST = Path(".build/lambda-manifest.json")
TEMPLATE = Path(".aws-sam/build/template.yaml")
CODE = Path(".build/lambda")


def inventory(root: Path) -> dict[str, str]:
    root = root.resolve()
    code = root / CODE
    template = root / TEMPLATE
    if not code.is_dir() or not (code / "harvest").is_dir() or not template.is_file():
        raise ValueError("Expected the complete Lambda tree and built SAM template")
    for path in (root / ".build", code, root / ".aws-sam", template.parent, template):
        if path.is_symlink():
            raise ValueError("Deployment artifact must not contain symbolic links")
    files = [template]
    for path in sorted(code.rglob("*")):
        if path.is_symlink():
            raise ValueError("Deployment artifact must not contain symbolic links")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError("Deployment artifact contains a non-regular file")
    if len(files) == 1:
        raise ValueError("Lambda artifact is empty")
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest() for path in files
    }


def validate_template(root: Path) -> None:
    # Only creation needs PyYAML from the frozen build environment. Verification
    # uses the standard library, so deployment never resolves application deps.
    import yaml

    template = root / TEMPLATE
    definition = yaml.safe_load(template.read_text())
    functions = [
        value
        for value in definition.get("Resources", {}).values()
        if value.get("Type") == "AWS::Serverless::Function"
    ]
    if not functions:
        raise ValueError("Built template contains no SAM functions")
    for function in functions:
        uri = function.get("Properties", {}).get("CodeUri")
        if not isinstance(uri, str) or (template.parent / uri).resolve() != (root / CODE).resolve():
            raise ValueError("Every function must reference the packaged Lambda tree")


def create(root: Path) -> None:
    root = root.resolve()
    validate_template(root)
    result = {"version": 1, "files": inventory(root)}
    path = root / MANIFEST
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def verify(root: Path) -> None:
    root = root.resolve()
    path = root / MANIFEST
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing artifact manifest; rebuild with make build before deploying")
    manifest = json.loads(path.read_text())
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported artifact manifest")
    if manifest["files"] != inventory(root):
        raise ValueError("Artifact differs from its manifest: incomplete, changed, or mixed build")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "verify"])
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    try:
        (create if args.action == "create" else verify)(args.artifact)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Artifact validation failed: {error}\n")
    print("Artifact manifest created" if args.action == "create" else "Artifact manifest verified")


if __name__ == "__main__":
    main()
