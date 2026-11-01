"""Billing records: exact yen arithmetic, monotonic counts and event deduplication."""

from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from harvest.drivers.dynamodb_driver import DynamoDbDriver, resolve_table_name


def tax_inclusive(raw_price, tax):
    return (Decimal(str(raw_price)) * Decimal(str(tax))).to_integral_value(rounding=ROUND_FLOOR)


def conditional_conflict(error):
    if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
        return True
    if error.response["Error"]["Code"] == "TransactionCanceledException":
        return any(
            item.get("Code") == "ConditionalCheckFailed"
            for item in error.response.get("CancellationReasons", [])
        )
    return False


class BillingController(DynamoDbDriver):
    def __init__(self, host, port):
        super().__init__(host, port, "Bills")
        self.set_partition_key("user_id")
        self.set_sort_key("context")

    def set_user_id(self, user_id):
        self.user_id = str(user_id)

    def show(self, user_id=None):
        user_id = user_id or self.user_id
        return {
            "destination": self.show_destination(user_id)["destination"],
            "history": self.list_history(user_id),
        }

    def show_destination(self, user_id=None):
        return self.get_item(user_id or self.user_id, "destination")["Item"]

    def list_history(self, user_id=None):
        items = self.query(user_id or self.user_id, "history", begins_with=True)["Items"]
        return sorted(
            (item["history"] for item in items),
            key=lambda item: datetime.strptime(item["invoice_date"], "%Y/%m/%d"),
        )

    def create(
        self,
        context,
        period,
        invoice_date,
        invoice_number,
        user_id=None,
        raw_price=0,
        tax=Decimal("1.10"),
        users_count=0,
        baseline_member_ids=None,
    ):
        user_id = user_id or self.user_id
        if baseline_member_ids is None:
            raise ValueError("Invoice creation requires the baseline member snapshot")
        baseline_member_ids = sorted(set(baseline_member_ids))
        history = {
            "billing_period": period,
            "invoice_date": invoice_date,
            "invoice_number": invoice_number,
            "price": tax_inclusive(raw_price, tax),
            "raw_price": Decimal(str(raw_price)),
            "status": "pending",
            "tax": Decimal(str(tax)),
            "users_count": users_count,
        }
        try:
            return self.update_item(
                user_id,
                "SET history = :history, baseline_member_ids = :members",
                sort_key=context,
                exp_attr_values={":history": history, ":members": baseline_member_ids},
                cond_exp="attribute_not_exists(#context)",
                exp_attr_names={"#context": "context"},
            )
        except ClientError as error:
            if not conditional_conflict(error):
                raise
        # A monthly retry and a registration event may see the same new member.
        # Reconcile through the SAME per-member ledger, never a separate count write.
        invoice = self.get_item(user_id, context, consistent_read=True).get("Item")
        if not invoice or "baseline_member_ids" not in invoice:
            raise ValueError("Billing baseline migration is required before reconciliation")
        unit_price = Decimal(str(raw_price)) / users_count if users_count else Decimal(0)
        for member_id in baseline_member_ids:
            self.increment_member(user_id, context, member_id, unit_price, tax)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def update(self, history, raw_price, users_count, tax=Decimal("1.10"), user_id=None):
        user_id = user_id or self.user_id
        try:
            return self.update_item(
                user_id,
                "SET history.#price = :price, history.#raw = :raw, history.#count = :count",
                sort_key=history,
                exp_attr_names={"#price": "price", "#raw": "raw_price", "#count": "users_count"},
                exp_attr_values={
                    ":price": tax_inclusive(raw_price, tax),
                    ":raw": Decimal(str(raw_price)),
                    ":count": users_count,
                },
                cond_exp="attribute_exists(user_id) AND history.#count < :count",
            )
        except ClientError as error:
            if not conditional_conflict(error):
                raise
            if "Item" not in self.get_item(user_id, history, consistent_read=True):
                raise ValueError("Current billing period has not been initialized") from error
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def increment_member(self, user_id, context, member_id, unit_price, tax):
        """Atomically claim a member event and update the invoice with optimistic locking."""
        if not member_id:
            raise ValueError("A stable member ID is required for billing deduplication")
        event_key = "member_event_" + sha256((context + "\0" + member_id).encode()).hexdigest()
        serialize = TypeSerializer().serialize
        name = resolve_table_name("Bills")
        for _ in range(8):
            if self.get_item(user_id, event_key, consistent_read=True).get("Item"):
                return {"ResponseMetadata": {"HTTPStatusCode": 200}}
            invoice = self.get_item(user_id, context, consistent_read=True).get("Item")
            if not invoice:
                raise ValueError("Current billing period has not been initialized")
            if "baseline_member_ids" not in invoice:
                raise ValueError(
                    "Billing baseline migration is required before registering members"
                )
            if member_id in invoice["baseline_member_ids"]:
                return {"ResponseMetadata": {"HTTPStatusCode": 200}}
            previous = invoice["history"]["users_count"]
            new_count = previous + 1
            raw_price = Decimal(str(unit_price)) * new_count
            values = {
                ":previous": previous,
                ":count": new_count,
                ":raw": raw_price,
                ":price": tax_inclusive(raw_price, tax),
                ":member": member_id,
            }
            actions = [
                {
                    "Put": {
                        "TableName": name,
                        "Item": {
                            key: serialize(value)
                            for key, value in {"user_id": user_id, "context": event_key}.items()
                        },
                        "ConditionExpression": "attribute_not_exists(user_id)",
                    }
                },
                {
                    "Update": {
                        "TableName": name,
                        "Key": {"user_id": serialize(user_id), "context": serialize(context)},
                        "UpdateExpression": "SET history.#count = :count, history.#raw = :raw, history.#price = :price",
                        "ConditionExpression": "history.#count = :previous AND attribute_exists(baseline_member_ids) AND NOT contains(baseline_member_ids, :member)",
                        "ExpressionAttributeNames": {
                            "#count": "users_count",
                            "#raw": "raw_price",
                            "#price": "price",
                        },
                        "ExpressionAttributeValues": {
                            key: serialize(value) for key, value in values.items()
                        },
                    }
                },
            ]
            try:
                return self.client.transact_write_items(TransactItems=actions)
            except ClientError as error:
                if not conditional_conflict(error):
                    raise
        raise RuntimeError("Concurrent billing updates exceeded retry limit")


def baseline_migration_request(invoice, members):
    """Prepare a reviewed, conditional migration without changing invoice amounts.

    Call only with a snapshot taken while membership writes are paused. This helper
    performs no AWS calls. A reviewed request must be applied before registrations
    are enabled on an existing period lacking its baseline.
    """
    if "baseline_member_ids" in invoice:
        raise ValueError("Invoice baseline is already present")
    identifiers = sorted({member["user_id"] for member in members})
    if invoice["history"]["users_count"] < len(identifiers):
        raise ValueError("Invoice count is below the member snapshot; reconcile separately")
    return {
        "Key": {"user_id": invoice["user_id"], "context": invoice["context"]},
        "UpdateExpression": "SET baseline_member_ids = :members",
        "ConditionExpression": "attribute_exists(user_id) AND attribute_not_exists(baseline_member_ids) AND history = :expected",
        "ExpressionAttributeValues": {":members": identifiers, ":expected": invoice["history"]},
    }
