"""Exercise job conditions and the recovery GSI against actual DynamoDB Local."""

import json
from unittest.mock import Mock
from uuid import uuid4

from botocore.exceptions import ClientError
import pytest

from harvest.jobs import GenerationJobs, JobConflict, reconcile_jobs


NOW = 1_800_000_000
QUEUE = "http://localhost/testing-queue"


@pytest.fixture
def jobs_table(dynamodb_local):
    table = dynamodb_local.resource.create_table(
        TableName="JobsIntegration" + uuid4().hex,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "updated_at", "AttributeType": "N"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "StatusUpdatedIndex",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "updated_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }
        ],
    )
    table.wait_until_exists()
    yield table
    table.delete()


def seed(table, identifier, **updates):
    item = {
        "job_id": identifier,
        "actor_id": "actor",
        "project_id": "project",
        "status": "queued",
        "attempts": 0,
        "updated_at": NOW - 600,
        "created_at": NOW - 600,
        "expires_at": NOW + 86400,
    }
    item.update(updates)
    table.put_item(Item=item)
    return item


def test_local_create_idempotency_and_atomic_claim_cap(jobs_table):
    clock = [NOW]
    jobs = GenerationJobs(jobs_table, clock=lambda: clock[0])
    item = jobs.create("actor", "project", {"type": "zip"}, "stable-request")
    assert jobs.create("actor", "project", {"type": "zip"}, "stable-request") == item
    with pytest.raises(JobConflict):
        jobs.create("actor", "project", {"type": "excel"}, "stable-request")
    for attempt in range(1, 6):
        claimed = jobs.claim(item["job_id"])
        assert claimed["attempts"] == attempt
        assert jobs.claim(item["job_id"]) is None  # live lease excludes a duplicate
        clock[0] = int(claimed["lease_until"]) + 1
    assert jobs.claim(item["job_id"]) is None  # sixth execution is blocked atomically
    assert jobs.get(item["job_id"])["attempts"] == 5
    result = reconcile_jobs(jobs, Mock(), QUEUE)
    assert result["failed"] == 1 and result["errors"] == 0
    assert jobs.get(item["job_id"])["error_code"] == "RETRIES_EXHAUSTED"


def test_local_expired_and_absent_records_cannot_be_claimed(jobs_table):
    jobs = GenerationJobs(jobs_table, clock=lambda: NOW)
    seed(jobs_table, "expired", expires_at=NOW)
    assert jobs.claim("expired") is None
    assert jobs.claim("missing") is None
    assert jobs.get("missing") == {}


def test_local_stale_worker_cannot_complete_or_fail_new_attempt(jobs_table):
    clock = [NOW]
    jobs = GenerationJobs(jobs_table, clock=lambda: clock[0])
    seed(jobs_table, "race")
    first = jobs.claim("race")
    clock[0] = int(first["lease_until"]) + 1
    second = jobs.claim("race")
    for operation in (
        lambda: jobs.complete(first, "project/race/old.zip"),
        lambda: jobs.failed(first),
    ):
        with pytest.raises(ClientError) as error:
            operation()
        assert error.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
    jobs.complete(second, "project/race/current.zip")
    current = jobs.get("race")
    assert current["status"] == "succeeded"
    assert current["output_key"] == "project/race/current.zip"
    assert "lease_until" not in current
    assert jobs.claim("race") is None


def test_local_retry_and_permanent_failure_transitions(jobs_table):
    jobs = GenerationJobs(jobs_table, clock=lambda: NOW)
    seed(jobs_table, "retry")
    first = jobs.claim("retry")
    jobs.failed(first)
    assert jobs.get("retry")["status"] == "queued"
    assert "lease_until" not in jobs.get("retry")
    second = jobs.claim("retry")
    assert second["attempts"] == 2
    jobs.failed(second, permanent=True)
    assert jobs.get("retry")["status"] == "failed"
    assert jobs.claim("retry") is None


def test_local_recovery_gsi_requeues_old_work_and_preserves_live_lease(jobs_table):
    jobs = GenerationJobs(jobs_table, clock=lambda: NOW)
    seed(jobs_table, "queued")
    seed(jobs_table, "stale", status="running", attempts=2, lease_until=NOW - 1)
    live = seed(jobs_table, "live", status="running", attempts=5, lease_until=NOW + 1)
    seed(jobs_table, "expired", expires_at=NOW)
    seed(jobs_table, "exhausted", status="running", attempts=5, lease_until=NOW - 1)
    seed(jobs_table, "recent", updated_at=NOW - 1)
    sqs = Mock()
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["requeued"] == 2 and result["failed"] == 2
    assert result["skipped"] == 1 and result["errors"] == result["conflicts"] == 0
    assert {
        json.loads(c.kwargs["MessageBody"])["job_id"] for c in sqs.send_message.call_args_list
    } == {"queued", "stale"}
    assert jobs.get("queued")["updated_at"] == NOW
    assert jobs.get("stale")["attempts"] == 2
    assert jobs.get("live") == live
    assert jobs.get("expired")["error_code"] == "EXPIRED"
    assert jobs.get("exhausted")["error_code"] == "RETRIES_EXHAUSTED"
    assert "lease_until" not in jobs.get("exhausted")


def test_local_recovery_fairness_update_cannot_overwrite_concurrent_claim(jobs_table):
    jobs = GenerationJobs(jobs_table, clock=lambda: NOW)
    seed(jobs_table, "race")
    sqs = Mock()
    sqs.send_message.side_effect = lambda **kwargs: jobs.claim("race")
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["requeued"] == result["conflicts"] == 1
    assert result["errors"] == 0
    current = jobs.get("race")
    assert current["status"] == "running" and current["attempts"] == 1
    assert current["lease_until"] == NOW + 360


def test_local_recovery_send_failure_keeps_job_eligible(jobs_table):
    jobs = GenerationJobs(jobs_table, clock=lambda: NOW)
    original = seed(jobs_table, "retry-send")
    sqs = Mock()
    sqs.send_message.side_effect = OSError("queue unavailable")
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["errors"] == 1 and result["requeued"] == 0
    assert jobs.get("retry-send") == original
