"""Opt-in DynamoDB Local fixtures. Never accept a non-loopback endpoint."""

import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config
import pytest


@pytest.fixture
def dynamodb_local(monkeypatch):
    endpoint = os.environ.get("DYNAMODB_LOCAL_ENDPOINT")
    if not endpoint:
        pytest.skip("Set DYNAMODB_LOCAL_ENDPOINT to opt in to local integration tests")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not parsed.port
    ):
        pytest.fail("DYNAMODB_LOCAL_ENDPOINT must be an explicit HTTP loopback URL with a port")
    # Drivers use the default boto3 session; replace it with isolated dummy
    # credentials so the test can never consult an AWS profile or metadata.
    session = boto3.Session(
        aws_access_key_id="localtest",
        aws_secret_access_key="localtest",
        region_name="ap-northeast-1",
    )
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", session)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    resource = session.resource(
        "dynamodb",
        endpoint_url=endpoint,
        config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 0}),
    )
    client = session.client(
        "dynamodb",
        endpoint_url=endpoint,
        config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 0}),
    )
    prefix = "harvest_test_" + uuid4().hex
    tables = {}
    try:
        for logical, sort_key in (
            ("Projects", None),
            ("Places", "place_id"),
            ("Targets", "target_id"),
            ("Imports", "operation_id"),
            ("Roles", "user_id"),
        ):
            name = f"{prefix}_{logical}"
            monkeypatch.setenv("TABLE_" + logical.upper(), name)
            keys = [{"AttributeName": "project_id", "KeyType": "HASH"}]
            attributes = [{"AttributeName": "project_id", "AttributeType": "S"}]
            if sort_key:
                keys.append({"AttributeName": sort_key, "KeyType": "RANGE"})
                attributes.append({"AttributeName": sort_key, "AttributeType": "S"})
            options = {}
            if logical in ("Places", "Targets"):
                attributes.append({"AttributeName": "parent_place_id", "AttributeType": "S"})
                options["GlobalSecondaryIndexes"] = [
                    {
                        "IndexName": "ParentPlacesIndex",
                        "KeySchema": [
                            {"AttributeName": "project_id", "KeyType": "HASH"},
                            {"AttributeName": "parent_place_id", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ]
            if logical == "Roles":
                options["GlobalSecondaryIndexes"] = [
                    {
                        "IndexName": "UserProjectsIndex",
                        "KeySchema": [
                            {"AttributeName": "user_id", "KeyType": "HASH"},
                            {"AttributeName": "project_id", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ]
            table = resource.create_table(
                TableName=name,
                KeySchema=keys,
                AttributeDefinitions=attributes,
                BillingMode="PAY_PER_REQUEST",
                **options,
            )
            tables[logical] = table
            table.wait_until_exists()
        project_id = str(uuid4())
        tables["Projects"].put_item(Item={"project_id": project_id, "name": "Integration project"})
        yield SimpleNamespace(
            resource=resource,
            client=client,
            host=parsed.hostname,
            port=parsed.port,
            tables=tables,
            project_id=project_id,
        )
    finally:
        for table in tables.values():
            table.delete()
        resource.meta.client.close()
        client.close()
