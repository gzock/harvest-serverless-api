"""Regression checks for permission revocation and duplicate-job interleavings."""

import copy
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from botocore.exceptions import ClientError
from PIL import Image
import pytest
import yaml

from harvest import jobs
from harvest.controllers.role_controller import RoleController
from harvest.controllers.target_controller import TargetController
from harvest.controllers.photo_controller import PhotoController
from harvest.drivers.dynamodb_driver import DynamoDbDriver


def test_authorization_role_lookup_is_strongly_consistent(monkeypatch):
    """A successful role revocation must be observed by the next guard."""
    getter = Mock(return_value={"Item": {"role": "reporter", "status": "active"}})
    monkeypatch.setattr(DynamoDbDriver, "get_item", getter)
    role = object.__new__(RoleController)
    role.project_id, role.user_id = "project-a", "actor-a"
    role.show()
    assert getter.call_args.kwargs.get("consistent_read") is True


class LiveLeaseTable:
    """Interleaving: duplicate message in DLQ while another worker holds a lease."""

    def __init__(self, now):
        self.now = now
        self.item = {
            "job_id": "job-a",
            "status": "running",
            "attempts": 2,
            "lease_until": now + 120,
        }

    def get_item(self, **kwargs):
        return {"Item": copy.deepcopy(self.item)}

    def update_item(self, **kwargs):
        condition = kwargs["ConditionExpression"]
        # This fixture owns an unexpired lease. A condition referencing that lease
        # must reject this write; the old status-only condition incorrectly passes.
        if "lease_until" in condition:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        self.item["status"] = "failed"
        self.item.pop("lease_until", None)
        return {}


def test_duplicate_dead_letter_cannot_cancel_a_live_generation_worker(monkeypatch):
    now = 1_800_000_000
    table = LiveLeaseTable(now)
    service = jobs.GenerationJobs(table=table, clock=lambda: now)
    monkeypatch.setattr(jobs, "GenerationJobs", lambda: service)
    result = jobs.dead_letter_handler(
        {"Records": [{"messageId": "duplicate", "body": json.dumps({"job_id": "job-a"})}]}, None
    )
    assert table.item["status"] == "running"
    assert table.item["lease_until"] == now + 120
    assert result == {"batchItemFailures": [{"itemIdentifier": "duplicate"}]}


def test_dead_letter_reconciler_can_read_state_after_a_conditional_rejection():
    template = yaml.safe_load((Path(__file__).resolve().parents[1] / "template.yaml").read_text())
    policies = template["Resources"]["GenerationDeadLetterFunction"]["Properties"]["Policies"]
    allowed = set()
    for policy in policies:
        for statement in policy.get("Statement", []):
            if statement.get("Effect") == "Allow" and statement.get("Resource") == {
                "Fn::GetAtt": ["GenerationJobsTable", "Arn"]
            }:
                actions = statement["Action"]
                allowed.update([actions] if isinstance(actions, str) else actions)
    assert {"dynamodb:GetItem", "dynamodb:UpdateItem"} <= allowed


def test_target_cleanup_includes_a_write_committed_before_project_tombstone(monkeypatch):
    target = object.__new__(TargetController)
    target.project_id = "project-a"
    target.set_partition_key("project_id")
    target.set_sort_key("target_id")
    table, s3 = Mock(), Mock()
    target._DynamoDbDriver__table = table
    target._TargetController__s3 = s3
    recently_committed = {
        "project_id": "project-a",
        "target_id": "target-a",
        "created_at": "2026-01-01T00:00:00Z",
        "photos": {"before": ["photo-a"], "after": []},
    }
    # Eventual reads can omit an acknowledged write; strong reads cannot.
    table.query.side_effect = lambda **kwargs: {
        "Items": [recently_committed] if kwargs.get("ConsistentRead") else []
    }
    delete_rows = Mock(return_value={})
    monkeypatch.setattr(DynamoDbDriver, "batch_del_item", delete_rows)
    target.delete_all()
    s3.delete_obj.assert_called_once_with("photo-a")
    delete_rows.assert_called_once_with([{"project_id": "project-a", "target_id": "target-a"}])


def test_join_creation_cannot_repopulate_a_deleting_or_nonexistent_project(monkeypatch):
    monkeypatch.setenv("TABLE_PROJECTS", "test-projects")
    role = object.__new__(RoleController)
    role.project_id, role.user_id = "project-a", "actor-a"
    role.client = Mock()
    role.create("worker", status="request")
    actions = role.client.transact_write_items.call_args.kwargs["TransactItems"]
    checks = [action["ConditionCheck"] for action in actions if "ConditionCheck" in action]
    assert len(checks) == 1
    check = checks[0]
    assert check["TableName"] == "test-projects"
    assert check["Key"] == {"project_id": {"S": "project-a"}}
    assert "attribute_exists(project_id)" in check["ConditionExpression"]
    assert {"S": "deleting"} in check["ExpressionAttributeValues"].values()


@pytest.mark.parametrize("missing_bytes", [2, 8, 16])
def test_photo_validation_rejects_truncated_jpeg_pixels(missing_bytes):
    image = BytesIO()
    Image.new("RGB", (20, 20), (23, 42, 81)).save(image, "JPEG")
    truncated = image.getvalue()[:-missing_bytes]
    with pytest.raises(ValueError, match="Invalid photo"):
        PhotoController.validate_image(truncated)


def test_dynamo_clients_follow_deployment_region_and_bound_network_timeouts(monkeypatch):
    import boto3

    resource, client = Mock(), Mock()
    resource_factory, client_factory = Mock(return_value=resource), Mock(return_value=client)
    monkeypatch.setattr(boto3, "resource", resource_factory)
    monkeypatch.setattr(boto3, "client", client_factory)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("TABLE_PROJECTS", "review-projects")
    DynamoDbDriver(None, None, "Projects")
    for factory in (resource_factory, client_factory):
        assert factory.call_args.kwargs["region_name"] == "eu-west-1"
        assert "endpoint_url" not in factory.call_args.kwargs
        config = factory.call_args.kwargs["config"]
        assert config.connect_timeout == 3
        assert config.read_timeout == 10
        assert config.retries["max_attempts"] == 2
    resource.Table.assert_called_once_with("review-projects")
