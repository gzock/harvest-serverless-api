UV ?= uv
SAM_VERSION := 1.169.0
SAM = $(UV) tool run --from aws-sam-cli==$(SAM_VERSION) sam

.PHONY: sync test lint typecheck contract check package validate build

sync:
	$(UV) sync --frozen --group dev

test:
	$(UV) run --frozen pytest

lint:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check src tests scripts

typecheck:
	$(UV) run --frozen mypy

contract:
	$(UV) run --frozen python scripts/export_contract.py --check

check: lint typecheck contract test validate

# Install only hashed production wheels for Lambda's Python/OS/CPU target.
# SAM SkipBuild consumes this prepared artifact, not host site-packages.
package:
	$(UV) run --frozen python scripts/build_package.py

validate:
	$(UV) run --frozen cfn-lint template.yaml

build: package validate
	$(SAM) build --template-file template.yaml
	$(UV) run --frozen python scripts/artifact_manifest.py create --artifact .
