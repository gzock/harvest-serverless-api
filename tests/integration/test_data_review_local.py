"""Data-path compatibility and TTL lifecycle tests on explicit loopback DynamoDB."""

from base64 import b64encode

import pytest

from harvest.controllers.import_controller import ImportController
from harvest.controllers.project_controller import ProjectController
from harvest.controllers.role_controller import RoleController


def load_csv(db):
    controller = ImportController(db.host, db.port)
    controller.set_project_id(db.project_id)
    controller.set_user_id("admin")
    controller.open_csv(
        b64encode(b"parent,type,name\n,place,Root\nRoot,target,Target\n").decode(), base64enc=True
    )
    controller.convert()
    return controller


@pytest.mark.parametrize("ttl_cleanup", ["not_started", "metadata_first", "chunks_first"])
def test_expired_import_can_restart_independently_of_ttl_cleanup_order(
    dynamodb_local, monkeypatch, ttl_cleanup
):
    db = dynamodb_local
    monkeypatch.setattr("harvest.controllers.import_controller.time.time", lambda: 1000)
    first = load_csv(db)
    first.batch_create()
    journal = db.tables["Imports"]
    records = journal.scan(ConsistentRead=True)["Items"]
    for record in records:
        is_meta = record["operation_id"] == first.import_digest
        if (
            ttl_cleanup == "metadata_first"
            and is_meta
            or ttl_cleanup == "chunks_first"
            and not is_meta
        ):
            journal.delete_item(
                Key={"project_id": db.project_id, "operation_id": record["operation_id"]}
            )
    monkeypatch.setattr(
        "harvest.controllers.import_controller.time.time",
        lambda: 1000 + ImportController.RETENTION_SECONDS + 1,
    )
    second = load_csv(db)
    assert second.batch_create()["imported"] == 2
    assert db.tables["Places"].scan(ConsistentRead=True, Select="COUNT")["Count"] == 2
    assert db.tables["Targets"].scan(ConsistentRead=True, Select="COUNT")["Count"] == 2
    latest = journal.get_item(
        Key={"project_id": db.project_id, "operation_id": first.import_digest}, ConsistentRead=True
    )["Item"]
    assert latest["phase"] == "completed"
    assert latest["ttl"] == 1001 + 2 * ImportController.RETENTION_SECONDS


def test_list_projects_uses_legacy_user_roles_index(dynamodb_local):
    db = dynamodb_local
    db.tables["Projects"].put_item(
        Item={"project_id": db.project_id, "name": "Visible", "created_at": "2026-11-01T00:00:00"}
    )
    db.tables["Roles"].put_item(
        Item={"project_id": db.project_id, "user_id": "actor", "role": "owner", "status": "active"}
    )
    roles = RoleController(db.host, db.port)
    roles.set_user_id("actor")
    controller = ProjectController.__new__(ProjectController)
    controller.user_id, controller.role, controller.dynamodb = "actor", roles, db.resource
    projects = controller.list_projects()
    assert len(projects) == 1
    assert projects[0]["project_id"] == db.project_id
    assert projects[0]["role"] == "owner"
    assert len(projects[0]["project_code"]) == 22


def test_expired_import_concurrent_restart_preserves_one_durable_plan(dynamodb_local, monkeypatch):
    """A losing restart must resume the winning plan, never mix generated IDs."""
    from botocore.exceptions import ClientError
    from harvest.controllers.import_controller import ImportInProgressError

    db = dynamodb_local
    monkeypatch.setattr("harvest.controllers.import_controller.time.time", lambda: 1000)
    original = load_csv(db)
    original.batch_create()
    monkeypatch.setattr(
        "harvest.controllers.import_controller.time.time",
        lambda: 1001 + ImportController.RETENTION_SECONDS,
    )
    losing = load_csv(db)
    winning = load_csv(db)
    apply_winning = winning._transact
    stale_lease = "stale-previous-execution"
    original_losing_transact = losing._transact
    interleaved = False

    def crash_after_staging(actions):
        apply_winning(actions)
        raise KeyboardInterrupt("worker died after atomic stage")

    winning._transact = crash_after_staging

    def race(actions):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            with pytest.raises(KeyboardInterrupt):
                winning.batch_create()
        return original_losing_transact(actions)

    losing._transact = race
    with pytest.raises(ImportInProgressError):
        losing.batch_create()
    journal = db.tables["Imports"]
    operation = original.import_digest
    meta = journal.get_item(
        Key={"project_id": db.project_id, "operation_id": operation}, ConsistentRead=True
    )["Item"]
    chunk = journal.get_item(
        Key={"project_id": db.project_id, "operation_id": operation + "#0"}, ConsistentRead=True
    )["Item"]
    assert meta["phase"] == "writing" and "ttl" not in meta and "ttl" not in chunk
    plan_ids = {
        entry["item"].get("place_id") for entry in chunk["entries"] if entry["table"] == "Places"
    }
    assert plan_ids == {winning.places[0]["place_id"]}
    assert losing.places[0]["place_id"] not in plan_ids
    # Even matching phase/cursor in a reused digest cannot revive an old worker.
    with pytest.raises(ClientError):
        original_losing_transact(
            [
                original._project_action(operation, stale_lease),
                original._progress(operation, meta, "publishing", 0),
            ]
        )
    db.tables["Projects"].update_item(
        Key={"project_id": db.project_id},
        UpdateExpression="SET import_lease_until = :expired",
        ExpressionAttributeValues={":expired": 0},
    )
    assert losing.batch_create()["imported"] == 2
    rows = db.tables["Places"].scan(ConsistentRead=True)["Items"]
    assert {row["place_id"] for row in rows} == {
        original.places[0]["place_id"],
        winning.places[0]["place_id"],
    }
