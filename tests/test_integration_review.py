"""Cross-layer regressions found while reviewing deletion and API integration."""

from unittest.mock import Mock
import pytest

from harvest.controllers.place_controller import PlaceController
from harvest.controllers.project_controller import ProjectController
from harvest.drivers.dynamodb_driver import DynamoDbDriver


def test_place_cleanup_reads_acknowledged_rows_from_base_table(monkeypatch):
    controller = PlaceController.__new__(PlaceController)
    controller.project_id = "project"
    controller.set_partition_key("project_id")
    controller.set_sort_key("place_id")
    table = Mock()
    row = {"project_id": "project", "place_id": "recent", "created_at": "2026-01-01T00:00:00Z"}
    table.query.side_effect = lambda **kwargs: {
        "Items": [row] if kwargs.get("ConsistentRead") else []
    }
    controller._DynamoDbDriver__table = table
    deletes = Mock()
    monkeypatch.setattr(DynamoDbDriver, "batch_del_item", deletes)
    controller.delete_all()
    deletes.assert_called_once_with([{"project_id": "project", "place_id": "recent"}])
    assert "IndexName" not in table.query.call_args.kwargs


def test_project_cleanup_reads_all_roles_before_atomic_owner_and_project_delete():
    controller = ProjectController.__new__(ProjectController)
    controller.project_id, controller.user_id = "project", "owner"
    controller.set_project_id = Mock()
    controller.update_item = Mock()
    controller.target, controller.place, controller.role, controller.client = (
        Mock(),
        Mock(),
        Mock(),
        Mock(),
    )
    controller.role.query.return_value = {
        "Items": [{"user_id": "owner"}, {"user_id": "just-joined"}]
    }
    controller.delete()
    controller.role.query.assert_called_once_with("project", consistent_read=True)
    controller.role.batch_del_item.assert_called_once_with(
        [{"project_id": "project", "user_id": "just-joined"}]
    )
    actions = controller.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(actions) == 2
    assert actions[0]["Delete"]["Key"]["user_id"] == {"S": "owner"}


def test_strong_reads_are_never_sent_to_global_secondary_indexes():
    driver = DynamoDbDriver.__new__(DynamoDbDriver)
    driver.set_partition_key("project_id")
    with pytest.raises(ValueError, match="base table"):
        driver.query("project", index="SomeGsi", consistent_read=True)


def test_import_lock_is_part_of_all_domain_write_conditions():
    from harvest.drivers.dynamodb_driver import active_project_check

    check = active_project_check("project")["ConditionCheck"]
    assert "#status <> :importing" in check["ConditionExpression"]
    assert "#status <> :deleting" in check["ConditionExpression"]
    assert check["ExpressionAttributeValues"][":importing"] == {"S": "importing"}


def test_project_metadata_write_respects_import_lock():
    controller = ProjectController.__new__(ProjectController)
    controller.project_id, controller.user_id = "project", "owner"
    controller.set_partition_key("project_id")
    table = controller._DynamoDbDriver__table = Mock()
    controller.update("name", "2026-01-01", "2026-12-01")
    request = table.update_item.call_args.kwargs
    assert "#status <> :importing" in request["ConditionExpression"]
    assert request["ExpressionAttributeValues"][":importing"] == "importing"


def test_deleting_import_locked_project_cannot_start_cleanup():
    from botocore.exceptions import ClientError

    controller = ProjectController.__new__(ProjectController)
    controller.project_id, controller.user_id = "project", "owner"
    controller.set_project_id = Mock()
    controller.target, controller.place, controller.role, controller.client = (
        Mock(),
        Mock(),
        Mock(),
        Mock(),
    )

    def check_lock(*args, **kwargs):
        assert "#status <> :importing" in kwargs["cond_exp"]
        # A deleting tombstone must remain retryable, but importing cannot become deleting.
        assert "#status <> :deleting" not in kwargs["cond_exp"]
        assert kwargs["exp_attr_values"][":importing"] == "importing"
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "importing"}},
            "UpdateItem",
        )

    controller.update_item = check_lock
    with pytest.raises(ClientError):
        controller.delete()
    controller.target.delete_all.assert_not_called()
    controller.place.delete_all.assert_not_called()
    controller.role.batch_del_item.assert_not_called()
    controller.client.transact_write_items.assert_not_called()
