"""Actual transaction fencing under upload ABA races and project tombstones."""

from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

from botocore.exceptions import ClientError
from PIL import Image
import pytest

from harvest.controllers.role_controller import RoleController
from harvest.uploads import UploadService


@pytest.fixture
def finalized_upload(dynamodb_local, monkeypatch):
    from harvest.controllers import photo_controller as module

    db = dynamodb_local
    table = db.resource.create_table(
        TableName="security_review_sessions_" + uuid4().hex,
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    try:
        final_s3, quarantine_s3 = Mock(), Mock()
        monkeypatch.setattr(module, "S3Driver", lambda *_: final_s3)
        photo = module.PhotoController(db.host, db.port)
        photo.set_project_id(db.project_id)
        photo.set_user_id("actor")
        photo.set_target_id("target")
        db.tables["Targets"].put_item(
            Item={
                "project_id": db.project_id,
                "target_id": "target",
                "parent_place_id": db.project_id,
                "photos": {
                    "before": ["original"],
                    "after": [],
                    "adopt": {"before": "original", "after": None},
                },
            }
        )
        now = [1000]
        service = UploadService(table=table, s3=quarantine_s3, clock=lambda: now[0])
        service.bucket = "quarantine"
        service._authorize = Mock(return_value=photo)
        image = BytesIO()
        Image.new("RGB", (2, 2)).save(image, "JPEG")
        data = image.getvalue()
        quarantine_s3.head_object.return_value = {
            "VersionId": "fixed-version",
            "ContentType": "image/jpeg",
            "ContentLength": len(data),
        }
        quarantine_s3.get_object.side_effect = lambda **_: {"Body": BytesIO(data)}
        quarantine_s3.generate_presigned_post.return_value = {"url": "local-mocked", "fields": {}}
        session = service.create(
            "actor", db.project_id, "target", "before", "image/jpeg", len(data)
        )
        yield db, table, service, photo, final_s3, now, session
    finally:
        table.delete()


def complete(fixture):
    db, _, service, _, _, _, session = fixture
    return service.complete("actor", db.project_id, "target", session["session_id"])


def test_finalize_is_atomic_and_completed_retry_never_resurrects_deleted_photo(finalized_upload):
    db, table, _, photo, s3, _, session = finalized_upload
    result = complete(finalized_upload)
    row = table.get_item(Key={"session_id": session["session_id"]}, ConsistentRead=True)["Item"]
    assert row["phase"] == "completed" and "lease_token" not in row
    assert result["photo_id"] == session["photo_id"]
    photo.update_adopt("target", "before", "original")
    photo.delete("target", session["photo_id"])
    assert complete(finalized_upload) == result
    assert photo._target("target")["photos"]["before"] == ["original"]
    assert s3.put_obj.call_count == 1


def test_expired_old_worker_cannot_publish_after_new_worker_completed_and_deleted(finalized_upload):
    _, table, _, photo, s3, now, session = finalized_upload
    interrupted = False

    def another_worker_finishes_then_user_deletes(*_):
        nonlocal interrupted
        if interrupted:
            return
        interrupted = True
        now[0] += 91
        complete(finalized_upload)
        photo.update_adopt("target", "before", "original")
        photo.delete("target", session["photo_id"])
        # Same photos map as the old worker's snapshot: a target-only CAS passes
        # this ABA transition. The session lease must reject the entire commit.

    s3.put_obj.side_effect = another_worker_finishes_then_user_deletes
    with pytest.raises(ClientError) as failure:
        complete(finalized_upload)
    assert failure.value.response["Error"]["Code"] == "TransactionCanceledException"
    assert photo._target("target")["photos"]["before"] == ["original"]
    assert table.get_item(Key={"session_id": session["session_id"]})["Item"]["phase"] == "completed"


def test_legacy_photo_committed_pending_session_recovery_avoids_duplicate_upload(finalized_upload):
    db, table, _, photo, s3, _, session = finalized_upload
    target = photo._target("target")
    target["photos"]["before"].append(session["photo_id"])
    db.tables["Targets"].put_item(Item=target)
    complete(finalized_upload)
    assert table.get_item(Key={"session_id": session["session_id"]})["Item"]["phase"] == "completed"
    s3.put_obj.assert_not_called()
    assert len(photo._target("target")["photos"]["before"]) == 2


@pytest.mark.parametrize("state", ["importing", "deleting"])
@pytest.mark.parametrize("operation", ["update", "update_status", "delete"])
def test_project_lock_atomically_blocks_membership_mutation(dynamodb_local, state, operation):
    db = dynamodb_local
    db.tables["Roles"].put_item(
        Item={
            "project_id": db.project_id,
            "user_id": "member",
            "role": "worker",
            "status": "active",
        }
    )
    role = RoleController(db.host, db.port)
    role.set_project_id(db.project_id)
    role.set_user_id("admin")
    db.tables["Projects"].update_item(
        Key={"project_id": db.project_id},
        UpdateExpression="SET #state = :state",
        ExpressionAttributeNames={"#state": "status"},
        ExpressionAttributeValues={":state": state},
    )
    with pytest.raises(ClientError) as failure:
        if operation == "delete":
            role.delete("member")
        else:
            getattr(role, operation)("guest" if operation == "update" else "reject", "member")
    assert failure.value.response["Error"]["Code"] == "TransactionCanceledException"
    assert (
        db.tables["Roles"].get_item(Key={"project_id": db.project_id, "user_id": "member"})["Item"][
            "role"
        ]
        == "worker"
    )
