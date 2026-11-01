"""DynamoDB Streams consumer with per-record retries and idempotent writes."""

import logging
import os

from harvest.controllers.notification_controller import NotificationController
from harvest.factories.notification_factory import NotificationFactory

logger = logging.getLogger(__name__)
DYNAMO_HOST = os.environ.get("DYNAMO_HOST")
DYNAMO_PORT = os.environ.get("DYNAMO_PORT")


def lambda_handler(event, context):
    controller = NotificationController(DYNAMO_HOST, DYNAMO_PORT)
    failures = []
    for record in event["Records"]:
        try:
            factory = NotificationFactory(DYNAMO_HOST, DYNAMO_PORT, record)
            notifications = factory.generate()
            if notifications:
                controller.batch_create(notifications)
        except Exception:
            # Do not log the stream payload or silently acknowledge a failed record.
            logger.error(
                "Notification record processing failed", extra={"event_id": record.get("eventID")}
            )
            sequence = record.get("dynamodb", {}).get("SequenceNumber")
            if not sequence:
                raise
            failures.append({"itemIdentifier": sequence})
    return {"batchItemFailures": failures}
