"""Exact billing arithmetic and member-ledger transactions against real DynamoDB Local."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock

import pytest

from harvest.commons.billing.billing import Billing
from harvest.controllers.billing_controller import BillingController, baseline_migration_request


@pytest.fixture
def local_billing(dynamodb_local, monkeypatch):
    db = dynamodb_local
    name = "test-billing-" + uuid4().hex
    table = db.resource.create_table(
        TableName=name,
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "context", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "context", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    monkeypatch.setenv("TABLE_BILLS", name)
    controller = BillingController.__new__(BillingController)
    controller.user_id = "corp"
    controller.logical_table_name = "Bills"
    controller.client, controller.dynamodb = db.client, db.resource
    controller._DynamoDbDriver__table = table
    controller.set_partition_key("user_id")
    controller.set_sort_key("context")
    service = Billing.__new__(Billing)
    service.user_id, service.billing = "corp", controller
    try:
        yield service, controller, table
    finally:
        table.delete()


def set_date(monkeypatch, *parts):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(*parts, tzinfo=timezone.utc)

    monkeypatch.setattr("harvest.commons.billing.billing.datetime", Clock)


def invoice(table, month):
    return table.get_item(
        Key={"user_id": "corp", "context": "history_" + month}, ConsistentRead=True
    )["Item"]


def test_local_baseline_duplicate_reconcile_and_month_boundary(local_billing, monkeypatch):
    service, controller, table = local_billing
    set_date(monkeypatch, 2026, 10, 25, 23, 59)
    service.set_users([{"user_id": "baseline"}])
    service.create()
    initial = invoice(table, "202610")
    assert initial["baseline_member_ids"] == ["baseline"]
    assert initial["history"]["price"] == Decimal("550")
    service.update("create_user", "corp", "baseline")
    assert invoice(table, "202610") == initial
    service.update("create_user", "corp", "added")
    service.update("create_user", "corp", "added")
    assert invoice(table, "202610")["history"]["users_count"] == 2
    # Monthly reprocessing and registration share the same deduplication ledger.
    service.set_users([{"user_id": "baseline"}, {"user_id": "added"}, {"user_id": "reconciled"}])
    service.create()
    service.create()
    service.update("create_user", "corp", "reconciled")
    history = invoice(table, "202610")["history"]
    assert history["users_count"] == 3
    assert history["raw_price"] == Decimal("1500")
    assert history["price"] == Decimal("1650")
    assert history["invoice_number"] == initial["history"]["invoice_number"]
    assert history["billing_period"] == "2026/09/26-2026/10/25"
    service.set_users([{"user_id": "baseline"}])
    service.create()
    assert invoice(table, "202610")["history"] == history
    # Legacy policy intentionally uses UTC 25/26 as the period boundary.
    set_date(monkeypatch, 2026, 10, 26, 0, 0)
    service.set_users([])
    service.create()
    service.update("create_user", "corp", "added")
    service.update("create_user", "corp", "added")
    next_history = invoice(table, "202611")["history"]
    assert next_history["users_count"] == 1
    assert next_history["price"] == Decimal("550")
    assert next_history["invoice_number"] == 2
    assert next_history["billing_period"] == "2026/10/26-2026/11/25"
    assert invoice(table, "202610")["history"] == history


def test_local_concurrent_member_events_retry_without_lost_increment(local_billing):
    _, controller, table = local_billing
    controller.create(
        "history_202610",
        "period",
        "2026/10/31",
        1,
        raw_price=500,
        users_count=1,
        baseline_member_ids=["baseline"],
    )
    original = controller.client.transact_write_items
    fired = False

    def interleaved(**kwargs):
        nonlocal fired
        if not fired:
            fired = True
            controller.increment_member(
                "corp", "history_202610", "concurrent", 500, Decimal("1.10")
            )
        return original(**kwargs)

    proxy = Mock(wraps=controller.client)
    proxy.transact_write_items.side_effect = interleaved
    controller.client = proxy
    controller.increment_member("corp", "history_202610", "requested", 500, Decimal("1.10"))
    assert invoice(table, "202610")["history"]["users_count"] == 3
    assert invoice(table, "202610")["history"]["price"] == Decimal("1650")
    events = [
        item
        for item in table.scan(ConsistentRead=True)["Items"]
        if item["context"].startswith("member_event_")
    ]
    assert len(events) == 2
    assert proxy.transact_write_items.call_count == 3


def test_local_legacy_baseline_plan_preserves_reviewed_invoice(local_billing):
    _, controller, table = local_billing
    legacy = {
        "user_id": "corp",
        "context": "history_202610",
        "history": {
            "users_count": 1,
            "raw_price": Decimal("500"),
            "price": Decimal("550"),
            "tax": Decimal("1.10"),
            "status": "pending",
            "invoice_number": 17,
            "invoice_date": "2026/10/31",
            "billing_period": "2026/09/26-2026/10/25",
        },
    }
    table.put_item(Item=legacy)
    with pytest.raises(ValueError, match="baseline migration"):
        controller.increment_member("corp", "history_202610", "new", 500, Decimal("1.10"))
    table.update_item(**baseline_migration_request(legacy, [{"user_id": "existing"}]))
    assert invoice(table, "202610")["history"] == legacy["history"]
    controller.increment_member("corp", "history_202610", "existing", 500, Decimal("1.10"))
    assert invoice(table, "202610")["history"] == legacy["history"]
    controller.increment_member("corp", "history_202610", "new", 500, Decimal("1.10"))
    actual = invoice(table, "202610")["history"]
    assert actual["users_count"] == 2 and actual["price"] == Decimal("1100")
    assert actual["invoice_number"] == 17 and actual["status"] == "pending"
