from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock
import pytest
from botocore.exceptions import ClientError
from harvest.controllers.billing_controller import BillingController, tax_inclusive
from harvest.commons.billing.billing import Billing


def conflict():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}}, "UpdateItem"
    )


def test_tax_arithmetic_is_exact_and_floors_yen():
    assert tax_inclusive(999, "1.10") == Decimal("1098")
    assert tax_inclusive(500 * 3, 1.1) == Decimal("1650")


def test_create_retry_reconciles_through_same_member_ledger():
    billing = BillingController.__new__(BillingController)
    billing.update_item = Mock(side_effect=conflict())
    billing.get_item = Mock(return_value={"Item": {"baseline_member_ids": ["one"]}})
    billing.increment_member = Mock()
    billing.create(
        "history_202610",
        "period",
        "2026/10/31",
        99,
        "corp",
        1500,
        users_count=3,
        baseline_member_ids=["one", "two", "three"],
    )
    assert billing.update_item.call_count == 1
    assert billing.increment_member.call_count == 3
    assert billing.increment_member.call_args.args[2] == "two"


def test_lower_count_does_not_reduce_existing_invoice():
    billing = BillingController.__new__(BillingController)
    billing.update_item = Mock(side_effect=conflict())
    billing.get_item = Mock(return_value={"Item": {"history": {"users_count": 5}}})
    assert (
        billing.update("history_202610", 1000, 2, user_id="corp")["ResponseMetadata"][
            "HTTPStatusCode"
        ]
        == 200
    )


def test_billing_member_duplicate_does_not_increment():
    billing = BillingController.__new__(BillingController)
    billing.get_item = Mock(return_value={"Item": {"context": "existing-event"}})
    billing.dynamodb, billing.client = Mock(), Mock()
    billing.increment_member("corp", "history_202610", "member", 500, Decimal("1.10"))
    billing.client.transact_write_items.assert_not_called()


def test_member_increment_is_atomic_and_retries_concurrent_update():
    billing = BillingController.__new__(BillingController)
    billing.dynamodb, billing.client = Mock(), Mock()
    billing.get_item = Mock(
        side_effect=[
            {},
            {"Item": {"history": {"users_count": 2}, "baseline_member_ids": []}},
            {},
            {"Item": {"history": {"users_count": 3}, "baseline_member_ids": []}},
        ]
    )
    error = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "conflict"},
            "CancellationReasons": [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}],
        },
        "TransactWriteItems",
    )
    billing.client.transact_write_items.side_effect = [error, {"ok": True}]
    assert billing.increment_member("corp", "history_202610", "member", 500, Decimal("1.10")) == {
        "ok": True
    }
    tx = billing.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert tx[0]["Put"]["ConditionExpression"] == "attribute_not_exists(user_id)"
    assert tx[1]["Update"]["ExpressionAttributeValues"][":count"] == {"N": "4"}
    assert tx[1]["Update"]["ExpressionAttributeValues"][":price"] == {"N": "2200"}


def test_missing_member_id_is_rejected():
    billing = BillingController.__new__(BillingController)
    with pytest.raises(ValueError, match="stable member"):
        billing.increment_member("corp", "history_202610", None, 500, Decimal("1.10"))


def test_snapshot_counts_unique_users():
    billing = Billing.__new__(Billing)
    billing.set_users([{"user_id": "one"}, {"user_id": "one"}, {"user_id": "two"}])
    assert len(billing.users) == 2


@pytest.mark.parametrize(
    "date,invoice,closing",
    [
        ((2024, 2, 25), "2024-02-29T00:00:00", "2024-02-25T00:00:00"),
        ((2024, 2, 26), "2024-03-31T00:00:00", "2024-03-25T00:00:00"),
        ((2026, 12, 26), "2027-01-31T00:00:00", "2027-01-25T00:00:00"),
    ],
)
def test_billing_keeps_existing_utc_closing_boundaries(monkeypatch, date, invoice, closing):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(*date, tzinfo=timezone.utc)

    monkeypatch.setattr("harvest.commons.billing.billing.datetime", Clock)
    billing = Billing.__new__(Billing)
    assert billing.get_cycle() == invoice
    assert billing.get_closing_date() == closing


def test_late_registration_already_in_monthly_snapshot_is_not_charged_twice():
    billing = BillingController.__new__(BillingController)
    billing.dynamodb, billing.client = Mock(), Mock()
    billing.get_item = Mock(
        side_effect=[
            {},
            {"Item": {"history": {"users_count": 2}, "baseline_member_ids": ["member"]}},
        ]
    )
    billing.increment_member("corp", "history_202610", "member", 500, Decimal("1.10"))
    billing.client.transact_write_items.assert_not_called()


def test_old_invoice_without_baseline_blocks_unsafe_increment():
    billing = BillingController.__new__(BillingController)
    billing.dynamodb, billing.client = Mock(), Mock()
    billing.get_item = Mock(side_effect=[{}, {"Item": {"history": {"users_count": 2}}}])
    with pytest.raises(ValueError, match="baseline migration"):
        billing.increment_member("corp", "history_202610", "member", 500, Decimal("1.10"))
    billing.client.transact_write_items.assert_not_called()


def test_baseline_migration_is_a_conditional_plan_without_amount_changes():
    from harvest.controllers.billing_controller import baseline_migration_request

    invoice = {
        "user_id": "corp",
        "context": "history_202610",
        "history": {"users_count": 3, "price": Decimal("1650")},
    }
    request = baseline_migration_request(invoice, [{"user_id": "member"}, {"user_id": "member"}])
    assert request["UpdateExpression"] == "SET baseline_member_ids = :members"
    assert request["ExpressionAttributeValues"][":members"] == ["member"]
    assert request["ExpressionAttributeValues"][":expected"] == invoice["history"]


def test_baseline_cli_emits_reviewable_plan_without_aws(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    invoice = tmp_path / "invoice.json"
    members = tmp_path / "members.json"
    output = tmp_path / "plan.json"
    invoice.write_text(
        json.dumps(
            {
                "user_id": "corp",
                "context": "history_202610",
                "history": {"users_count": 3, "price": 1650, "tax": 1.1},
            }
        )
    )
    members.write_text(json.dumps([{"user_id": "member"}, {"user_id": "member"}]))
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/plan_billing_baseline.py"),
            "--invoice",
            str(invoice),
            "--members",
            str(members),
            "--table",
            "StageBills",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(output.read_text())
    assert plan["mode"] == "plan_only_no_aws_calls"
    assert plan["review"]["amounts_changed"] is False
    assert plan["review"]["invoice_count_minus_unique_members"] == 2
    assert plan["conditional_update_request"]["ExpressionAttributeValues"][":expected"]["M"][
        "tax"
    ] == {"N": "1.1"}
    assert len(plan["source_sha256"]["invoice"]) == 64
