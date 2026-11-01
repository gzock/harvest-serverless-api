"""Generation job contracts, AWS request boundaries, and failure/retry behaviour."""

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
import pytest
import yaml

from harvest import jobs as module
from harvest.jobs import GenerationJobs, JobConflict


NOW = 1_800_000_000


def aws_error(code="ConditionalCheckFailedException"):
    return ClientError({"Error": {"Code": code, "Message": "fixture"}}, "UpdateItem")


def job(**updates):
    value = {
        "job_id": "job-1",
        "actor_id": "alice",
        "project_id": "project-1",
        "config": {"type": "zip"},
        "status": "running",
        "attempts": 1,
        "created_at": NOW - 10,
        "updated_at": NOW,
        "expires_at": NOW + 86400,
    }
    value.update(updates)
    return value


class MemoryCreates:
    """Model only conditional creation/read; DynamoDB updates are covered by Stubber."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression):
        assert ConditionExpression == "attribute_not_exists(job_id)"
        if Item["job_id"] in self.items:
            raise aws_error()
        self.items[Item["job_id"]] = copy.deepcopy(Item)

    def get_item(self, Key, ConsistentRead):
        assert ConsistentRead is True
        return {"Item": copy.deepcopy(self.items.get(Key["job_id"], {}))}


def dynamo_table():
    return boto3.resource(
        "dynamodb",
        region_name="ap-northeast-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ).Table("Jobs")


def wire(value):
    serializer = TypeSerializer()
    return {key: serializer.serialize(item) for key, item in value.items()}


def sqs_event(*ids):
    return {
        "Records": [
            {"messageId": identifier, "body": json.dumps({"job_id": "job-" + identifier})}
            for identifier in ids
        ]
    }


def test_idempotency_is_canonical_and_scoped_to_actor_and_project():
    service = GenerationJobs(MemoryCreates(), clock=lambda: NOW)
    first = service.create("alice", "project-a", {"type": "zip", "title": "Report"}, "request-1")
    repeated = service.create("alice", "project-a", {"title": "Report", "type": "zip"}, "request-1")
    other_actor = service.create(
        "bob", "project-a", {"type": "zip", "title": "Report"}, "request-1"
    )
    other_project = service.create(
        "alice", "project-b", {"type": "zip", "title": "Report"}, "request-1"
    )
    assert first == repeated
    assert len({first["job_id"], other_actor["job_id"], other_project["job_id"]}) == 3
    assert first["expires_at"] == NOW + 7 * 86400
    assert first["status"] == "queued" and first["attempts"] == 0
    with pytest.raises(JobConflict):
        service.create("alice", "project-a", {"type": "zip", "title": "Different"}, "request-1")


def test_expired_idempotency_record_is_not_returned_as_a_live_job():
    store = MemoryCreates()
    service = GenerationJobs(store, clock=lambda: NOW)
    first = service.create("alice", "project-a", {"type": "zip"}, "request-1")
    store.items[first["job_id"]]["expires_at"] = NOW
    with pytest.raises(JobConflict):
        service.create("alice", "project-a", {"type": "zip"}, "request-1")


def test_requests_without_idempotency_key_create_distinct_jobs():
    service = GenerationJobs(MemoryCreates(), clock=lambda: NOW)
    assert service.create("alice", "p", {})["job_id"] != service.create("alice", "p", {})["job_id"]


@pytest.mark.parametrize("key", ["", "x" * 129])
def test_invalid_idempotency_key_rejected_before_storage(key):
    table = Mock()
    with pytest.raises(ValueError):
        GenerationJobs(table).create("alice", "p", {}, key)
    table.put_item.assert_not_called()


def test_claim_uses_expiring_lease_and_atomic_attempt_counter():
    table = dynamo_table()
    expected = {
        "TableName": "Jobs",
        "Key": {"job_id": "job-1"},
        "UpdateExpression": "SET #status = :running, lease_until = :lease, updated_at = :now ADD attempts :one",
        "ConditionExpression": "expires_at > :now AND attempts < :max_attempts AND (#status = :queued OR (#status = :running AND lease_until < :now))",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {
            ":running": "running",
            ":queued": "queued",
            ":lease": NOW + 360,
            ":now": NOW,
            ":one": 1,
            ":max_attempts": 5,
        },
        "ReturnValues": "ALL_NEW",
    }
    with Stubber(table.meta.client) as stub:
        stub.add_response(
            "update_item", {"Attributes": wire(job(attempts=2, lease_until=NOW + 360))}, expected
        )
        result = GenerationJobs(table, clock=lambda: NOW).claim("job-1")
        assert result["attempts"] == 2
        assert result["lease_until"] == NOW + 360
        stub.assert_no_pending_responses()


def test_retry_cap_is_enforced_atomically_before_duplicate_delivery_can_render(monkeypatch):
    table = dynamo_table()
    store = GenerationJobs(table, clock=lambda: NOW)
    render = Mock()
    monkeypatch.setattr(module, "GenerationJobs", lambda: store)
    monkeypatch.setattr(module, "render", render)
    expected = {
        "TableName": "Jobs",
        "Key": {"job_id": "job-1"},
        "UpdateExpression": ANY,
        "ConditionExpression": "expires_at > :now AND attempts < :max_attempts AND (#status = :queued OR (#status = :running AND lease_until < :now))",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {
            ":running": "running",
            ":queued": "queued",
            ":lease": NOW + 360,
            ":now": NOW,
            ":one": 1,
            ":max_attempts": 5,
        },
        "ReturnValues": "ALL_NEW",
    }
    with Stubber(table.meta.client) as stub:
        stub.add_client_error(
            "update_item",
            service_error_code="ConditionalCheckFailedException",
            service_message="The fifth attempt already owns or exhausted this job",
            expected_params=expected,
        )
        assert module.worker_handler(sqs_event("1"), None) == {"batchItemFailures": []}
        render.assert_not_called()
        stub.assert_no_pending_responses()


def test_unavailable_claim_is_not_treated_like_a_database_outage():
    table = dynamo_table()
    with Stubber(table.meta.client) as stub:
        stub.add_client_error("update_item", service_error_code="ConditionalCheckFailedException")
        stub.add_client_error(
            "update_item", service_error_code="ProvisionedThroughputExceededException"
        )
        service = GenerationJobs(table, clock=lambda: NOW)
        assert service.claim("already-running-or-expired") is None
        with pytest.raises(ClientError, match="ProvisionedThroughputExceededException"):
            service.claim("database-throttled")
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("operation", ["complete", "failed"])
def test_old_worker_cannot_overwrite_a_newer_attempt(operation):
    table = dynamo_table()
    expected = {
        "TableName": "Jobs",
        "Key": {"job_id": "job-1"},
        "UpdateExpression": ANY,
        "ConditionExpression": "#status = :running AND attempts = :attempt",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": ANY,
    }
    with Stubber(table.meta.client) as stub:
        stub.add_client_error(
            "update_item",
            service_error_code="ConditionalCheckFailedException",
            expected_params=expected,
        )
        service = GenerationJobs(table, clock=lambda: NOW)
        with pytest.raises(ClientError):
            if operation == "complete":
                service.complete(job(), "project-1/job-1/report.zip")
            else:
                service.failed(job())
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "attempts, permanent, expected_status",
    [(1, False, "queued"), (5, False, "failed"), (1, True, "failed")],
)
def test_failure_policy_is_bounded_and_does_not_publish_raw_exception(
    attempts, permanent, expected_status
):
    table = Mock()
    GenerationJobs(table, clock=lambda: NOW).failed(job(attempts=attempts), permanent=permanent)
    values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert values[":state"] == expected_status
    assert values[":error"] == "GENERATION_FAILED"
    assert values[":attempt"] == attempts


@pytest.mark.parametrize(
    "actor, project, expiry",
    [("bob", "project-1", NOW + 1), ("alice", "project-2", NOW + 1), ("alice", "project-1", NOW)],
)
def test_public_view_does_not_disclose_another_actor_project_or_expired_job(actor, project, expiry):
    table, s3 = Mock(), Mock()
    table.get_item.return_value = {
        "Item": job(status="succeeded", output_key="project-1/job-1/report.zip", expires_at=expiry)
    }
    assert GenerationJobs(table, clock=lambda: NOW).public(actor, project, "job-1", s3=s3) is None
    s3.generate_presigned_url.assert_not_called()


def test_success_view_only_signs_owned_result_for_five_minutes(monkeypatch):
    monkeypatch.setenv("GENERATED_BUCKET_NAME", "generated-test")
    table, s3 = Mock(), Mock()
    table.get_item.return_value = {
        "Item": job(status="succeeded", output_key="project-1/job-1/報告.zip")
    }
    s3.generate_presigned_url.return_value = "https://signed.example/report"
    result = GenerationJobs(table, clock=lambda: NOW).public("alice", "project-1", "job-1", s3=s3)
    assert result["result"] == {"name": "報告.zip", "download_url": "https://signed.example/report"}
    assert set(result) == {"job_id", "status", "created_at", "updated_at", "result"}
    s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "generated-test", "Key": "project-1/job-1/報告.zip"},
        ExpiresIn=300,
    )


@pytest.mark.parametrize(
    "key",
    ["project-2/job-1/report.zip", "project-1/job-2/report.zip", "project-1/job-10/report.zip"],
)
def test_success_view_refuses_foreign_output_prefix(key):
    table, s3 = Mock(), Mock()
    table.get_item.return_value = {"Item": job(status="succeeded", output_key=key)}
    with pytest.raises(RuntimeError, match="ownership"):
        GenerationJobs(table, clock=lambda: NOW).public("alice", "project-1", "job-1", s3=s3)
    s3.generate_presigned_url.assert_not_called()


def test_outbox_publishes_insert_records_and_returns_only_failed_sequence(monkeypatch):
    monkeypatch.setenv(
        "GENERATION_QUEUE_URL", "https://sqs.ap-northeast-1.amazonaws.com/123456789012/jobs"
    )
    sqs = boto3.client(
        "sqs",
        region_name="ap-northeast-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    monkeypatch.setattr(module.boto3, "client", lambda name: sqs)
    records = [
        {
            "eventName": "INSERT",
            "dynamodb": {"SequenceNumber": "1", "NewImage": {"job_id": {"S": "job-a"}}},
        },
        {"eventName": "MODIFY", "dynamodb": {"SequenceNumber": "2"}},
        {
            "eventName": "INSERT",
            "dynamodb": {"SequenceNumber": "3", "NewImage": {"job_id": {"S": "job-b"}}},
        },
    ]
    with Stubber(sqs) as stub:
        stub.add_client_error(
            "send_message",
            service_error_code="ServiceUnavailable",
            expected_params={
                "QueueUrl": module.os.environ["GENERATION_QUEUE_URL"],
                "MessageBody": json.dumps({"job_id": "job-a"}),
            },
        )
        stub.add_response(
            "send_message",
            {"MessageId": "sent"},
            {
                "QueueUrl": module.os.environ["GENERATION_QUEUE_URL"],
                "MessageBody": json.dumps({"job_id": "job-b"}),
            },
        )
        assert module.dispatch_handler({"Records": records}, None) == {
            "batchItemFailures": [{"itemIdentifier": "1"}]
        }
        stub.assert_no_pending_responses()


def test_worker_partial_failures_do_not_prevent_other_jobs_completing(monkeypatch):
    store = Mock()
    first, second = job(job_id="job-a"), job(job_id="job-b")
    store.claim.side_effect = [first, second]
    monkeypatch.setattr(module, "GenerationJobs", lambda: store)
    render = Mock(side_effect=[OSError("sensitive internal details"), "project-1/job-b/report.zip"])
    monkeypatch.setattr(module, "render", render)
    assert module.worker_handler(sqs_event("a", "b"), None) == {
        "batchItemFailures": [{"itemIdentifier": "a"}]
    }
    store.failed.assert_called_once_with(first, permanent=False)
    store.complete.assert_called_once_with(second, "project-1/job-b/report.zip")


def test_worker_rechecks_authorization_and_marks_revocation_permanent(monkeypatch):
    store = Mock()
    store.claim.return_value = job()
    monkeypatch.setattr(module, "GenerationJobs", lambda: store)
    auth = Mock()
    auth.guard.return_value = False
    monkeypatch.setattr(module, "Auth", lambda *args: auth)
    result = module.worker_handler(sqs_event("a"), None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "a"}]}
    auth.guard.assert_called_once_with("admin")
    store.failed.assert_called_once_with(store.claim.return_value, permanent=True)
    store.complete.assert_not_called()


def test_worker_rejects_foreign_renderer_output(monkeypatch):
    store = Mock()
    store.claim.return_value = job()
    monkeypatch.setattr(module, "GenerationJobs", lambda: store)
    monkeypatch.setattr(module, "render", lambda item: "other-project/job-1/secret.zip")
    assert module.worker_handler(sqs_event("a"), None)["batchItemFailures"] == [
        {"itemIdentifier": "a"}
    ]
    store.complete.assert_not_called()
    store.failed.assert_called_once()


def test_duplicate_or_finished_message_does_not_render_again(monkeypatch):
    store, render = Mock(), Mock()
    store.claim.return_value = None
    monkeypatch.setattr(module, "GenerationJobs", lambda: store)
    monkeypatch.setattr(module, "render", render)
    assert module.worker_handler(sqs_event("a"), None) == {"batchItemFailures": []}
    render.assert_not_called()


def test_claim_failure_and_failed_state_write_remain_retryable(monkeypatch):
    store = Mock()
    store.claim.side_effect = [aws_error("ThrottlingException"), job()]
    store.failed.side_effect = aws_error("ThrottlingException")
    monkeypatch.setattr(module, "GenerationJobs", lambda: store)
    monkeypatch.setattr(module, "render", Mock(side_effect=OSError("render unavailable")))
    assert module.worker_handler(sqs_event("a", "b"), None) == {
        "batchItemFailures": [{"itemIdentifier": "a"}, {"itemIdentifier": "b"}]
    }


@pytest.mark.parametrize("kind", ["zip", "excel"])
def test_renderer_uses_original_actor_project_and_job_scoped_output(monkeypatch, kind):
    from harvest import generate

    config = {
        "type": kind,
        "title": "施工報告",
        "needs_include_hierarchy": True,
        "needs_date": True,
        "by_name": True,
        "needs_make_dir": True,
        "needs_all_photos": False,
        "char_enc": "utf_8",
        "template_id": "basic_1.xlsx",
        "needs_print_settings": True,
        "needs_sort": True,
    }
    item = job(config=config)
    authorizer, generator = Mock(), Mock()
    monkeypatch.setattr(module, "authorize", authorizer)
    monkeypatch.setattr(generate, "Generate", lambda *args: generator)
    generator.gen_zip.return_value = "project-1/job-1/report.zip"
    generator.gen_excel_doc.return_value = "project-1/job-1/report.xlsx"
    expected = generator.gen_zip if kind == "zip" else generator.gen_excel_doc
    assert module.render(item) == expected.return_value
    authorizer.assert_called_once_with("alice", "project-1")
    generator.set_user_id.assert_called_once_with("alice")
    generator.set_project_id.assert_called_once_with("project-1")
    assert expected.call_args.args == ("project-1",)
    assert expected.call_args.kwargs["output_id"] == "job-1"
    assert expected.call_args.kwargs["needs_download_url"] is False


def test_malformed_queue_message_is_retried_for_dead_letter_handling(monkeypatch):
    store = Mock()
    monkeypatch.setattr(module, "GenerationJobs", lambda: store)
    assert module.worker_handler({"Records": [{"messageId": "bad", "body": "not-json"}]}, None) == {
        "batchItemFailures": [{"itemIdentifier": "bad"}]
    }
    store.claim.assert_not_called()
    store.failed.assert_not_called()


def test_queue_visibility_and_lambda_timeout_protect_the_job_lease():
    template = yaml.safe_load((Path(__file__).resolve().parents[1] / "template.yaml").read_text())
    table = Mock()
    table.update_item.return_value = {"Attributes": job()}
    GenerationJobs(table, clock=lambda: NOW).claim("job-1")
    lease_seconds = table.update_item.call_args.kwargs["ExpressionAttributeValues"][":lease"] - NOW
    worker = template["Resources"]["GenerationWorkerFunction"]["Properties"]
    queue = template["Resources"]["GenerationQueue"]["Properties"]
    assert worker["Timeout"] < lease_seconds < queue["VisibilityTimeout"]
    assert queue["VisibilityTimeout"] >= 6 * worker["Timeout"]
    assert (
        "ReportBatchItemFailures" in worker["Events"]["Jobs"]["Properties"]["FunctionResponseTypes"]
    )
