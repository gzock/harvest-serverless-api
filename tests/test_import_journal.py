"""Stateful crash/retry tests for the durable import protocol (no AWS calls)."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
import pytest

from harvest.controllers.import_controller import ImportController, ImportInProgressError


def cancelled():
    return ClientError({"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems")


class MemoryDynamo:
    def __init__(self):
        self.rows = {
            "Projects": {("project",): {"project_id": "project"}},
            "Imports": {},
            "Places": {},
            "Targets": {},
        }
        self.calls = []
        self.fail_before = None
        self.fail_after = None
        self.crash_after = None
        self.deserialize = TypeDeserializer().deserialize

    @staticmethod
    def key(item):
        return tuple(
            item[k] for k in ("project_id", "operation_id", "place_id", "target_id") if k in item
        )

    def decode(self, item):
        return {k: self.deserialize(v) for k, v in item.items()}

    def Table(self, name):
        def get_item(**kwargs):
            assert kwargs["ConsistentRead"] is True
            return (
                {"Item": deepcopy(self.rows[name][self.key(kwargs["Key"])])}
                if self.key(kwargs["Key"]) in self.rows[name]
                else {}
            )

        def release(**kwargs):
            row = self.rows[name][self.key(kwargs["Key"])]
            values = kwargs["ExpressionAttributeValues"]
            if row.get("import_lease") != values[":lease"]:
                raise cancelled()
            row["import_lease_until"] = values[":expired"]

        return SimpleNamespace(get_item=get_item, update_item=release)

    def transact_write_items(self, TransactItems):
        self.calls.append(deepcopy(TransactItems))
        number = len(self.calls)
        if self.fail_before == number:
            raise RuntimeError("transport failure before transaction")
        rows = deepcopy(self.rows)
        for action in TransactItems:
            kind, args = next(iter(action.items()))
            table = args["TableName"]
            if kind == "Put":
                item = self.decode(args["Item"])
                key = self.key(item)
                if key in rows[table]:
                    raise cancelled()
                rows[table][key] = item
                continue
            key = self.key(self.decode(args["Key"]))
            row = rows[table][key]
            values = self.decode(args.get("ExpressionAttributeValues", {}))
            names = args.get("ExpressionAttributeNames", {})
            expression = args["UpdateExpression"]
            if table == "Projects":
                if ":now" in values:
                    if row.get("status") not in (None, "active") and not (
                        row.get("status") == "importing"
                        and row.get("import_operation") == values[":operation"]
                        and (
                            row.get("import_lease_until", 0) < values[":now"]
                            or row.get("import_lease") == values[":lease"]
                        )
                    ):
                        raise cancelled()
                    row.update(
                        status="importing",
                        import_operation=values[":operation"],
                        import_lease=values[":lease"],
                        import_lease_until=values[":until"],
                    )
                else:
                    if (
                        row.get("status") != "importing"
                        or row.get("import_operation") != values[":operation"]
                        or row.get("import_lease") != values[":lease"]
                    ):
                        raise cancelled()
                    if expression.startswith("REMOVE"):
                        for field in (
                            "status",
                            "import_operation",
                            "import_lease",
                            "import_lease_until",
                        ):
                            row.pop(field)
                    else:
                        row["import_lease_until"] = values[":until"]
            elif table == "Imports":
                if ":phase" in values:
                    if row["phase"] != values[":phase"] or row["cursor"] != values[":cursor"]:
                        raise cancelled()
                    row.update(phase=values[":next_phase"], cursor=values[":next_cursor"])
                if ":ttl" in values:
                    row["ttl"] = values[":ttl"]
            else:
                for i in range(len(names)):
                    if row.get(names[f"#f{i}"]) != values[f":v{i}"]:
                        raise cancelled()
                row.pop("import_pending")
        self.rows = rows
        if self.crash_after == number:
            raise KeyboardInterrupt("Lambda process terminated after commit")
        if self.fail_after == number:
            raise RuntimeError("transport failure after commit")
        return {}


def importer(db, digest="same-csv"):
    value = ImportController.__new__(ImportController)
    value.project_id = "project"
    value.import_digest = digest
    value.user_id = "admin"
    value.place_controller = SimpleNamespace(client=db, dynamodb=db)
    return value


def places(count):
    return [
        {
            "project_id": "project",
            "place_id": str(i),
            "name": f"Place {i}",
            "photos": {"required": 0, "results": {"before": 0, "after": 0}},
        }
        for i in range(count)
    ]


def test_300_rows_complete_with_bounded_atomic_progress_and_terminal_ttl():
    db = MemoryDynamo()
    result = importer(db).batch_create(places(300), [])
    assert result["imported"] == 300
    assert len(db.rows["Places"]) == 300
    assert all("import_pending" not in p for p in db.rows["Places"].values())
    assert db.rows["Projects"][("project",)] == {"project_id": "project"}
    assert db.rows["Imports"][("project", "same-csv")]["phase"] == "completed"
    assert all("ttl" in item for item in db.rows["Imports"].values())
    assert all(len(call) <= 77 for call in db.calls)
    assert not any("ttl" in str(call) for call in db.calls[:-1])


@pytest.mark.parametrize("failure", ["fail_before", "fail_after", "crash_after"])
def test_retry_preserves_ids_after_partial_commit_and_never_deletes(failure):
    db = MemoryDynamo()
    setattr(db, failure, 4)  # stage, lock, first batch, second batch
    with pytest.raises((RuntimeError, KeyboardInterrupt)):
        importer(db).batch_create(places(151), [])
    assert db.rows["Projects"][("project",)]["status"] == "importing"
    assert 75 <= len(db.rows["Places"]) <= 150
    assert all(row["import_pending"] for row in db.rows["Places"].values())
    assert all("ttl" not in row for row in db.rows["Imports"].values())
    # A new parser produces fresh IDs; only the original persisted plan is used.
    replacement = [{**row, "place_id": "new-" + row["place_id"]} for row in places(151)]
    db.rows["Projects"][("project",)]["import_lease_until"] = 0
    result = importer(db).batch_create(replacement, [])
    assert result["imported"] == 151
    assert {key[1] for key in db.rows["Places"]} == {str(i) for i in range(151)}
    assert not any("Delete" in action for call in db.calls for action in call)


def test_active_lease_and_other_csv_cannot_take_over_project():
    db = MemoryDynamo()
    db.crash_after = 3
    with pytest.raises(KeyboardInterrupt):
        importer(db).batch_create(places(151), [])
    with pytest.raises(ImportInProgressError):
        importer(db).batch_create(places(151), [])
    db.rows["Projects"][("project",)]["import_lease_until"] = 0
    with pytest.raises(ImportInProgressError):
        importer(db, digest="different-csv").batch_create(places(1), [])


def test_completed_response_loss_is_idempotent():
    db = MemoryDynamo()
    db.fail_after = 5  # stage + acquire + write + publish + finish
    with pytest.raises(RuntimeError):
        importer(db).batch_create(places(1), [])
    assert importer(db).batch_create(places(1), [])["imported"] == 1
    assert len(db.rows["Places"]) == 1


def test_snapshot_modified_outside_lock_is_never_overwritten_or_removed():
    db = MemoryDynamo()
    db.crash_after = 3
    with pytest.raises(KeyboardInterrupt):
        importer(db).batch_create(places(1), [])
    db.rows["Places"][("project", "0")]["photos"]["results"]["before"] = 1
    db.rows["Projects"][("project",)]["import_lease_until"] = 0
    with pytest.raises(ClientError):
        importer(db).resume("same-csv")
    assert db.rows["Places"][("project", "0")]["photos"]["results"]["before"] == 1
    assert db.rows["Projects"][("project",)]["status"] == "importing"


def test_stale_invocation_fenced_after_lease_takeover():
    db = MemoryDynamo()
    controller = importer(db)
    db.crash_after = 3
    with pytest.raises(KeyboardInterrupt):
        controller.batch_create(places(151), [])
    old = db.calls[2]
    db.rows["Projects"][("project",)]["import_lease"] = "new-worker"
    with pytest.raises(ClientError):
        db.transact_write_items(TransactItems=old)


def test_importing_projects_block_reads_unless_explicit_import_resume():
    from harvest.controllers.role_controller import RoleController

    role = RoleController.__new__(RoleController)
    role.project_id = "project"
    role.client = Mock()
    role.client.get_item.return_value = {
        "Item": {"project_id": {"S": "project"}, "status": {"S": "importing"}}
    }
    assert role.project_is_available() is False
    assert role.project_is_available(allow_deleting=True) is False
    assert role.project_is_available(allow_importing=True) is True


def test_recovery_worker_only_uses_persisted_operation_and_paginates(monkeypatch):
    import harvest.import_recovery as worker

    controller = Mock()
    table = controller.place_controller.dynamodb.Table.return_value
    table.get_item.return_value = {}
    table.scan.side_effect = [
        {
            "Items": [{"project_id": "project", "import_operation": "digest"}],
            "LastEvaluatedKey": {"project_id": "project"},
        },
        {"Items": []},
    ]
    monkeypatch.setattr(worker, "ImportController", lambda *_: controller)
    assert worker.handler({}, None) == {"recovered": 1, "failed": 0}
    assert controller.resume.call_args.args == ("digest",)
    assert callable(controller.resume.call_args.kwargs["should_continue"])
    controller.set_project_id.assert_called_once_with("project")
    assert table.scan.call_args.kwargs["ExclusiveStartKey"] == {"project_id": "project"}
    assert table.scan.call_args.kwargs["ConsistentRead"] is True


def test_recovery_worker_failure_is_observable_and_not_success(monkeypatch):
    import harvest.import_recovery as worker

    controller = Mock()
    controller.place_controller.dynamodb.Table.return_value.scan.return_value = {
        "Items": [{"project_id": "project", "import_operation": "digest"}]
    }
    controller.place_controller.dynamodb.Table.return_value.get_item.return_value = {}
    controller.resume.side_effect = RuntimeError("stored plan damaged")
    monkeypatch.setattr(worker, "ImportController", lambda *_: controller)
    with pytest.raises(RuntimeError, match="require retry"):
        worker.handler({}, None)


def test_recovery_bounded_pages_persist_cursor_for_next_run(monkeypatch):
    import harvest.import_recovery as worker

    controller = Mock()
    table = controller.place_controller.dynamodb.Table.return_value
    table.get_item.return_value = {"Item": {"cursor": {"project_id": "prior-page"}}}
    table.scan.return_value = {"Items": [], "LastEvaluatedKey": {"project_id": "next-page"}}
    monkeypatch.setattr(worker, "ImportController", lambda *_: controller)
    assert worker.handler({}, None)["continuation"] is True
    assert table.scan.call_count == 4
    assert table.scan.call_args_list[0].kwargs["ExclusiveStartKey"] == {"project_id": "prior-page"}
    assert all(c.kwargs["Limit"] == 25 for c in table.scan.call_args_list)
    assert table.put_item.call_args.kwargs["Item"]["cursor"] == {"project_id": "next-page"}


def test_recovery_low_time_preserves_page_for_retry(monkeypatch):
    import harvest.import_recovery as worker

    controller = Mock()
    table = controller.place_controller.dynamodb.Table.return_value
    table.get_item.return_value = {}
    table.scan.return_value = {"Items": [{"project_id": "project", "import_operation": "digest"}]}
    context = Mock()
    context.get_remaining_time_in_millis.side_effect = [25_000, 15_000]
    monkeypatch.setattr(worker, "ImportController", lambda *_: controller)
    assert worker.handler({}, context)["continuation"] is True
    table.put_item.assert_not_called()
    controller.resume.assert_not_called()


def test_busy_different_import_does_not_leave_staged_orphan():
    db = MemoryDynamo()
    db.crash_after = 3
    with pytest.raises(KeyboardInterrupt):
        importer(db).batch_create(places(151), [])
    with pytest.raises(ImportInProgressError):
        importer(db, "other-csv").batch_create(places(1), [])
    assert not any(key[1].startswith("other-csv") for key in db.rows["Imports"])


def test_chunk_budget_pause_keeps_journal_and_releases_lease_for_recovery():
    from harvest.controllers.import_controller import ImportPaused

    db = MemoryDynamo()
    db.crash_after = 3
    with pytest.raises(KeyboardInterrupt):
        importer(db).batch_create(places(151), [])
    db.rows["Projects"][("project",)]["import_lease_until"] = 0
    with pytest.raises(ImportPaused):
        importer(db).resume("same-csv", should_continue=lambda: False)
    assert db.rows["Projects"][("project",)]["import_lease_until"] == 0
    assert len(db.rows["Places"]) == 75
    assert importer(db).resume("same-csv")["imported"] == 151
