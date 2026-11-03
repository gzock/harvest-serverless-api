#!/usr/bin/env python3
"""Prepare a reviewable billing-baseline migration. Never connects to AWS."""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from boto3.dynamodb.types import TypeSerializer
from harvest.controllers.billing_controller import baseline_migration_request


def load_json(path):
    content = path.read_bytes()
    return json.loads(content, parse_float=Decimal), sha256(content).hexdigest()


def build_plan(invoice, members, table):
    if not isinstance(invoice, dict) or not isinstance(members, list):
        raise ValueError("Invoice must be an object and members must be an array")
    if not table or not all(
        isinstance(member, dict) and isinstance(member.get("user_id"), str) and member["user_id"]
        for member in members
    ):
        raise ValueError("A table name and nonempty member user_id strings are required")
    request = baseline_migration_request(invoice, members)
    serializer = TypeSerializer()
    low_level = {
        "TableName": table,
        **request,
        "Key": {key: serializer.serialize(value) for key, value in request["Key"].items()},
        "ExpressionAttributeValues": {
            key: serializer.serialize(value)
            for key, value in request["ExpressionAttributeValues"].items()
        },
    }
    unique = request["ExpressionAttributeValues"][":members"]
    history = invoice["history"]
    return {
        "schema_version": 1,
        "mode": "plan_only_no_aws_calls",
        "invoice": {"user_id": invoice["user_id"], "context": invoice["context"]},
        "review": {
            "existing_invoice": history,
            "input_member_records": len(members),
            "unique_members": len(unique),
            "duplicate_member_records": len(members) - len(unique),
            "invoice_count_minus_unique_members": history["users_count"] - len(unique),
            "amounts_changed": False,
            "warning": "A count gap may be a legitimate monthly high-water count or a historical error. This plan does not determine or correct billing amounts.",
            "preconditions": [
                "Pause member registrations and changes",
                "Drain in-flight billing events",
                "Review invoice and member snapshots from the same period",
                "Review historical count discrepancies separately",
            ],
        },
        "changes": [{"field": "baseline_member_ids", "before": None, "after": unique}],
        "conditional_update_request": low_level,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", type=Path, required=True)
    parser.add_argument("--members", type=Path, required=True)
    parser.add_argument(
        "--table", required=True, help="Explicit target table name; included in the plan only"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        invoice, invoice_hash = load_json(args.invoice)
        members, members_hash = load_json(args.members)
        plan = build_plan(invoice, members, args.table)
        plan["source_sha256"] = {"invoice": invoice_hash, "members": members_hash}
        rendered = (
            json.dumps(
                plan,
                ensure_ascii=False,
                indent=2,
                default=lambda value: str(value) if isinstance(value, Decimal) else value,
            )
            + "\n"
        )
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, "Cannot prepare billing baseline plan: " + str(error) + "\n")


if __name__ == "__main__":
    main()
