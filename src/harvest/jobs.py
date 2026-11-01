"""Durable generation jobs: DynamoDB outbox -> SQS -> leased worker."""

import hashlib
import json
import os
import time
from collections import deque
from pathlib import PurePosixPath
from uuid import uuid4, uuid5, NAMESPACE_URL
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit

from harvest.auth import Auth
from harvest.exception import ActionDeniedError


class JobConflict(ValueError):
    status_code = 409


def _conditional(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def authorize(actor: str, project_id: str) -> None:
    auth = Auth(os.getenv("DYNAMO_HOST"), os.getenv("DYNAMO_PORT"), actor, project_id)
    if not auth.guard("admin"):
        raise ActionDeniedError()


class GenerationJobs:
    def __init__(self, table=None, clock=time.time):
        self.table = (
            table
            if table is not None
            else boto3.resource("dynamodb").Table(os.environ["GENERATION_JOBS_TABLE"])
        )
        self.clock = clock

    def get(self, job_id: str) -> dict:
        return self.table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item", {})

    def create(
        self, actor: str, project_id: str, config: dict, idempotency_key: str | None = None
    ) -> dict:
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise ValueError("Invalid idempotency key")
        digest = hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        job_id = (
            str(uuid5(NAMESPACE_URL, json.dumps([actor, project_id, idempotency_key])))
            if idempotency_key
            else str(uuid4())
        )
        now = int(self.clock())
        item: dict[str, Any] = {
            "job_id": job_id,
            "actor_id": actor,
            "project_id": project_id,
            "config": config,
            "config_hash": digest,
            "status": "queued",
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
            "expires_at": now + 7 * 86400,
        }
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(job_id)")
        except ClientError as exc:
            if not _conditional(exc):
                raise
            item = self.get(job_id)
            if (
                item.get("config_hash") != digest
                or item.get("actor_id") != actor
                or item.get("project_id") != project_id
                or item.get("expires_at", 0) <= now
            ):
                raise JobConflict("Idempotency key conflict") from None
        return item

    def claim(self, job_id: str) -> dict | None:
        now = int(self.clock())
        try:
            return self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #status = :running, lease_until = :lease, updated_at = :now ADD attempts :one",
                ConditionExpression="expires_at > :now AND attempts < :max_attempts AND (#status = :queued OR (#status = :running AND lease_until < :now))",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":running": "running",
                    ":queued": "queued",
                    ":lease": now + 360,
                    ":now": now,
                    ":one": 1,
                    ":max_attempts": 5,
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except ClientError as exc:
            if _conditional(exc):
                return None
            raise

    def complete(self, item: dict, output_key: str) -> None:
        self.table.update_item(
            Key={"job_id": item["job_id"]},
            UpdateExpression="SET #status = :done, output_key = :key, updated_at = :now REMOVE lease_until",
            ConditionExpression="#status = :running AND attempts = :attempt",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":done": "succeeded",
                ":running": "running",
                ":key": output_key,
                ":now": int(self.clock()),
                ":attempt": item["attempts"],
            },
        )

    def failed(self, item: dict, permanent: bool = False) -> None:
        terminal = permanent or item["attempts"] >= 5
        self.table.update_item(
            Key={"job_id": item["job_id"]},
            UpdateExpression="SET #status = :state, error_code = :error, updated_at = :now REMOVE lease_until",
            ConditionExpression="#status = :running AND attempts = :attempt",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":state": "failed" if terminal else "queued",
                ":running": "running",
                ":error": "GENERATION_FAILED",
                ":now": int(self.clock()),
                ":attempt": item["attempts"],
            },
        )

    def public(self, actor: str, project_id: str, job_id: str, s3=None) -> dict | None:
        item = self.get(job_id)
        if (
            not item
            or item.get("project_id") != project_id
            or item.get("actor_id") != actor
            or item.get("expires_at", 0) <= self.clock()
        ):
            return None
        result = {key: item[key] for key in ("job_id", "status", "created_at", "updated_at")}
        if item["status"] == "failed":
            result["error_code"] = item.get("error_code", "GENERATION_FAILED")
        if item["status"] == "succeeded":
            key = item["output_key"]
            if not key.startswith(project_id + "/" + job_id + "/"):
                raise RuntimeError("Invalid generation result ownership")
            client = s3 if s3 is not None else boto3.client("s3")
            result["result"] = {
                "name": PurePosixPath(key).name,
                "download_url": client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": os.environ["GENERATED_BUCKET_NAME"], "Key": key},
                    ExpiresIn=300,
                ),
            }
        return result


def dispatch_handler(event, context):
    """An INSERT is the outbox. Failed publishes retry; duplicate publishes are safe."""
    sqs = boto3.client("sqs")
    failures = []
    for record in event.get("Records", []):
        if record.get("eventName") != "INSERT":
            continue
        try:
            job_id = record["dynamodb"]["NewImage"]["job_id"]["S"]
            sqs.send_message(
                QueueUrl=os.environ["GENERATION_QUEUE_URL"],
                MessageBody=json.dumps({"job_id": job_id}),
            )
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}


def render(item: dict) -> str:
    from harvest.generate import Generate

    actor, project_id, config = item["actor_id"], item["project_id"], item["config"]
    authorize(actor, project_id)
    gen = Generate(os.getenv("DYNAMO_HOST"), os.getenv("DYNAMO_PORT"))
    gen.set_user_id(actor)
    gen.set_project_id(project_id)
    common = {key: config[key] for key in ("title", "needs_include_hierarchy", "needs_date")}
    common.update(needs_download_url=False, output_id=item["job_id"])
    if config["type"] == "zip":
        return gen.gen_zip(
            project_id,
            **common,
            **{
                key: config[key]
                for key in ("by_name", "needs_make_dir", "needs_all_photos", "char_enc")
            },
        )
    return gen.gen_excel_doc(
        project_id,
        **common,
        **{key: config[key] for key in ("template_id", "needs_print_settings", "needs_sort")},
    )


def worker_handler(event, context):
    jobs = GenerationJobs()
    failures = []
    for record in event.get("Records", []):
        item = None
        try:
            job_id = json.loads(record["body"])["job_id"]
            item = jobs.claim(job_id)
            if item is None:
                continue
            output_key = render(item)
            if not isinstance(output_key, str) or not output_key.startswith(
                item["project_id"] + "/" + item["job_id"] + "/"
            ):
                raise RuntimeError("Invalid generated output")
            jobs.complete(item, output_key)
        except Exception as exc:
            if item is not None:
                try:
                    jobs.failed(item, permanent=isinstance(exc, (ActionDeniedError, ValueError)))
                except Exception:
                    pass  # Unacknowledged message will retry after its lease expires.
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


failure_metrics = Metrics(namespace="Harvest", service="generation-worker")


@failure_metrics.log_metrics
def dead_letter_handler(event, context):
    """Make exhausted/time-out jobs terminal so clients never poll forever."""
    failure_metrics.add_metric(name="DeadLetterBatches", unit=MetricUnit.Count, value=1)
    jobs = GenerationJobs()
    failures = []
    for record in event.get("Records", []):
        try:
            job_id = json.loads(record["body"])["job_id"]
            try:
                jobs.table.update_item(
                    Key={"job_id": job_id},
                    UpdateExpression="SET #status = :failed, error_code = :error, updated_at = :now REMOVE lease_until",
                    ConditionExpression="attribute_exists(job_id) AND (#status = :queued OR (#status = :running AND lease_until < :now))",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":failed": "failed",
                        ":queued": "queued",
                        ":running": "running",
                        ":error": "RETRIES_EXHAUSTED",
                        ":now": int(jobs.clock()),
                    },
                )
                failure_metrics.add_metric(name="ExhaustedJobs", unit=MetricUnit.Count, value=1)
            except ClientError as exc:
                if not _conditional(exc):
                    raise
                current = jobs.get(job_id)
                if current and current.get("status") not in ("succeeded", "failed"):
                    # A duplicate delivery can reach the DLQ while another
                    # worker owns a live lease. Never invalidate that worker.
                    failures.append({"itemIdentifier": record["messageId"]})
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def reconcile_jobs(
    jobs: GenerationJobs,
    sqs,
    queue_url: str,
    *,
    monotonic=time.monotonic,
    minimum_age: int = 300,
    max_items: int = 100,
    max_pages: int = 8,
    max_seconds: float = 20,
) -> dict[str, int]:
    """Recover stranded outbox deliveries without taking ownership from a worker.

    The GSI is only a candidate list. Decisions use strongly consistent base-table
    reads, and writes fence against the exact status, attempt and lease observed.
    A successful SQS send precedes the fairness update: an ambiguous outcome can
    produce a duplicate message, but can never suppress an unsent job.
    """
    summary = dict(examined=0, requeued=0, failed=0, skipped=0, conflicts=0, errors=0, pages=0)
    deadline = monotonic() + max_seconds
    started_at = int(jobs.clock())
    cutoff = started_at - minimum_age
    # Alternate the first partition each scheduled interval. If slow AWS calls
    # consume the whole time budget on its first page, the other status still
    # receives priority on the next invocation.
    statuses = deque(
        ["queued", "running"] if (started_at // 300) % 2 == 0 else ["running", "queued"]
    )
    cursors: dict[str, dict] = {}
    seen_cursors: dict[str, set[str]] = {"queued": set(), "running": set()}

    def time_available() -> bool:
        # Handler clients have a 1s connect + 2s read budget and no SDK retries.
        # Leave room for the operation that is about to start and metric flushing.
        return monotonic() + 3 < deadline

    while statuses and summary["pages"] < max_pages and summary["examined"] < max_items:
        if not time_available():
            break
        status = statuses.popleft()
        request = {
            "IndexName": "StatusUpdatedIndex",
            "KeyConditionExpression": Key("status").eq(status) & Key("updated_at").lte(cutoff),
            "ProjectionExpression": "job_id",
            "ScanIndexForward": True,
            "Limit": min(25, max_items - summary["examined"]),
        }
        if status in cursors:
            request["ExclusiveStartKey"] = cursors[status]
        summary["pages"] += 1
        try:
            page = jobs.table.query(**request)
        except Exception:
            # An unavailable partition must not starve the other status partition.
            summary["errors"] += 1
            continue
        for candidate in page.get("Items", []):
            if summary["examined"] >= max_items or not time_available():
                return summary
            summary["examined"] += 1
            try:
                item = jobs.get(candidate["job_id"])
                now = int(jobs.clock())
                if (
                    not item
                    or item.get("status") != status
                    or item.get("updated_at", now) > cutoff
                    or (status == "running" and item.get("lease_until", 0) >= now)
                ):
                    summary["skipped"] += 1
                    continue
                condition = (
                    "attribute_exists(job_id) AND #status = :observed_status "
                    "AND updated_at = :observed_at AND attempts = :attempt"
                )
                values = {
                    ":observed_status": status,
                    ":observed_at": item["updated_at"],
                    ":attempt": item["attempts"],
                    ":now": now,
                }
                if status == "running":
                    if "lease_until" in item:
                        condition += " AND lease_until = :observed_lease"
                        values[":observed_lease"] = item["lease_until"]
                    else:
                        condition += " AND attribute_not_exists(lease_until)"
                expired = item.get("expires_at", 0) <= now
                terminal = expired or item["attempts"] >= 5
                if not time_available():
                    return summary
                if terminal:
                    values.update(
                        {
                            ":failed": "failed",
                            ":error": "EXPIRED" if expired else "RETRIES_EXHAUSTED",
                        }
                    )
                    update = "SET #status = :failed, error_code = :error, updated_at = :now REMOVE lease_until"
                else:
                    sqs.send_message(
                        QueueUrl=queue_url,
                        MessageBody=json.dumps({"job_id": item["job_id"]}),
                    )
                    summary["requeued"] += 1
                    if not time_available():
                        return summary
                    update = "SET updated_at = :now"
                jobs.table.update_item(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression=update,
                    ConditionExpression=condition,
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues=values,
                )
                if terminal:
                    summary["failed"] += 1
            except ClientError as exc:
                summary["conflicts" if _conditional(exc) else "errors"] += 1
            except Exception:
                summary["errors"] += 1
        cursor = page.get("LastEvaluatedKey")
        if cursor:
            marker = json.dumps(cursor, sort_keys=True, default=str)
            if marker in seen_cursors[status]:
                summary["errors"] += 1
                continue
            seen_cursors[status].add(marker)
            cursors[status] = cursor
            statuses.append(status)
    return summary


reconciliation_metrics = Metrics(namespace="Harvest", service="generation-reconciler")


@reconciliation_metrics.log_metrics
def reconciliation_handler(event, context):
    """Bounded scheduled recovery, including jobs whose stream record expired."""
    config = Config(connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 1})
    table = boto3.resource("dynamodb", config=config).Table(os.environ["GENERATION_JOBS_TABLE"])
    sqs = boto3.client("sqs", config=config)
    budget = 20.0
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if callable(remaining):
        budget = min(budget, max(0.0, remaining() / 1000 - 5))
    summary = reconcile_jobs(
        GenerationJobs(table=table), sqs, os.environ["GENERATION_QUEUE_URL"], max_seconds=budget
    )
    reconciliation_metrics.add_metric(name="ReconciliationRuns", unit=MetricUnit.Count, value=1)
    for key, metric in (
        ("requeued", "RecoveredJobs"),
        ("failed", "TerminatedJobs"),
        ("errors", "ReconciliationErrors"),
    ):
        if summary[key]:
            reconciliation_metrics.add_metric(
                name=metric, unit=MetricUnit.Count, value=summary[key]
            )
    return summary
