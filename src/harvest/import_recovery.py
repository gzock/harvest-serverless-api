"""Scheduled recovery for timed-out imports; no user credentials are required.

Only projects already locked by an accepted import are considered. The stored
manifest supplies all data; the worker cannot introduce a new import payload.
"""

import logging
import time

from boto3.dynamodb.conditions import Attr

from harvest.controllers.import_controller import (
    ImportController,
    ImportInProgressError,
    ImportPaused,
)
from harvest.drivers.dynamodb_driver import resolve_table_name

logger = logging.getLogger(__name__)


def handler(event, context):
    controller = ImportController(None, None)
    projects = controller.place_controller.dynamodb.Table(resolve_table_name("Projects"))
    journal = controller.place_controller.dynamodb.Table(resolve_table_name("Imports"))
    checkpoint_key = {"project_id": "__import_recovery__", "operation_id": "scan_cursor"}
    checkpoint = journal.get_item(Key=checkpoint_key, ConsistentRead=True).get("Item", {})
    arguments = {
        "FilterExpression": Attr("status").eq("importing")
        & Attr("import_lease_until").lt(int(time.time())),
        "ProjectionExpression": "project_id, import_operation",
        "ConsistentRead": True,
        "Limit": 25,
    }
    if checkpoint.get("cursor"):
        arguments["ExclusiveStartKey"] = checkpoint["cursor"]
    started = time.monotonic()
    recovered, failed, continuation = 0, 0, False

    def within_budget():
        return time.monotonic() - started <= 40 and (
            context is None or context.get_remaining_time_in_millis() >= 20_000
        )

    for _ in range(4):  # <=100 evaluated rows per invocation, with persisted paging.
        if time.monotonic() - started > 40 or (
            context and context.get_remaining_time_in_millis() < 20_000
        ):
            continuation = True
            break
        page = projects.scan(**arguments)
        interrupted = False
        for project in page.get("Items", []):
            if time.monotonic() - started > 40 or (
                context and context.get_remaining_time_in_millis() < 20_000
            ):
                # Keep the previous page cursor so skipped rows are revisited.
                interrupted = continuation = True
                break
            controller.set_project_id(project["project_id"])
            try:
                controller.resume(project["import_operation"], should_continue=within_budget)
                recovered += 1
            except ImportPaused:
                interrupted = continuation = True
                break
            except ImportInProgressError:
                pass  # Another invocation acquired the lease after the scan.
            except Exception as error:
                failed += 1
                logger.error(
                    "Import recovery failed: project_id=%s error_type=%s",
                    project["project_id"],
                    type(error).__name__,
                )
        if interrupted:
            break
        cursor = page.get("LastEvaluatedKey")
        # Concurrent schedulers may revisit a page; each import is independently
        # lease-fenced, and a complete scan eventually wraps to its beginning.
        journal.put_item(Item={**checkpoint_key, "cursor": cursor or {}})
        continuation = bool(cursor)
        if not cursor:
            break
        arguments["ExclusiveStartKey"] = cursor
    if failed:
        raise RuntimeError(f"{failed} import recovery operations require retry")
    result = {"recovered": recovered, "failed": 0}
    if continuation:
        result["continuation"] = True
    return result
