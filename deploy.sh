#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./deploy.sh --stack-name NAME --s3-bucket BUCKET --region REGION --artifact DIRECTORY [--execute] --parameter-overrides EnvironmentName=dev|staging|prod [Key=Value ...]

Print commands by default. DIRECTORY must contain the validated CI artifact's
.aws-sam/build/template.yaml, .build/lambda and .build/lambda-manifest.json in
their original layout. Artifacts from before manifest support must be rebuilt.
--execute uploads that artifact and asks SAM to confirm the change set. This
script never rebuilds the application or replaces the selected artifact.
USAGE
}

stack_name=''
artifact_bucket=''
region=''
artifact=''
execute=false
parameters=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack-name|--s3-bucket|--region|--artifact)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 2; }
      case "$1" in
        --stack-name) stack_name=$2 ;;
        --s3-bucket) artifact_bucket=$2 ;;
        --region) region=$2 ;;
        --artifact) artifact=$2 ;;
      esac
      shift 2 ;;
    --parameter-overrides)
      shift
      [[ $# -gt 0 && "$1" != --* ]] || { usage >&2; exit 2; }
      while [[ $# -gt 0 && "$1" != --* ]]; do
        [[ "$1" =~ ^[A-Za-z][A-Za-z0-9]*=.+$ && "$1" != ParameterKey=* ]] || { echo 'Expected SAM parameter Key=Value (one argument per parameter).' >&2; exit 2; }
        parameters+=("$1")
        shift
      done ;;
    --execute) execute=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

environment_count=0
for parameter in "${parameters[@]}"; do
  if [[ "$parameter" == EnvironmentName=* ]]; then
    [[ "$parameter" =~ ^EnvironmentName=(dev|staging|prod)$ ]] || { echo 'EnvironmentName must be dev, staging or prod.' >&2; exit 2; }
    environment_count=$((environment_count + 1))
  fi
done
[[ "$environment_count" == 1 ]] || { echo 'Exactly one explicit EnvironmentName=dev|staging|prod is required.' >&2; exit 2; }

[[ "$stack_name" =~ ^[A-Za-z][A-Za-z0-9-]{0,127}$ ]] || { echo 'A valid stack name is required.' >&2; exit 2; }
[[ "$artifact_bucket" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || { echo 'A valid artifact bucket is required.' >&2; exit 2; }
[[ "$region" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || { echo 'An explicit AWS region is required.' >&2; exit 2; }
[[ -f "$artifact/.aws-sam/build/template.yaml" && -d "$artifact/.build/lambda/harvest" ]] || {
  echo 'Expected an extracted validated API artifact with .aws-sam/build/template.yaml and .build/lambda/harvest.' >&2
  exit 2
}
artifact="$(cd "$artifact" && pwd)"
script_directory="$(cd "$(dirname "$0")" && pwd)"
python3 "$script_directory/scripts/artifact_manifest.py" verify --artifact "$artifact"
command=(uv tool run --from aws-sam-cli==1.169.0 sam deploy
  --template-file "$artifact/.aws-sam/build/template.yaml"
  --stack-name "$stack_name" --s3-bucket "$artifact_bucket" --region "$region"
  --capabilities CAPABILITY_IAM --confirm-changeset)
if [[ ${#parameters[@]} -gt 0 ]]; then
  command+=(--parameter-overrides "${parameters[@]}")
fi
printf '%q ' "${command[@]}"
printf '\n'
if "$execute"; then
  # SAM can write deployment metadata here, leaving the selected artifact alone.
  work_directory=$(mktemp -d)
  trap 'rm -rf "$work_directory"' EXIT
  cd "$work_directory"
  "${command[@]}"
fi
