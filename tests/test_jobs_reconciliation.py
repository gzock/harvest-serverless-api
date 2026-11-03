"""Recovery when the DynamoDB stream outbox has exhausted its own retries."""

import copy
import json
from unittest.mock import Mock

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
import pytest

from harvest.jobs import GenerationJobs, reconcile_jobs
from harvest import jobs as module


NOW = 1_800_000_000
QUEUE = "https://sqs.ap-northeast-1.amazonaws.com/123456789012/jobs"


def item(identifier="job-a", **updates):
    value = {
        "job_id": identifier,
        "status": "queued",
        "updated_at": NOW - 600,
        "attempts": 0,
        "expires_at": NOW + 86400,
    }
    value.update(updates)
    return value


def status_query(request):
    return (
        request["KeyConditionExpression"]
        .get_expression()["values"][0]
        .get_expression()["values"][1]
    )


def fixture_jobs(records, *, candidates=None):
    records = {record["job_id"]: copy.deepcopy(record) for record in records}
    table, sqs = Mock(), Mock()
    if candidates is None:
        candidates = {
            status: [record["job_id"] for record in records.values() if record["status"] == status]
            for status in ("queued", "running")
        }
    table.query.side_effect = lambda **kwargs: {
        "Items": [{"job_id": key} for key in candidates.get(status_query(kwargs), [])]
    }

    def get(Key, ConsistentRead):
        assert ConsistentRead is True
        return {"Item": copy.deepcopy(records.get(Key["job_id"], {}))}

    table.get_item.side_effect = get
    return GenerationJobs(table=table, clock=lambda: NOW), sqs


def test_stranded_outbox_job_is_sent_before_fairness_timestamp_is_updated():
    jobs, sqs = fixture_jobs([item()])
    order = Mock()
    order.attach_mock(jobs.table, "table")
    order.attach_mock(sqs, "sqs")
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["requeued"] == 1 and result["errors"] == 0
    sqs.send_message.assert_called_once_with(
        QueueUrl=QUEUE, MessageBody=json.dumps({"job_id": "job-a"})
    )
    update = jobs.table.update_item.call_args.kwargs
    assert update["UpdateExpression"] == "SET updated_at = :now"
    assert update["ExpressionAttributeValues"][":now"] == NOW
    assert update["ExpressionAttributeValues"][":observed_at"] == NOW - 600
    assert update["ExpressionAttributeValues"][":observed_status"] == "queued"
    names = [call[0] for call in order.mock_calls]
    assert names.index("sqs.send_message") < names.index("table.update_item")


def test_gsi_candidates_are_rechecked_against_current_base_table_state():
    jobs, sqs = fixture_jobs(
        [
            item("completed", status="succeeded"),
            item("recent", updated_at=NOW - 10),
            item("now-running", status="running", lease_until=NOW + 200),
        ],
        candidates={"queued": ["deleted", "completed", "recent", "now-running"], "running": []},
    )
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["skipped"] == 4
    sqs.send_message.assert_not_called()
    jobs.table.update_item.assert_not_called()


@pytest.mark.parametrize("attempts, expires_at", [(1, NOW + 86400), (5, NOW - 1)])
def test_live_worker_lease_is_never_modified_even_if_expired_or_exhausted(attempts, expires_at):
    jobs, sqs = fixture_jobs(
        [item(status="running", attempts=attempts, expires_at=expires_at, lease_until=NOW + 1)]
    )
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["skipped"] == 1
    sqs.send_message.assert_not_called()
    jobs.table.update_item.assert_not_called()


def test_expired_worker_lease_is_requeued_without_resetting_its_attempt_counter():
    jobs, sqs = fixture_jobs([item(status="running", attempts=3, lease_until=NOW - 1)])
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["requeued"] == 1
    update = jobs.table.update_item.call_args.kwargs
    assert update["UpdateExpression"] == "SET updated_at = :now"
    assert update["ExpressionAttributeValues"][":attempt"] == 3
    assert update["ExpressionAttributeValues"][":observed_lease"] == NOW - 1
    assert "lease_until = :observed_lease" in update["ConditionExpression"]


@pytest.mark.parametrize(
    "updates, error",
    [
        ({"attempts": 5}, "RETRIES_EXHAUSTED"),
        ({"expires_at": NOW}, "EXPIRED"),
        ({"status": "running", "attempts": 5, "lease_until": NOW - 1}, "RETRIES_EXHAUSTED"),
    ],
)
def test_expired_or_exhausted_jobs_become_terminal_without_queue_delivery(updates, error):
    jobs, sqs = fixture_jobs([item(**updates)])
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["failed"] == 1
    sqs.send_message.assert_not_called()
    update = jobs.table.update_item.call_args.kwargs
    assert update["ExpressionAttributeValues"][":error"] == error
    assert update["ExpressionAttributeValues"][":failed"] == "failed"
    assert "REMOVE lease_until" in update["UpdateExpression"]
    assert "attempts = :attempt" in update["ConditionExpression"]


def test_send_failure_leaves_job_old_enough_for_next_scheduled_recovery():
    jobs, sqs = fixture_jobs([item()])
    sqs.send_message.side_effect = ClientError(
        {"Error": {"Code": "ServiceUnavailable"}}, "SendMessage"
    )
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["errors"] == 1 and result["requeued"] == 0
    jobs.table.update_item.assert_not_called()


def test_concurrent_worker_claim_is_not_overwritten_by_post_send_fairness_update():
    jobs, sqs = fixture_jobs([item()])
    jobs.table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["requeued"] == 1 and result["conflicts"] == 1 and result["errors"] == 0
    assert jobs.table.update_item.call_count == 1
    condition = jobs.table.update_item.call_args.kwargs["ConditionExpression"]
    assert "#status = :observed_status" in condition
    assert "attempts = :attempt" in condition
    assert "updated_at = :observed_at" in condition


def test_status_partitions_are_interleaved_and_empty_pages_are_followed():
    jobs, sqs = fixture_jobs([item("q"), item("r", status="running", lease_until=NOW - 1)])
    cursor = {"job_id": "page-one", "status": "queued", "updated_at": NOW - 1000}
    jobs.table.query.side_effect = [
        {"Items": [], "LastEvaluatedKey": cursor},
        {"Items": [{"job_id": "r"}]},
        {"Items": [{"job_id": "q"}]},
    ]
    result = reconcile_jobs(jobs, sqs, QUEUE)
    requests = [call.kwargs for call in jobs.table.query.call_args_list]
    assert [status_query(request) for request in requests] == ["queued", "running", "queued"]
    assert requests[2]["ExclusiveStartKey"] == cursor
    assert all(request["ScanIndexForward"] is True for request in requests)
    assert all(
        "ConsistentRead" not in request for request in requests
    )  # GSIs cannot use strong reads.
    assert result["requeued"] == 2


def test_query_failure_in_one_partition_does_not_starve_the_other():
    jobs, sqs = fixture_jobs([item("r", status="running", lease_until=NOW - 1)])
    jobs.table.query.side_effect = [RuntimeError("query unavailable"), {"Items": [{"job_id": "r"}]}]
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["errors"] == 1 and result["requeued"] == 1


def test_first_partition_rotates_across_schedules_even_when_page_budget_is_exhausted():
    jobs, sqs = fixture_jobs([])
    reconcile_jobs(jobs, sqs, QUEUE, max_pages=1)
    first = status_query(jobs.table.query.call_args.kwargs)
    jobs.clock = lambda: NOW + 300
    reconcile_jobs(jobs, sqs, QUEUE, max_pages=1)
    second = status_query(jobs.table.query.call_args.kwargs)
    assert {first, second} == {"queued", "running"}


def test_item_and_page_limits_bound_work():
    jobs, sqs = fixture_jobs([item(str(index)) for index in range(20)])
    result = reconcile_jobs(jobs, sqs, QUEUE, max_items=3, max_pages=2)
    assert result["examined"] == 3 and result["requeued"] == 3
    assert jobs.table.query.call_args.kwargs["Limit"] == 3
    assert result["pages"] <= 2


def test_repeated_pagination_cursor_terminates_instead_of_looping():
    jobs, sqs = fixture_jobs([])
    jobs.table.query.side_effect = lambda **kwargs: {
        "Items": [],
        "LastEvaluatedKey": {"job_id": "same"},
    }
    result = reconcile_jobs(jobs, sqs, QUEUE)
    assert result["pages"] == 4
    assert result["errors"] == 2


def test_time_budget_stops_before_network_when_insufficient_for_one_call():
    jobs, sqs = fixture_jobs([item()])
    result = reconcile_jobs(jobs, sqs, QUEUE, monotonic=lambda: 100, max_seconds=3)
    assert result["pages"] == 0
    jobs.table.query.assert_not_called()
    sqs.send_message.assert_not_called()


def test_timeout_after_send_leaves_no_unsafe_or_incomplete_state_update():
    jobs, sqs = fixture_jobs([item()])
    # deadline creation, query, record/read, send, then no time for update.
    ticks = iter([0, 0, 0, 0, 18])
    result = reconcile_jobs(jobs, sqs, QUEUE, monotonic=lambda: next(ticks), max_seconds=20)
    assert result["requeued"] == 1
    jobs.table.update_item.assert_not_called()


def test_native_dynamodb_request_roundtrip_with_stubber():
    table = boto3.resource(
        "dynamodb",
        region_name="ap-northeast-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ).Table("Jobs")
    service = GenerationJobs(table, clock=lambda: NOW)
    sqs = Mock()
    serializer = TypeSerializer()
    record = item()
    encoded = {key: serializer.serialize(value) for key, value in record.items()}
    with Stubber(table.meta.client) as stub:
        query_params = {
            "TableName": "Jobs",
            "IndexName": "StatusUpdatedIndex",
            "KeyConditionExpression": ANY,
            "ProjectionExpression": "job_id",
            "ScanIndexForward": True,
            "Limit": 25,
        }
        stub.add_response("query", {"Items": [{"job_id": {"S": "job-a"}}]}, query_params)
        stub.add_response(
            "get_item",
            {"Item": encoded},
            {"TableName": "Jobs", "Key": {"job_id": "job-a"}, "ConsistentRead": True},
        )
        stub.add_response(
            "update_item",
            {},
            {
                "TableName": "Jobs",
                "Key": {"job_id": "job-a"},
                "UpdateExpression": "SET updated_at = :now",
                "ConditionExpression": "attribute_exists(job_id) AND #status = :observed_status AND updated_at = :observed_at AND attempts = :attempt",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":observed_status": "queued",
                    ":observed_at": NOW - 600,
                    ":attempt": 0,
                    ":now": NOW,
                },
            },
        )
        stub.add_response("query", {"Items": []}, query_params)
        assert reconcile_jobs(service, sqs, QUEUE)["requeued"] == 1
        stub.assert_no_pending_responses()


def test_scheduled_handler_uses_bounded_sdk_timeouts_and_remaining_lambda_time(monkeypatch):
    monkeypatch.setenv("GENERATION_JOBS_TABLE", "Jobs")
    monkeypatch.setenv("GENERATION_QUEUE_URL", QUEUE)
    resource, sqs = Mock(), Mock()
    resource_factory, client_factory = Mock(return_value=resource), Mock(return_value=sqs)
    monkeypatch.setattr(module.boto3, "resource", resource_factory)
    monkeypatch.setattr(module.boto3, "client", client_factory)
    summary = dict(examined=0, requeued=0, failed=0, skipped=0, conflicts=0, errors=0, pages=0)
    reconcile = Mock(return_value=summary)
    monkeypatch.setattr(module, "reconcile_jobs", reconcile)
    context = Mock()
    context.get_remaining_time_in_millis.return_value = 12_000
    assert module.reconciliation_handler({}, context) == summary
    assert reconcile.call_args.kwargs["max_seconds"] == 7
    config = client_factory.call_args.kwargs["config"]
    assert config.connect_timeout == 1 and config.read_timeout == 2
    assert config.retries["total_max_attempts"] == 1
