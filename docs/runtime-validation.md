# Runtime and dependency validation

Executed on **2026-10-22 15:53 UTC / 2026-10-23 00:53 JST** in the managed Linux x86_64 workspace. These are observed build and advisory results, not an AWS deployment report.

| Check | Result |
| --- | --- |
| Python / uv | CPython 3.14.8 / uv 0.12.23 |
| Frozen development environment | `uv sync --frozen --group dev` passed |
| Production dependency artifact | 22 pinned distributions, all binary wheels, hashes required; Python 3.14 / x86_64-manylinux_2_28 target |
| Artifact-only import smoke | All 18 SAM functions passed, including generation reconciliation and CSV import recovery |
| Source inclusion | Every `src/**/*.py` file matched its freshly built artifact copy |
| CloudFormation lint | `cfn-lint template.yaml` passed |
| SAM build | AWS SAM CLI 1.169.0, `sam build --template-file template.yaml`, passed |
| Production advisory audit | pip-audit 2.10.1 / PyPI: 22 distributions, zero known vulnerabilities, zero skipped dependencies |
| Runtime + development advisory audit | Same auditor/service: 45 distributions, zero known vulnerabilities, zero skipped dependencies |
| Job/recovery/reviewer regression tests | 57 passed; includes atomic five-attempt cap, live-lease fencing, bounded reconciliation and truncated JPEG rejection |
| DynamoDB Local jobs integration | 7 passed against explicit loopback endpoint; real conditional writes, GSI queries and concurrent claim fencing |
| Jobs type check | `mypy src/harvest/jobs.py` passed |

`make build` exports the frozen production lock with hashes, installs wheels into `.build/lambda`, copies application source, runs the import smoke test, lints infrastructure, and builds the SAM template. The smoke process uses Python `-S`, so host site-packages and editable installations cannot hide missing production dependencies. It rejects socket connections and AWS client/resource construction during imports.

The resulting artifact contains 82,411,481 bytes (approximately 78.6 MiB) before ZIP packaging. The SAM template uses `SkipBuild` and references the prepared `.build/lambda` directory. Preserve **both `.build` and `.aws-sam/build`** when transferring build output or deploying from CI.

## Reproduction

Use a writable cache/tool directory when the environment has a read-only home. These variables change caches only; they do not change the project lock or deployment target.

```sh
export UV_CACHE_DIR=/workspace/.uv-cache
export UV_PYTHON_INSTALL_DIR=/workspace/.uv-python
export UV_TOOL_DIR=/workspace/.uv-tools
export __SAM_CLI_APP_DIR=/workspace/.sam-config
export SAM_CLI_TELEMETRY=0
uv sync --frozen --group dev
make build
# With the CI-pinned DynamoDB Local container already listening locally:
DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:18000 \
  uv run --frozen pytest tests/integration/test_jobs_dynamodb_local.py -q
uv export --frozen --all-groups --no-emit-project \
  --format requirements-txt --output-file /tmp/harvest-all-requirements.txt
uv tool run --from pip-audit==2.10.1 pip-audit \
  --disable-pip --no-deps --strict --vulnerability-service pypi \
  --cache-dir /workspace/.audit-cache --progress-spinner off \
  --requirement .build/requirements.txt --format json \
  --output .build/audit-runtime.json
uv tool run --from pip-audit==2.10.1 pip-audit \
  --disable-pip --no-deps --strict --vulnerability-service pypi \
  --cache-dir /workspace/.audit-cache --progress-spinner off \
  --requirement /tmp/harvest-all-requirements.txt --format json \
  --output .build/audit-all.json
```

Both audit inputs are complete, pinned, hashed exports of `uv.lock`; `--no-deps --disable-pip` prevents the auditor from resolving a different dependency set. Audit results apply to the packages selected on Linux/Python 3.14. Advisory data can change after this execution.

## Evidence and limits

- [Runtime advisory results](validation/python-runtime-audit.json)
- [Runtime and development advisory results](validation/python-all-audit.json)
- [Function inventory, resolved versions, artifact size, lock/template digests](validation/lambda-build.json)

This pass validates local Linux imports, wheel availability, the frozen dependency graph, infrastructure syntax, the listed regression tests, and job state transitions against DynamoDB Local. It does not invoke Lambda containers or deploy/invoke AWS resources. Cognito token acceptance against a deployed pool, IAM execution, S3 CORS/lifecycle, DynamoDB/SQS event-source behavior, actual report delivery and retained-data migration still require a staging environment with explicit resource parameters. SAM CLI's isolated tooling environment and the operating system are outside the two application dependency audits. Run `make check` for the complete backend test/lint/type-check gates; this report lists only the focused checks executed for runtime and job validation.
