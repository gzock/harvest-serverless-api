"""Administrative plans are offline and cannot silently choose an AWS target."""

from argparse import Namespace
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest

spec = importlib.util.spec_from_file_location(
    "harvest_admin", Path(__file__).parents[1] / "scripts/admin.py"
)
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


def args(**values):
    result = dict(
        command="create-tables",
        environment="local",
        region="ap-northeast-1",
        account_id=None,
        endpoint_url="http://127.0.0.1:18000",
        profile=None,
        table_prefix="review_",
        table=None,
        user_pool_id=None,
        input=None,
        apply=False,
    )
    return Namespace(**(result | values))


def test_default_plan_is_offline_and_includes_all_current_tables(monkeypatch):
    monkeypatch.setattr(
        boto3, "Session", Mock(side_effect=AssertionError("AWS must not be called"))
    )
    plan = admin.build_plan(args())
    assert plan["mode"] == "dry-run"
    tables = [
        item["request"]["TableName"]
        for item in plan["operations"]
        if item["operation"] == "create_table"
    ]
    assert len(tables) == 9
    assert {"review_Imports", "review_UploadSessions", "review_GenerationJobs"} <= set(tables)


@pytest.mark.parametrize(
    "overrides",
    [
        {"endpoint_url": "https://dynamodb.ap-northeast-1.amazonaws.com"},
        {"endpoint_url": "http://127.0.0.1:8000@evil.example:80"},
        {"environment": "prod", "endpoint_url": None},
        {"environment": "prod", "account_id": "123456789012"},
        {"table_prefix": None},
    ],
)
def test_invalid_or_implicit_targets_are_rejected(overrides):
    with pytest.raises(ValueError):
        admin.build_plan(args(**overrides))


def test_billing_json_quotes_unicode_are_preserved_and_overwrite_prohibited(tmp_path):
    data = {
        key: '会社 "quoted" \\ path\nnew line'
        for key in (
            "user_id",
            "name",
            "email",
            "postal_code",
            "address",
            "department",
            "tel",
            "contact",
        )
    }
    source = tmp_path / "destination.json"
    source.write_text(json.dumps(data))
    plan = admin.build_plan(
        args(command="create-billing-destination", input=source, table="review_Bills")
    )
    request = plan["operations"][0]["request"]
    assert request["Item"]["destination"]["M"]["name"]["S"] == data["name"]
    assert request["ConditionExpression"] == "attribute_not_exists(user_id)"


def test_corporation_invites_without_supplied_or_printed_password(tmp_path):
    data = dict(
        username="corp-review",
        email="corp@example.invalid",
        name="法人",
        kana_name="ホウジン",
        pricing_plan="standard",
    )
    source = tmp_path / "corporation.json"
    source.write_text(json.dumps(data))
    parameters = args(
        command="create-corporation",
        environment="staging",
        endpoint_url=None,
        account_id="123456789012",
        user_pool_id="ap-northeast-1_review",
        input=source,
    )
    request = admin.build_plan(parameters)["operations"][0]["request"]
    assert request["DesiredDeliveryMediums"] == ["EMAIL"]
    assert "TemporaryPassword" not in request
    assert not any(attr["Name"] == "email_verified" for attr in request["UserAttributes"])
    source.write_text(json.dumps(data | {"password": "must-not-be-accepted"}))
    with pytest.raises(ValueError, match="no password"):
        admin.build_plan(parameters)


def test_apply_account_mismatch_never_constructs_mutation_client(monkeypatch):
    session = Mock()
    session.client.return_value.get_caller_identity.return_value = {"Account": "999999999999"}
    monkeypatch.setattr(boto3, "Session", lambda **_: session)
    parameters = args(
        environment="staging", endpoint_url=None, account_id="123456789012", apply=True
    )
    with pytest.raises(ValueError, match="account does not match"):
        admin.execute(parameters, admin.build_plan(parameters))
    assert [call.args[0] for call in session.client.call_args_list] == ["sts"]


def test_aws_table_plan_never_creates_sam_owned_new_tables():
    plan = admin.build_plan(
        args(environment="staging", endpoint_url=None, account_id="123456789012")
    )
    names = {
        item["request"]["TableName"]
        for item in plan["operations"]
        if item["operation"] == "create_table"
    }
    assert len(names) == 6
    assert not names.intersection(
        {"review_Imports", "review_UploadSessions", "review_GenerationJobs"}
    )
