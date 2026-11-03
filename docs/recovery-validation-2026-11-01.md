# Backend recovery validation — 2026-11-01

The isolated backend was rebuilt from the old `66d7d18` tree plus preserved command outputs, complete file additions and subsequent patches. The reconstruction and verification ran in an isolated copy. The recovery candidates and per-file source item IDs are recorded under `/workspace/recovered`; the integrated source is `/workspace/recovered/integration/harvest-serverless-api`.

## Fresh results

- **562 tests passed**, including all **24 DynamoDB Local integration tests**, with no skips.
- Ruff passed; all 96 Python files passed formatting checks.
- Mypy passed for all 65 application source files.
- OpenAPI freshness validation passed.
- Frozen dependency installation, binary-wheel Lambda packaging, CloudFormation lint and SAM 1.169.0 build passed.
- All 18 deployed handler imports passed using only the artifact and standard library, with AWS client creation and socket access prohibited during import.
- Artifact copies matched all 65 source files.
- pip-audit 2.10.1, using current PyPI advisories, checked 45 runtime/development packages: no known vulnerabilities and no skipped dependencies.

DynamoDB Local used `amazon/dynamodb-local@sha256:6ada631ec7aa5fc83446855eb17233e3dad184fec47477e8140c6c569cfe0c86`, bound only to `127.0.0.1:18000`. AWS resources were neither deployed nor invoked.

## Historical identity and reconstruction limits

The recovered `template.yaml`, regenerated `contracts/openapi.json`, and regenerated `uv.lock` match their preserved historical SHA-256 digests exactly. The lock was regenerated using uv 0.12.23 and the 45 observed dependency versions; digest equality verifies that the resulting bytes match the historical lock.

The entire source tree is not claimed byte-identical to the lost workspace. Some historical formatting was reapplied; a few synthetic authentication-test values had been redacted and were reconstructed with explicit notes in the source manifests. Those inferences do not replace the production JWT verifier, which was recovered from a complete historical read.

The original audit JSON files were not recovered in full. Historical validation prose remains evidence of the earlier run; the JSON linked below records this new verification. Advisory results are a point-in-time check, not a guarantee against undiscovered vulnerabilities.

## Evidence

- [Current build, source hashes and verification counts](validation/recovery-build.json)
- [Current dependency advisory results](validation/recovery-dependency-audit.json)
- Integration provenance: `/workspace/recovered/integration/backend-integration-manifest.json`
- Preserved historical records: `/workspace/recovery-records/`

Validation commands: `uv sync --frozen --group dev`, `DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:18000 uv run --frozen pytest -q`, `uv run --frozen ruff check .`, `uv run --frozen ruff format --check src tests scripts`, `uv run --frozen mypy`, `uv run --frozen python scripts/export_contract.py --check`, and `make build`.
