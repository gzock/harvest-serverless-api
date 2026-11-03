"""Exercise actual DynamoDB expression validation and atomicity, never AWS."""

from base64 import b64encode
from copy import deepcopy
from io import BytesIO
from unittest.mock import Mock

from botocore.exceptions import ClientError
from PIL import Image
import pytest

from harvest.controllers.import_controller import (
    ImportController,
    ImportInProgressError,
    ImportPaused,
)
from harvest.controllers.role_controller import RoleController
from harvest.drivers.dynamodb_driver import active_project_check


def load_import(db, count=151):
    controller = ImportController(db.host, db.port)
    controller.set_project_id(db.project_id)
    controller.set_user_id("admin")
    text = "parent,type,name\n,place,Root\n" + "".join(
        f"Root,target,Target {i}\n" for i in range(count - 1)
    )
    controller.open_csv(b64encode(text.encode()).decode(), base64enc=True)
    controller.convert()
    return controller


def get(table, **key):
    return table.get_item(Key=key, ConsistentRead=True).get("Item")


def test_csv_300_rows_publish_and_finish_actual_transactions(dynamodb_local):
    db = dynamodb_local
    controller = load_import(db, 300)
    assert controller.batch_create()["imported"] == 300
    project = get(db.tables["Projects"], project_id=db.project_id)
    assert "status" not in project and "import_operation" not in project
    for table, expected in (("Places", 1), ("Targets", 299)):
        rows = db.tables[table].scan(ConsistentRead=True)["Items"]
        assert len(rows) == expected
        assert all("import_pending" not in row for row in rows)
    journal = db.tables["Imports"].scan(ConsistentRead=True)["Items"]
    assert len(journal) == 5
    assert all("ttl" in row for row in journal)
    assert (
        get(db.tables["Imports"], project_id=db.project_id, operation_id=controller.import_digest)[
            "phase"
        ]
        == "completed"
    )
    # A fresh parse generates new IDs, but retry uses the durable original plan.
    assert load_import(db, 300).batch_create()["imported"] == 300
    assert db.tables["Targets"].scan(Select="COUNT")["Count"] == 299


def test_csv_resume_after_partial_transaction_blocks_other_writers(dynamodb_local):
    db = dynamodb_local
    controller = load_import(db)
    original = controller._transact
    count = 0

    def stop_after_first_batch(actions):
        nonlocal count
        count += 1
        result = original(actions)
        if count == 3:
            raise KeyboardInterrupt("simulated process death after transaction commit")
        return result

    controller._transact = stop_after_first_batch
    with pytest.raises(KeyboardInterrupt):
        controller.batch_create()
    assert get(db.tables["Projects"], project_id=db.project_id)["status"] == "importing"
    assert db.tables["Targets"].scan(Select="COUNT")["Count"] == 74
    role = RoleController(db.host, db.port)
    role.set_project_id(db.project_id)
    assert role.project_is_available() is False
    assert role.project_is_available(allow_importing=True) is True
    with pytest.raises(ClientError) as failure:
        db.client.transact_write_items(TransactItems=[active_project_check(db.project_id)])
    assert failure.value.response["Error"]["Code"] == "TransactionCanceledException"
    with pytest.raises(ImportInProgressError):
        load_import(db).batch_create()
    db.tables["Projects"].update_item(
        Key={"project_id": db.project_id},
        UpdateExpression="SET import_lease_until = :expired",
        ExpressionAttributeValues={":expired": 0},
    )
    assert load_import(db).batch_create()["imported"] == 151
    assert db.tables["Targets"].scan(Select="COUNT")["Count"] == 150


def test_csv_publication_snapshot_conflict_preserves_modified_data(dynamodb_local):
    db = dynamodb_local
    controller = load_import(db, 2)
    original = controller._transact
    count = 0

    def stop_after_writes(actions):
        nonlocal count
        count += 1
        result = original(actions)
        if count == 3:
            raise KeyboardInterrupt()
        return result

    controller._transact = stop_after_writes
    with pytest.raises(KeyboardInterrupt):
        controller.batch_create()
    target = db.tables["Targets"].scan()["Items"][0]
    db.tables["Targets"].update_item(
        Key={"project_id": db.project_id, "target_id": target["target_id"]},
        UpdateExpression="SET #name = :value",
        ExpressionAttributeNames={"#name": "name"},
        ExpressionAttributeValues={":value": "Concurrent out-of-band edit"},
    )
    db.tables["Projects"].update_item(
        Key={"project_id": db.project_id},
        UpdateExpression="SET import_lease_until = :expired",
        ExpressionAttributeValues={":expired": 0},
    )
    with pytest.raises(ClientError) as failure:
        load_import(db, 2).batch_create()
    assert failure.value.response["Error"]["Code"] == "TransactionCanceledException"
    assert (
        get(db.tables["Targets"], project_id=db.project_id, target_id=target["target_id"])["name"]
        == "Concurrent out-of-band edit"
    )
    assert get(db.tables["Projects"], project_id=db.project_id)["status"] == "importing"


def test_photo_compare_and_swap_quota_and_parent_counts(dynamodb_local, monkeypatch):
    from harvest.controllers import photo_controller as photos_module
    from harvest.exception import PhotoSaveLimitExceededError

    db = dynamodb_local
    s3 = Mock()
    monkeypatch.setattr(photos_module, "S3Driver", lambda *_: s3)
    photos = photos_module.PhotoController(db.host, db.port)
    photos.set_project_id(db.project_id)
    photos.set_user_id("reporter")
    db.tables["Places"].put_item(
        Item={
            "project_id": db.project_id,
            "place_id": "parent",
            "hierarchy": db.project_id,
            "photos": {"required": 1, "results": {"before": 0, "after": 0}},
        }
    )
    initial = {
        "project_id": db.project_id,
        "target_id": "target",
        "parent_place_id": "parent",
        "photos": {"before": [], "after": [], "adopt": {"before": None, "after": None}},
    }
    db.tables["Targets"].put_item(Item=initial)
    stream = BytesIO()
    Image.new("RGB", (2, 2)).save(stream, "JPEG")
    photos.create("target", "before", stream.getvalue(), photo_id="photo-1")
    assert (
        get(db.tables["Places"], project_id=db.project_id, place_id="parent")["photos"]["results"][
            "before"
        ]
        == 1
    )
    stale = deepcopy(initial["photos"])
    stale["before"].append("losing-race")
    with pytest.raises(ClientError) as failure:
        db.client.transact_write_items(
            **photos._transaction(initial, stale, increment_type="before")
        )
    assert failure.value.response["Error"]["Code"] == "TransactionCanceledException"
    assert (
        get(db.tables["Places"], project_id=db.project_id, place_id="parent")["photos"]["results"][
            "before"
        ]
        == 1
    )
    for i in range(2, 6):
        photos.create("target", "before", stream.getvalue(), photo_id=f"photo-{i}")
    with pytest.raises(PhotoSaveLimitExceededError):
        photos.create("target", "before", stream.getvalue(), photo_id="photo-6")
    assert s3.put_obj.call_count == 5
    photos.update_adopt("target", "before", "photo-1")
    photos.delete("target", "photo-2")
    assert (
        len(
            get(db.tables["Targets"], project_id=db.project_id, target_id="target")["photos"][
                "before"
            ]
        )
        == 4
    )


def test_csv_scheduled_recovery_scan_checkpoint_and_completion(dynamodb_local, monkeypatch):
    import harvest.import_recovery as recovery

    db = dynamodb_local
    controller = load_import(db, 151)
    original = controller._transact
    count = 0

    def crash(actions):
        nonlocal count
        count += 1
        result = original(actions)
        if count == 3:
            raise KeyboardInterrupt()
        return result

    controller._transact = crash
    with pytest.raises(KeyboardInterrupt):
        controller.batch_create()
    db.tables["Projects"].update_item(
        Key={"project_id": db.project_id},
        UpdateExpression="SET import_lease_until = :expired",
        ExpressionAttributeValues={":expired": 0},
    )
    fresh = ImportController(db.host, db.port)
    monkeypatch.setattr(recovery, "ImportController", lambda *_: fresh)
    assert recovery.handler({}, None) == {"recovered": 1, "failed": 0}
    assert (
        get(db.tables["Imports"], project_id="__import_recovery__", operation_id="scan_cursor")[
            "cursor"
        ]
        == {}
    )
    assert get(db.tables["Projects"], project_id=db.project_id).get("status") is None
    assert db.tables["Targets"].scan(Select="COUNT")["Count"] == 150


def test_csv_other_payload_rejected_without_orphan_and_budget_pause_resumable(dynamodb_local):
    db = dynamodb_local
    controller = load_import(db, 2)
    entries = [{"table": "Places", "key": "place_id", "item": dict(controller.places[0])}]
    entries[0]["item"].update(import_operation=controller.import_digest, import_pending=True)
    _, lease = controller._stage(entries, controller.import_digest)
    with pytest.raises(ImportInProgressError):
        load_import(db, 3).batch_create()
    assert db.tables["Imports"].scan(Select="COUNT")["Count"] == 2
    with pytest.raises(ImportPaused):
        controller.resume(controller.import_digest, _lease=lease, should_continue=lambda: False)
    assert get(db.tables["Projects"], project_id=db.project_id)["import_lease_until"] == 0
    assert controller.resume(controller.import_digest)["imported"] == 1
