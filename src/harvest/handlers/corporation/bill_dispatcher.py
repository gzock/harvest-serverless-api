"""Retryable monthly reconciliation; one failing corporation does not skip others."""

import logging
import os
from harvest.commons.billing.billing import Billing
from harvest.controllers.corporation_controller import CorporationController

DYNAMO_HOST = os.environ.get("DYNAMO_HOST")
DYNAMO_PORT = os.environ.get("DYNAMO_PORT")
logger = logging.getLogger(__name__)


def lambda_handler(event, context):
    billing = Billing(DYNAMO_HOST, DYNAMO_PORT)
    corp = CorporationController(DYNAMO_HOST, DYNAMO_PORT)
    failed = []
    for corp_id in corp.list_corporations():
        try:
            corp.set_corporation_id(corp_id)
            billing.set_users(corp.list_users())
            billing.create(corp_id)
        except Exception:
            logger.error("Monthly billing reconciliation failed", extra={"corporation_id": corp_id})
            failed.append(corp_id)
    if failed:
        # EventBridge retries the invocation. Per-period conditional writes make
        # the corporations which already succeeded safe to revisit.
        raise RuntimeError(
            "Monthly billing reconciliation failed for %d corporations" % len(failed)
        )
    return {"message": "bill dispatch successfully"}
