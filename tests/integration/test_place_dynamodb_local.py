"""Real DynamoDB expression/transaction coverage; requires explicit local endpoint."""

import os
from io import BytesIO
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from PIL import Image

from harvest.controllers.place_controller import PlaceController
from harvest.controllers.target_controller import TargetController
from harvest.controllers.photo_controller import PhotoController
from harvest.exception import NotMeetRequirementsToDeleteError


def make_controller(kind, logical, db):
    controller = kind.__new__(kind)
    controller.logical_table_name = logical
    controller.client, controller.dynamodb = db.client, db.resource
    controller.project_id, controller.user_id = db.project_id, "actor"
    controller.set_partition_key("project_id")
    controller.set_sort_key("place_id" if logical == "Places" else "target_id")
    controller._DynamoDbDriver__table = db.resource.Table(os.environ["TABLE_" + logical.upper()])
    return controller


def setup(db):
    db.resource.Table(os.environ["TABLE_PROJECTS"]).put_item(
        Item={"project_id": db.project_id, "name": "Project"}
    )
    place = make_controller(PlaceController, "Places", db)
    place.create("Parent")
    parent_id = place.list(consistent_read=True)[0]["place_id"]
    target = make_controller(TargetController, "Targets", db)
    target.place = place
    target._TargetController__s3 = Mock()
    return place, target, parent_id


@pytest.mark.parametrize("kind", ["place", "target"])
@pytest.mark.parametrize("version", [None, "existing-token"])
def test_local_child_commits_between_child_check_and_parent_delete(dynamodb_local, kind, version):
    db = dynamodb_local
    place, target, parent_id = setup(db)
    if version:
        db.resource.Table(os.environ["TABLE_PLACES"]).update_item(
            Key={"project_id": db.project_id, "place_id": parent_id},
            UpdateExpression="SET children_version = :version",
            ExpressionAttributeValues={":version": version},
        )
    child = place if kind == "place" else target
    original = db.client.transact_write_items
    called = False

    def interleaved(**kwargs):
        nonlocal called
        if not called:
            called = True
            child.create("Child", parent_id)
        return original(**kwargs)

    proxy = Mock(wraps=db.client)
    proxy.transact_write_items.side_effect = interleaved
    place.client = target.client = proxy
    with pytest.raises(ClientError) as error:
        place.delete(parent_id)
    assert error.value.response["Error"]["Code"] == "TransactionCanceledException"
    assert place.show(parent_id)["children_version"] != version
    with pytest.raises(NotMeetRequirementsToDeleteError):
        place.delete(parent_id)


@pytest.mark.parametrize("kind", ["place", "target"])
def test_local_parent_removed_before_child_transaction_prevents_orphan(dynamodb_local, kind):
    db = dynamodb_local
    place, target, parent_id = setup(db)
    child = place if kind == "place" else target
    original = db.client.transact_write_items
    called = False

    def interleaved(**kwargs):
        nonlocal called
        if not called:
            called = True
            place.delete(parent_id)
        return original(**kwargs)

    proxy = Mock(wraps=db.client)
    proxy.transact_write_items.side_effect = interleaved
    place.client = target.client = proxy
    with pytest.raises(ClientError) as error:
        child.create("Child", parent_id)
    assert error.value.response["Error"]["Code"] == "TransactionCanceledException"
    assert place.list(consistent_read=True) == []
    assert target.list(consistent_read=True) == []


def test_local_target_photo_counters_and_empty_parent_deletion(dynamodb_local):
    db = dynamodb_local
    place, target, parent_id = setup(db)
    # Legacy rows have no version until their first child is created.
    assert "children_version" not in place.show(parent_id)
    place.create("Nested", parent_id)
    nested_id = next(
        item["place_id"] for item in place.list(consistent_read=True) if item["name"] == "Nested"
    )
    target.create("Target", nested_id)
    target_id = target.list(consistent_read=True)[0]["target_id"]
    assert place.show(parent_id)["photos"]["required"] == 1
    assert place.show(nested_id)["photos"]["required"] == 1
    photo = make_controller(PhotoController, "Targets", db)
    photo.place = place
    photo._PhotoController__s3 = Mock()
    output = BytesIO()
    Image.new("RGB", (2, 2)).save(output, format="PNG")
    photo.create(target_id, "before", output.getvalue(), photo_id="stable-photo")
    # Retried finalized photo never increments ancestor counts twice.
    photo.create(target_id, "before", output.getvalue(), photo_id="stable-photo")
    for parent in (parent_id, nested_id):
        assert place.show(parent)["photos"] == {"required": 1, "results": {"before": 1, "after": 0}}
    target.delete(target_id)
    for parent in (parent_id, nested_id):
        assert place.show(parent)["photos"] == {"required": 0, "results": {"before": 0, "after": 0}}
    target._TargetController__s3.delete_obj.assert_called_once_with("stable-photo")
    place.delete(nested_id)
    place.delete(parent_id)
    assert place.list(consistent_read=True) == []
    assert target.list(consistent_read=True) == []
