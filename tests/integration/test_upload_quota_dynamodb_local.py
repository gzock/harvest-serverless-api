"""Validate resource-client transaction serialization and quota atomicity on DynamoDB."""

from uuid import uuid4
from io import BytesIO
from PIL import Image
from unittest.mock import Mock

import pytest

from harvest.uploads import UploadRateLimited, UploadService


def test_local_upload_quota_failure_rolls_back_all_actions(dynamodb_local, monkeypatch):
    db = dynamodb_local
    table_name = "test-upload-quota-" + uuid4().hex
    db.client.create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    try:
        table = db.resource.Table(table_name)
        monkeypatch.setenv("UPLOAD_ACTOR_HOURLY_LIMIT", "2")
        monkeypatch.setenv("UPLOAD_TARGET_WINDOW_LIMIT", "1")
        service = UploadService(table=table, s3=Mock())

        def create(target, now=1000):
            item = {
                "session_id": str(uuid4()),
                "actor_id": "actor",
                "project_id": db.project_id,
                "target_id": target,
                "phase": "pending",
            }
            service._store_session(item, now)
            return item

        create("first")
        with pytest.raises(UploadRateLimited):
            create("first")
        rows = table.scan(ConsistentRead=True)["Items"]
        assert len(rows) == 3  # actor quota, target quota, one session
        assert [row["count"] for row in rows if "count" in row] == [1, 1]
        create("second")  # rejected target attempt did not consume actor quota
        with pytest.raises(UploadRateLimited):
            create("third")
        rows = table.scan(ConsistentRead=True)["Items"]
        assert len([row for row in rows if row.get("phase") == "pending"]) == 2
        assert len([row for row in rows if row["session_id"].startswith("quota:target:")]) == 2
        # Delayed TTL cleanup does not prevent the next window's requests.
        create("first", now=3600)
        rows = table.scan(ConsistentRead=True)["Items"]
        assert len([row for row in rows if row.get("phase") == "pending"]) == 3
        # Run the actual complete/lease/retry expressions against the service,
        # substituting only the external image storage and photo side effect.
        output = BytesIO()
        Image.new("RGB", (2, 2)).save(output, format="PNG")
        data = output.getvalue()
        service.clock = lambda: 5000
        service._authorize = Mock()
        photo = service._authorize.return_value
        photo._target.return_value = {"photos": {"before": [], "after": []}}
        service.s3.head_object.return_value = {
            "VersionId": "v1",
            "ContentLength": len(data),
            "ContentType": "image/png",
        }
        service.s3.get_object.side_effect = lambda **_: {"Body": BytesIO(data)}
        session = service.create(
            "another-actor", db.project_id, "finalize-target", "before", "image/png", len(data)
        )
        attempts = 0

        def write_photo(*args, finalization, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient photo write failure")
            # PhotoController now atomically applies this action with membership.
            # This quota-focused test substitutes the photo write, but executes
            # the real session completion expression. Full cross-table races are
            # covered in test_security_review_dynamodb.py.
            return db.client.transact_write_items(TransactItems=[finalization])

        photo.create.side_effect = write_photo
        with pytest.raises(RuntimeError, match="transient"):
            service.complete(
                "another-actor", db.project_id, "finalize-target", session["session_id"]
            )
        stored = table.get_item(Key={"session_id": session["session_id"]}, ConsistentRead=True)[
            "Item"
        ]
        assert stored["pinned_version"] == "v1"
        assert "lease_token" not in stored
        result = service.complete(
            "another-actor", db.project_id, "finalize-target", session["session_id"]
        )
        assert (
            service.complete(
                "another-actor", db.project_id, "finalize-target", session["session_id"]
            )
            == result
        )
        stored = table.get_item(Key={"session_id": session["session_id"]}, ConsistentRead=True)[
            "Item"
        ]
        assert stored["phase"] == "completed"
        assert "lease_token" not in stored
        assert photo.create.call_count == 2
    finally:
        db.client.delete_table(TableName=table_name)
