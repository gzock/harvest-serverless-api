import copy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from boto3.dynamodb.types import TypeDeserializer
from PIL import Image

from harvest.exception import ActionDeniedError, PhotoSaveLimitExceededError
from harvest.uploads import (
    UploadConflict,
    UploadExpired,
    UploadNotFound,
    UploadRateLimited,
    UploadService,
)


def conditional_error():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")


class SessionTable:
    """In-memory session store with the lease semantics exercised by retries."""

    def __init__(self):
        self.items = {}
        self.fail_completion = False
        self.updates = []
        self.name = "UploadSessions"
        self.meta = SimpleNamespace(client=self)
        self.transactions = []

    def transact_write_items(self, TransactItems, ClientRequestToken):
        self.transactions.append(copy.deepcopy(TransactItems))
        reasons = []
        for action in TransactItems:
            if "Update" in action:
                update = action["Update"]
                count = self.items.get(update["Key"]["session_id"], {}).get("count", 0)
                rejected = count >= update["ExpressionAttributeValues"][":limit"]
            else:
                rejected = TransactItems[-1]["Put"]["Item"]["session_id"] in self.items
            reasons.append({"Code": "ConditionalCheckFailed" if rejected else "None"})
        if any(reason["Code"] != "None" for reason in reasons):
            raise ClientError(
                {"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": reasons},
                "TransactWriteItems",
            )
        for action in TransactItems:
            if "Update" in action:
                update = action["Update"]
                key = update["Key"]["session_id"]
                quota = self.items.setdefault(key, {"session_id": key, "count": 0})
                quota["count"] += 1
                quota["ttl"] = update["ExpressionAttributeValues"][":ttl"]
            else:
                item = action["Put"]["Item"]
                self.items[item["session_id"]] = copy.deepcopy(item)
        return {}

    def put_item(self, Item, **kwargs):
        assert Item["session_id"] not in self.items
        self.items[Item["session_id"]] = copy.deepcopy(Item)

    def get_item(self, Key, **kwargs):
        item = self.items.get(Key["session_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
        self.updates.append(kwargs.get("ConditionExpression"))
        item = self.items[Key["session_id"]]
        values = ExpressionAttributeValues
        if "SET phase = :processing" in UpdateExpression:
            if (
                item["phase"] == "completed"
                or item.get("lease_until", 0) > values[":now"]
                or item.get("pinned_version", values[":version"]) != values[":version"]
            ):
                raise conditional_error()
            item.update(
                phase="processing",
                pinned_version=values[":version"],
                lease_token=values[":lease"],
                lease_until=values[":until"],
            )
        elif "SET phase = :completed" in UpdateExpression:
            if self.fail_completion:
                self.fail_completion = False
                raise ReadTimeoutError(endpoint_url="test")
            if item.get("lease_token") != values[":lease"]:
                raise conditional_error()
            item.update(phase="completed", completed_at=values[":now"])
            item.pop("lease_token", None)
            item.pop("lease_until", None)
        else:
            if item.get("lease_token") != values[":lease"] or item["phase"] == "completed":
                raise conditional_error()
            item.pop("lease_token", None)
            item.pop("lease_until", None)
        return {}


@pytest.fixture
def uploads(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET_NAME", "quarantine-bucket")
    table, s3 = SessionTable(), Mock()
    now = [1000]
    service = UploadService(table=table, s3=s3, clock=lambda: now[0])
    photo = Mock()
    photo._target.return_value = {"photos": {"before": [], "after": []}}

    def atomic_photo_create(*args, finalization, **kwargs):
        action = finalization["Update"]
        deserialize = TypeDeserializer().deserialize
        table.update_item(
            Key={key: deserialize(value) for key, value in action["Key"].items()},
            UpdateExpression=action["UpdateExpression"],
            ConditionExpression=action["ConditionExpression"],
            ExpressionAttributeValues={
                key: deserialize(value)
                for key, value in action["ExpressionAttributeValues"].items()
            },
        )

    photo.create.side_effect = atomic_photo_create
    service._authorize = Mock(return_value=photo)
    image = BytesIO()
    Image.new("RGB", (2, 2)).save(image, "JPEG")
    data = image.getvalue()
    s3.head_object.return_value = {
        "VersionId": "version-one",
        "ContentType": "image/jpeg",
        "ContentLength": len(data),
    }
    s3.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(data)}
    s3.generate_presigned_post.return_value = {
        "url": "https://upload.invalid",
        "fields": {"key": "scoped"},
    }
    return SimpleNamespace(service=service, table=table, s3=s3, photo=photo, now=now, data=data)


def create(uploads):
    return uploads.service.create(
        "actor-a", "project-a", "target-a", "before", "image/jpeg", len(uploads.data)
    )


def complete(uploads, session_id, **kwargs):
    return uploads.service.complete("actor-a", "project-a", "target-a", session_id, **kwargs)


def test_presigned_post_has_exact_size_type_and_server_key(uploads):
    result = create(uploads)
    request = uploads.s3.generate_presigned_post.call_args.kwargs
    assert request["Bucket"] == "quarantine-bucket"
    assert request["Key"].startswith("quarantine/project-a/target-a/")
    assert ["content-length-range", len(uploads.data), len(uploads.data)] in request["Conditions"]
    assert request["Fields"] == {"Content-Type": "image/jpeg"}
    assert result["expires_at"] == 1900


@pytest.mark.parametrize(
    "size,kind",
    [(0, "image/jpeg"), (4194305, "image/jpeg"), (True, "image/jpeg"), (1, "text/html")],
)
def test_invalid_upload_does_not_issue_presigned_policy(uploads, size, kind):
    with pytest.raises(ValueError):
        uploads.service.create("actor-a", "project-a", "target-a", "before", kind, size)
    uploads.s3.generate_presigned_post.assert_not_called()


def test_full_target_refuses_new_session(uploads):
    uploads.photo._target.return_value["photos"]["before"] = list(range(5))
    with pytest.raises(PhotoSaveLimitExceededError):
        create(uploads)
    assert not uploads.table.items


def test_finalize_pins_version_and_returns_stable_result(uploads):
    session = create(uploads)
    first = complete(uploads, session["session_id"])
    second = complete(uploads, session["session_id"])
    assert first == second == {"message": "successfully", "photo_id": session["photo_id"]}
    uploads.photo.create.assert_called_once()
    assert uploads.photo.create.call_args.args == ("target-a", "before", uploads.data)
    assert uploads.photo.create.call_args.kwargs["photo_id"] == session["photo_id"]
    completion = uploads.photo.create.call_args.kwargs["finalization"]["Update"]
    assert "lease_until > :now" in completion["ConditionExpression"]
    assert "phase = :processing" in completion["ConditionExpression"]
    assert uploads.s3.get_object.call_args.kwargs["VersionId"] == "version-one"


@pytest.mark.parametrize(
    "actor,project,target",
    [
        ("actor-b", "project-a", "target-a"),
        ("actor-a", "project-b", "target-a"),
        ("actor-a", "project-a", "target-b"),
    ],
)
def test_foreign_session_rejected_before_object_access(uploads, actor, project, target):
    session = create(uploads)
    with pytest.raises(ActionDeniedError):
        uploads.service.complete(actor, project, target, session["session_id"])
    uploads.s3.head_object.assert_not_called()
    uploads.photo.create.assert_not_called()


def test_unknown_session(uploads):
    with pytest.raises(UploadNotFound):
        complete(uploads, "not-found")
    uploads.s3.head_object.assert_not_called()


def test_expired_pending_session_cannot_finalize(uploads):
    session = create(uploads)
    uploads.now[0] = 1900
    with pytest.raises(UploadExpired):
        complete(uploads, session["session_id"])
    uploads.photo.create.assert_not_called()


@pytest.mark.parametrize(
    "head", [{"VersionId": "null"}, {"ContentLength": 999}, {"ContentType": "image/png"}]
)
def test_upload_metadata_mismatch_rejected(uploads, head):
    session = create(uploads)
    uploads.s3.head_object.return_value.update(head)
    with pytest.raises(ValueError):
        complete(uploads, session["session_id"])
    uploads.photo.create.assert_not_called()


def test_invalid_real_image_never_reaches_final_bucket(uploads):
    session = create(uploads)
    uploads.s3.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(b"x" * len(uploads.data))}
    with pytest.raises(ValueError):
        complete(uploads, session["session_id"])
    uploads.photo.create.assert_not_called()
    assert "lease_token" not in uploads.table.items[session["session_id"]]


def test_response_loss_retries_same_id_and_pinned_version_after_url_expiry(uploads):
    session = create(uploads)
    uploads.table.fail_completion = True
    with pytest.raises(ReadTimeoutError):
        complete(uploads, session["session_id"])
    uploads.now[0] = 1901
    result = complete(uploads, session["session_id"])
    assert result["photo_id"] == session["photo_id"]
    assert {call.kwargs["photo_id"] for call in uploads.photo.create.call_args_list} == {
        session["photo_id"]
    }
    assert uploads.s3.head_object.call_args.kwargs["VersionId"] == "version-one"


def test_version_cannot_be_changed_after_first_finalize_attempt(uploads):
    session = create(uploads)
    uploads.photo.create.side_effect = RuntimeError("retry")
    with pytest.raises(RuntimeError):
        complete(uploads, session["session_id"])
    with pytest.raises(UploadConflict):
        complete(uploads, session["session_id"], version_id="version-two")
    assert uploads.photo.create.call_count == 1


def test_active_finalize_lease_returns_conflict(uploads):
    session = create(uploads)
    uploads.table.items[session["session_id"]].update(
        lease_until=1050, phase="processing", pinned_version="version-one"
    )
    with pytest.raises(UploadConflict):
        complete(uploads, session["session_id"])
    uploads.photo.create.assert_not_called()


def test_finalization_rechecks_current_authorization(uploads):
    session = create(uploads)
    uploads.service._authorize.side_effect = ActionDeniedError()
    with pytest.raises(ActionDeniedError):
        complete(uploads, session["session_id"])
    uploads.s3.head_object.assert_not_called()


def test_target_quota_rejects_without_consuming_actor_quota_or_creating_session(uploads):
    uploads.service.target_window_limit = 2
    create(uploads)
    create(uploads)
    state = copy.deepcopy(uploads.table.items)
    with pytest.raises(UploadRateLimited):
        create(uploads)
    assert uploads.table.items == state
    assert uploads.s3.generate_presigned_post.call_count == 2


def test_actor_quota_spans_targets(uploads):
    uploads.service.actor_hourly_limit = 2
    for target in ("target-a", "target-b"):
        uploads.service.create(
            "actor-a", "project-a", target, "before", "image/jpeg", len(uploads.data)
        )
    with pytest.raises(UploadRateLimited):
        uploads.service.create(
            "actor-a", "project-a", "target-c", "before", "image/jpeg", len(uploads.data)
        )
    assert uploads.s3.generate_presigned_post.call_count == 2


def test_quota_window_reset_does_not_depend_on_ttl_deletion(uploads):
    uploads.service.target_window_limit = 1
    create(uploads)
    uploads.now[0] = 1800
    create(uploads)
    assert len([key for key in uploads.table.items if key.startswith("quota:target:")]) == 2
    assert uploads.s3.generate_presigned_post.call_count == 2


def test_quota_isolation_for_different_actors(uploads):
    uploads.service.target_window_limit = 1
    create(uploads)
    uploads.service.create(
        "actor-b", "project-a", "target-a", "before", "image/jpeg", len(uploads.data)
    )
    assert uploads.s3.generate_presigned_post.call_count == 2


def test_sdk_errors_are_not_misreported_as_quota_exhaustion(uploads):
    uploads.table.transact_write_items = Mock(
        side_effect=ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "TransactWriteItems"
        )
    )
    with pytest.raises(ClientError):
        create(uploads)
    uploads.s3.generate_presigned_post.assert_not_called()


def test_upload_transaction_native_values_match_boto_resource_contract(monkeypatch):
    import boto3
    from botocore.stub import Stubber, ANY

    table = boto3.resource(
        "dynamodb",
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    ).Table("UploadSessions")
    service = UploadService(table=table, s3=Mock(), clock=lambda: 1000)
    item = {
        "session_id": "12345678-1234-1234-1234-123456789abc",
        "actor_id": "actor",
        "project_id": "project",
        "target_id": "target",
        "ttl": 9000,
    }
    with Stubber(table.meta.client) as stubber:
        stubber.add_response(
            "transact_write_items",
            {},
            {"TransactItems": ANY, "ClientRequestToken": item["session_id"]},
        )
        service._store_session(item, 1000)
        stubber.assert_no_pending_responses()
