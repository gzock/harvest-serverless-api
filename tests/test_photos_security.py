import copy
from io import BytesIO
from unittest.mock import Mock

import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError, ReadTimeoutError
from harvest.controllers.photo_controller import PhotoController
from harvest.drivers.dynamodb_driver import DynamoDbDriver
from harvest.exception import (
    AdoptedPhotoIsCanNotDeleteError,
    LastOnePhotoIsCanNotDeleteError,
    PhotoIsNotFoundError,
    PhotoSaveLimitExceededError,
)
from PIL import Image


@pytest.fixture
def photo(monkeypatch):
    target = {
        "project_id": "project-a",
        "target_id": "target-a",
        "parent_place_id": "project-a",
        "photos": {"before": ["p1", "p2"], "after": [], "adopt": {"before": "p1"}},
    }
    obj = PhotoController.__new__(PhotoController)
    obj.project_id, obj.target_id, obj.user_id = "project-a", "target-a", "user-a"
    obj.client, obj.place, obj._PhotoController__s3 = Mock(), Mock(), Mock()
    monkeypatch.setattr(
        DynamoDbDriver, "get_item", lambda *args, **kwargs: {"Item": copy.deepcopy(target)}
    )
    return obj, target, obj._PhotoController__s3


@pytest.fixture
def jpeg():
    stream = BytesIO()
    Image.new("RGB", (2, 2)).save(stream, "JPEG")
    return stream.getvalue()


def test_foreign_photo_never_reaches_s3(photo):
    obj, target, s3 = photo
    with pytest.raises(PhotoIsNotFoundError):
        obj.show("foreign")
    s3.get_obj_bytes.assert_not_called()


def test_foreign_or_wrong_type_adoption_never_writes(photo):
    obj, target, s3 = photo
    for kind, key in [("before", "foreign"), ("after", "p1")]:
        with pytest.raises(PhotoIsNotFoundError):
            obj.update_adopt("target-a", kind, key)
    obj.client.transact_write_items.assert_not_called()


def test_limit_checked_before_upload(photo, jpeg):
    obj, target, s3 = photo
    target["photos"]["before"] = [str(i) for i in range(5)]
    with pytest.raises(PhotoSaveLimitExceededError):
        obj.create("target-a", "before", jpeg)
    s3.put_obj.assert_not_called()


def test_parallel_stale_upload_compensated_without_lost_update(photo, jpeg):
    obj, target, s3 = photo
    obj.client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems"
    )
    with pytest.raises(ClientError):
        obj.create("target-a", "before", jpeg)
    key = s3.put_obj.call_args.args[0]
    s3.delete_obj.assert_called_once_with(key)
    write = obj.client.transact_write_items.call_args.kwargs["TransactItems"][1]["Update"]
    assert "photos = :expected" in write["ConditionExpression"]
    decode = TypeDeserializer().deserialize
    assert decode(write["ExpressionAttributeValues"][":expected"]) == target["photos"]
    assert len(decode(write["ExpressionAttributeValues"][":photos"])["before"]) == 3


def test_unknown_transaction_outcome_keeps_image(photo, jpeg):
    obj, target, s3 = photo
    obj.client.transact_write_items.side_effect = ReadTimeoutError(endpoint_url="test")
    with pytest.raises(ReadTimeoutError):
        obj.create("target-a", "before", jpeg)
    s3.delete_obj.assert_not_called()


def test_first_photo_counter_and_target_update_are_atomic(photo, jpeg):
    obj, target, s3 = photo
    target["parent_place_id"] = "place-a"
    obj.place.show.return_value = {"hierarchy": "project-a#parent-a"}
    obj.create("target-a", "after", jpeg)
    writes = obj.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(writes) == 4
    assert writes[2]["Update"]["Key"]["place_id"] == {"S": "parent-a"}
    assert writes[3]["Update"]["Key"]["place_id"] == {"S": "place-a"}


def test_adopted_and_last_photo_cannot_be_deleted(photo):
    obj, target, s3 = photo
    with pytest.raises(AdoptedPhotoIsCanNotDeleteError):
        obj.delete("target-a", "p1")
    target["photos"]["before"] = ["p1"]
    with pytest.raises(LastOnePhotoIsCanNotDeleteError):
        obj.delete("target-a", "p1")
    obj.client.transact_write_items.assert_not_called()
    s3.delete_obj.assert_not_called()


def test_invalid_image_rejected_before_upload(photo):
    obj, target, s3 = photo
    with pytest.raises(ValueError):
        obj.create("target-a", "before", b"not-an-image")
    s3.put_obj.assert_not_called()


def test_deleting_target_rejects_upload_before_s3(photo, jpeg):
    obj, target, s3 = photo
    target["status"] = "deleting"
    with pytest.raises(PhotoIsNotFoundError):
        obj.create("target-a", "before", jpeg)
    s3.put_obj.assert_not_called()


def test_broken_parent_rejects_before_upload(photo, jpeg):
    obj, target, s3 = photo
    target["parent_place_id"] = "missing-place"
    obj.place.show.side_effect = ValueError("Parent missing")
    with pytest.raises(ValueError):
        obj.create("target-a", "after", jpeg)
    s3.put_obj.assert_not_called()


def test_stable_photo_id_replay_does_not_upload_or_append(photo, jpeg):
    obj, target, s3 = photo
    assert obj.create("target-a", "before", jpeg, photo_id="p1") == {"message": "successfully"}
    s3.put_obj.assert_not_called()
    obj.client.transact_write_items.assert_not_called()


def test_concurrent_stable_id_failure_does_not_delete_committed_image(photo, jpeg):
    obj, target, s3 = photo
    obj.client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems"
    )
    with pytest.raises(ClientError):
        obj.create("target-a", "before", jpeg, photo_id="session-photo")
    s3.delete_obj.assert_not_called()
