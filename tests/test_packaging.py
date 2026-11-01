"""Import every deployed entry point without credentials, clients, or network access."""

import json
import os
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]


def sam_template():
    return yaml.safe_load((ROOT / "template.yaml").read_text())


def test_all_sam_handlers_import_without_network_or_aws_clients():
    template = sam_template()
    handlers = [
        resource["Properties"]["Handler"]
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::Serverless::Function"
    ]
    code = """
import importlib, json, socket, sys
import boto3

def forbidden(*args, **kwargs):
    raise AssertionError("Import must not construct AWS clients or connect to the network")

socket.create_connection = forbidden
socket.socket.connect = forbidden
boto3.client = forbidden
boto3.resource = forbidden
boto3.session.Session.client = forbidden
boto3.session.Session.resource = forbidden
for handler in json.loads(sys.argv[1]):
    module, name = handler.rsplit(".", 1)
    assert callable(getattr(importlib.import_module(module), name)), handler
"""
    environment = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        AWS_EC2_METADATA_DISABLED="true",
        AWS_DEFAULT_REGION="ap-northeast-1",
    )
    result = subprocess.run(
        [sys.executable, "-c", code, json.dumps(handlers)],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_harvest_package_import_does_not_eagerly_load_excel_or_aws():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, harvest; assert 'openpyxl' not in sys.modules; assert 'boto3' not in sys.modules",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_http_routes_require_the_shared_cognito_authorizer_and_public_preflight():
    template = sam_template()
    api = template["Resources"]["HarvestApi"]["Properties"]
    auth = api["Auth"]
    assert auth["DefaultAuthorizer"] == "CognitoAuthorizer"
    assert auth["AddDefaultAuthorizerToCorsPreflight"] is False
    assert "UserPoolArn" in auth["Authorizers"][auth["DefaultAuthorizer"]]
    assert "Idempotency-Key" in api["Cors"]["AllowHeaders"]
    assert api["Cors"]["AllowOrigin"] == {"Fn::Sub": "'${CorsOrigin}'"}
    assert {"DEFAULT_4XX", "DEFAULT_5XX"} <= api["GatewayResponses"].keys()
    count = 0
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::Serverless::Function":
            continue
        for event in resource["Properties"].get("Events", {}).values():
            if event["Type"] != "Api":
                continue
            properties = event["Properties"]
            assert properties["RestApiId"] == {"Ref": "HarvestApi"}
            assert properties["Method"].upper() != "OPTIONS"  # SAM provides mock CORS responses.
            assert properties.get("Auth", {}).get("Authorizer") != "NONE"
            count += 1
    assert count > 0


def test_existing_resources_must_be_selected_explicitly_for_each_deployment():
    parameters = sam_template()["Parameters"]
    names = [
        name
        for name in parameters
        if name.endswith(("TableName", "BucketName", "StreamArn"))
        or name in {"CognitoUserPoolId", "CognitoClientId", "CorsOrigin"}
    ]
    assert names
    for name in names:
        assert "Default" not in parameters[name], name


def test_job_records_survive_stack_changes_and_have_expiration_and_recovery():
    resource = sam_template()["Resources"]["GenerationJobsTable"]
    assert resource["DeletionPolicy"] == "Retain"
    assert resource["UpdateReplacePolicy"] == "Retain"
    properties = resource["Properties"]
    assert properties["TimeToLiveSpecification"] == {"AttributeName": "expires_at", "Enabled": True}
    assert properties["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
    assert properties["SSESpecification"]["SSEEnabled"] is True
    assert properties["StreamSpecification"]["StreamViewType"] == "NEW_IMAGE"


def test_every_stream_mapping_can_retry_partial_failures_and_quarantine_exhausted_records():
    resources = sam_template()["Resources"]
    mappings = []
    for resource in resources.values():
        if resource["Type"] == "AWS::Serverless::Function":
            mappings.extend(
                event["Properties"]
                for event in resource["Properties"].get("Events", {}).values()
                if event["Type"] == "DynamoDB"
            )
    assert mappings
    for mapping in mappings:
        assert "ReportBatchItemFailures" in mapping["FunctionResponseTypes"]
        assert mapping["BisectBatchOnFunctionError"] is True
        assert 0 < mapping["MaximumRetryAttempts"] <= 100
        assert 0 < mapping["MaximumRecordAgeInSeconds"] <= 86400
        destination = mapping["DestinationConfig"]["OnFailure"]
        assert destination["Type"] == "SQS"
        queue = destination["Destination"]["Fn::GetAtt"][0]
        assert resources[queue]["Type"] == "AWS::SQS::Queue"
        assert resources[queue]["Properties"]["SqsManagedSseEnabled"] is True


def test_every_function_deploys_the_locked_artifact_and_has_bounded_log_retention():
    template = sam_template()
    resources = template["Resources"]
    assert template["Globals"]["Function"]["Runtime"] == "python3.14"
    assert template["Globals"]["Function"]["Architectures"] == ["x86_64"]
    for name, resource in resources.items():
        if resource["Type"] != "AWS::Serverless::Function":
            continue
        assert resource["Properties"]["CodeUri"] == ".build/lambda/", name
        assert resource["Metadata"]["SkipBuild"] is True, name
        assert "/" not in resource["Properties"]["Handler"], name
        assert resources[name + "LogGroup"]["Properties"]["RetentionInDays"] == {
            "Ref": "LogRetentionDays"
        }
