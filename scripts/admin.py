#!/usr/bin/env python3
"""Explicit-target administrative operations. Dry-run never connects to AWS."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError(f"Invalid {label}")
    return value


def table_spec(name, partition, sort=None, parent_index=False, user_index=False, jobs=False):
    attributes = {partition: "S"}
    keys = [{"AttributeName": partition, "KeyType": "HASH"}]
    if sort:
        attributes[sort] = "S"
        keys.append({"AttributeName": sort, "KeyType": "RANGE"})
    result = {"TableName": name, "KeySchema": keys, "BillingMode": "PAY_PER_REQUEST"}
    if parent_index:
        attributes["parent_place_id"] = "S"
        result["LocalSecondaryIndexes"] = [
            {
                "IndexName": "ParentPlacesIndex",
                "KeySchema": [
                    {"AttributeName": partition, "KeyType": "HASH"},
                    {"AttributeName": "parent_place_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ]
    if user_index:
        result["GlobalSecondaryIndexes"] = [
            {
                "IndexName": "UserRolesIndex",
                "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ]
    if jobs:
        attributes.update(status="S", updated_at="N")
        result["GlobalSecondaryIndexes"] = [
            {
                "IndexName": "StatusUpdatedIndex",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "updated_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ]
    if partition == "project_id" and name.endswith(("Projects", "Roles", "Places", "Targets")):
        result["StreamSpecification"] = {
            "StreamEnabled": True,
            "StreamViewType": "NEW_AND_OLD_IMAGES",
        }
    result["AttributeDefinitions"] = [
        {"AttributeName": key, "AttributeType": kind} for key, kind in attributes.items()
    ]
    return result


def validate_target(args):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", args.environment):
        raise ValueError("An environment label is required")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", args.region):
        raise ValueError("An explicit AWS region is required")
    if args.environment == "local":
        parsed = urlsplit(args.endpoint_url or "")
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("localhost", "127.0.0.1")
            or not parsed.port
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Local operations require an explicit HTTP loopback endpoint and port")
        if args.command == "create-corporation":
            raise ValueError("Cognito provisioning requires an AWS environment")
    elif args.endpoint_url or not re.fullmatch(r"\d{12}", args.account_id or ""):
        raise ValueError("AWS operations require --account-id and prohibit endpoint overrides")


def build_plan(args):
    validate_target(args)
    now = datetime.now(timezone.utc).isoformat()
    operations = []
    if args.command == "create-tables":
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", args.table_prefix or ""):
            raise ValueError("An explicit --table-prefix is required")
        specs = [
            ("Projects", "project_id", None, None),
            ("Roles", "project_id", "user_id", None),
            ("Places", "project_id", "place_id", None),
            ("Targets", "project_id", "target_id", None),
            ("Notifications", "user_id", "notification_id", "ttl"),
            ("Bills", "user_id", "context", None),
            ("Imports", "project_id", "operation_id", "ttl"),
            ("UploadSessions", "session_id", None, "ttl"),
            ("GenerationJobs", "job_id", None, "expires_at"),
        ]
        # Cloud ownership of the new resources belongs to SAM, never this tool.
        if args.environment != "local":
            specs = specs[:6]
        for logical, partition, sort, ttl in specs:
            name = args.table_prefix + logical
            operations.append(
                {
                    "service": "dynamodb",
                    "operation": "create_table",
                    "request": table_spec(
                        name,
                        partition,
                        sort,
                        logical in ("Places", "Targets"),
                        logical == "Roles",
                        logical == "GenerationJobs",
                    ),
                }
            )
            if ttl:
                operations.append(
                    {
                        "service": "dynamodb",
                        "operation": "update_time_to_live",
                        "request": {
                            "TableName": name,
                            "TimeToLiveSpecification": {"Enabled": True, "AttributeName": ttl},
                        },
                    }
                )
    else:
        if not args.input:
            raise ValueError("An explicit --input JSON file is required")
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Input must be a JSON object")
        if args.command == "create-billing-destination":
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", args.table or ""):
                raise ValueError("An explicit --table is required")
            fields = ("name", "email", "postal_code", "address", "department", "tel", "contact")
            if set(data) != {"user_id", *fields}:
                raise ValueError(
                    "Billing input must contain exactly user_id, name, email, postal_code, address, department, tel, contact"
                )
            from boto3.dynamodb.types import TypeSerializer

            serializer = TypeSerializer()
            destination = {}
            for key in fields:
                value = data[key]
                if not isinstance(value, str) or len(value) > 2048:
                    raise ValueError(f"Invalid {key}")
                destination[key] = value
            text(data["name"], "name")
            text(data["email"], "email")
            item = {
                "user_id": text(data["user_id"], "user_id"),
                "context": "destination",
                "destination": destination,
                "created_at": now,
                "updated_at": now,
            }
            operations.append(
                {
                    "service": "dynamodb",
                    "operation": "put_item",
                    "request": {
                        "TableName": args.table,
                        "Item": {key: serializer.serialize(value) for key, value in item.items()},
                        "ConditionExpression": "attribute_not_exists(user_id)",
                    },
                }
            )
        else:
            if not re.fullmatch(re.escape(args.region) + r"_[A-Za-z0-9]+", args.user_pool_id or ""):
                raise ValueError("An explicit user pool in the selected region is required")
            fields = {"username", "email", "name", "kana_name", "pricing_plan"}
            if set(data) != fields:
                raise ValueError(
                    "Corporation input must contain exactly username, email, name, kana_name, pricing_plan; no password"
                )
            for key in fields:
                text(data[key], key)
            attrs = {
                "email": data["email"],
                "preferred_username": data["name"],
                "custom:user_type": "corporation",
                "custom:kana_username": data["kana_name"],
                "custom:pricing_plan": data["pricing_plan"],
                "custom:created_at": now,
            }
            operations.append(
                {
                    "service": "cognito-idp",
                    "operation": "admin_create_user",
                    "request": {
                        "UserPoolId": args.user_pool_id,
                        "Username": data["username"],
                        "DesiredDeliveryMediums": ["EMAIL"],
                        "UserAttributes": [
                            {"Name": key, "Value": value} for key, value in attrs.items()
                        ],
                    },
                }
            )
    return {
        "mode": "apply" if args.apply else "dry-run",
        "environment": args.environment,
        "region": args.region,
        "account_id": args.account_id,
        "endpoint_url": args.endpoint_url,
        "operations": operations,
    }


def execute(args, plan):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    config = Config(
        connect_timeout=3, read_timeout=10, retries={"mode": "standard", "max_attempts": 2}
    )
    options = {"region_name": args.region}
    if args.environment == "local":
        options.update(aws_access_key_id="localadmin", aws_secret_access_key="localadmin")
    elif args.profile:
        options["profile_name"] = args.profile
    session = boto3.Session(**options)
    if args.environment != "local":
        identity = session.client("sts", config=config).get_caller_identity()
        if identity["Account"] != args.account_id:
            raise ValueError("AWS caller account does not match --account-id; no changes applied")
    for operation in plan["operations"]:
        client = session.client(operation["service"], endpoint_url=args.endpoint_url, config=config)
        request = operation["request"]
        if operation["operation"] == "create_table":
            try:
                client.create_table(**request)
            except ClientError as error:
                if error.response["Error"]["Code"] != "ResourceInUseException":
                    raise
                # No implicit updates/replacements of existing tables.
                existing = client.describe_table(TableName=request["TableName"])["Table"]
                for field in ("KeySchema", "AttributeDefinitions"):
                    if sorted(existing[field], key=str) != sorted(request[field], key=str):
                        raise ValueError(
                            "Existing table schema differs; use an explicit migration"
                        ) from None
                for field in ("LocalSecondaryIndexes", "GlobalSecondaryIndexes"):
                    normalize = lambda indexes: sorted(
                        (
                            {key: index[key] for key in ("IndexName", "KeySchema", "Projection")}
                            for index in indexes
                        ),
                        key=lambda index: index["IndexName"],
                    )
                    if normalize(existing.get(field, [])) != normalize(request.get(field, [])):
                        raise ValueError("Existing table indexes differ; use an explicit migration")
            client.get_waiter("table_exists").wait(TableName=request["TableName"])
        elif operation["operation"] == "update_time_to_live":
            current = client.describe_time_to_live(TableName=request["TableName"])[
                "TimeToLiveDescription"
            ]
            if current.get("TimeToLiveStatus") in ("ENABLED", "ENABLING"):
                if (
                    current.get("AttributeName")
                    != request["TimeToLiveSpecification"]["AttributeName"]
                ):
                    raise ValueError("Existing TTL attribute differs; use an explicit migration")
            else:
                client.update_time_to_live(**request)
        else:
            result = getattr(client, operation["operation"])(**request)
            if operation["operation"] == "admin_create_user":
                # Do not print Cognito's temporary password or user attributes.
                print(
                    json.dumps(
                        {
                            "created_username": result["User"]["Username"],
                            "next_step": "Provision a corporation group named exactly this Username using reviewed IaC/admin controls.",
                        }
                    )
                )
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("create-tables", "create-corporation", "create-billing-destination")
    )
    parser.add_argument("--environment", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--account-id")
    parser.add_argument("--endpoint-url")
    parser.add_argument("--profile")
    parser.add_argument("--table-prefix")
    parser.add_argument("--table")
    parser.add_argument("--user-pool-id")
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the displayed operation after verifying the target account",
    )
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args)
        if args.apply:
            execute(args, plan)
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "environment": args.environment,
                        "operations": len(plan["operations"]),
                    }
                )
            )
        else:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
    except Exception as error:
        # SDK messages and tracebacks can contain request/PII; keep errors bounded.
        message = str(error) if isinstance(error, (ValueError, OSError)) else type(error).__name__
        parser.exit(2, "Administrative operation failed: " + message + "\n")


if __name__ == "__main__":
    main()
