from importlib import import_module
from unittest.mock import Mock
from copy import deepcopy
import pytest
from botocore.exceptions import ClientError
from harvest.factories.notification_factory import NotificationFactory
from harvest.controllers.notification_controller import NotificationController
from harvest.commons.notifications.base_notification import BaseNotification
from harvest.commons.notifications.photo_notification import PhotoNotification


def test_stream_decoder_preserves_nested_types_and_original_input():
    record = {
        "dynamodb": {
            "NewImage": {
                "photos": {
                    "M": {
                        "before": {"L": [{"S": "photo"}]},
                        "adopt": {"M": {"before": {"NULL": True}}},
                    }
                }
            }
        }
    }
    original = deepcopy(record)
    decoded = NotificationFactory(None, None).serialize(record)
    assert decoded["dynamodb"]["NewImage"]["photos"] == {
        "before": ["photo"],
        "adopt": {"before": None},
    }
    assert record == original


def test_notification_retry_preserves_already_read_item(monkeypatch):
    controller = NotificationController.__new__(NotificationController)
    controller.put_item = Mock(
        side_effect=ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem"
        )
    )
    controller.batch_create([{"notification_id": "event-id", "read": False}])
    assert (
        controller.put_item.call_args.kwargs["cond_exp"] == "attribute_not_exists(notification_id)"
    )


def test_notification_write_failure_propagates():
    controller = NotificationController.__new__(NotificationController)
    controller.put_item = Mock(
        side_effect=ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "retry"}},
            "PutItem",
        )
    )
    with pytest.raises(ClientError):
        controller.batch_create([{"notification_id": "event-id"}])


def test_dispatcher_returns_sequence_number_for_failed_record(monkeypatch):
    # Handler lives outside the shared package until final src-layout migration.
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    module = import_module("harvest.handlers.notifications.dispatcher")
    controller = Mock()
    controller.batch_create.side_effect = [None, RuntimeError("write failed"), None]
    monkeypatch.setattr(module, "NotificationController", lambda *_: controller)
    factory = Mock()
    factory.generate.return_value = [{"notification_id": "event-id"}]
    monkeypatch.setattr(module, "NotificationFactory", lambda *_: factory)
    event = {
        "Records": [{"eventID": str(i), "dynamodb": {"SequenceNumber": str(i)}} for i in range(3)]
    }
    assert module.lambda_handler(event, None) == {"batchItemFailures": [{"itemIdentifier": "1"}]}
    assert controller.batch_create.call_count == 3


def test_notifications_are_stable_and_do_not_share_instance_state(monkeypatch):
    import harvest.commons.notifications.base_notification as module

    monkeypatch.setattr(module, "ProjectController", lambda *_: Mock())
    monkeypatch.setattr(module, "ProjectUserController", lambda *_: Mock())
    monkeypatch.setattr(module, "CognitoDriver", lambda *_: Mock())

    def make(event_id):
        record = {
            "eventID": event_id,
            "eventSourceARN": "arn:aws:dynamodb:x:1:table/Targets/stream/x",
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {"project_id": "project"},
                "ApproximateCreationDateTime": 1000,
                "NewImage": {
                    "project_id": "project",
                    "updated_by": "actor",
                    "created_at": "2026-01-01T00:00:00",
                },
                "OldImage": {},
            },
        }
        obj = BaseNotification(record, None, None)
        obj.message = "message"
        obj.notify_users = [{"user_id": "recipient"}]
        obj._BaseNotification__get_project_name = lambda: "project"
        obj._BaseNotification__get_user_name = lambda: "actor"
        return obj

    first, second = make("same-event"), make("same-event")
    first.needs_strings_dict["private"] = "one"
    assert "private" not in second.needs_strings_dict
    assert first.generate()[0]["notification_id"] == second.generate()[0]["notification_id"]
    assert first.generate()[0]["created_at"] == second.generate()[0]["created_at"]


def test_photo_notification_uses_deserialized_list():
    item = PhotoNotification.__new__(PhotoNotification)
    item.is_insert_event = item.is_remove_event = False
    item.is_modify_event = True
    item.old_record = {
        "photos": {"before": [], "after": [], "adopt": {"before": None, "after": None}}
    }
    item.record = {
        "photos": {"before": ["photo"], "after": [], "adopt": {"before": "photo", "after": None}}
    }
    assert item.select_message() == item.shot_before_photo


def test_notification_bulk_updates_deduplicate_and_support_single_id():
    controller = NotificationController.__new__(NotificationController)
    controller.user_id = "user"
    controller._DynamoDbDriver__table = Mock()
    controller.set_partition_key("user_id")
    controller.set_sort_key("notification_id")
    controller.update(True, notification_ids=["one", "one", "two"])
    assert controller._DynamoDbDriver__table.update_item.call_count == 2


@pytest.mark.parametrize("old_pending,new_pending", [(False, True), (True, True), (True, False)])
@pytest.mark.parametrize("table", ["Places", "Targets"])
def test_import_pending_rows_never_emit_notifications(monkeypatch, old_pending, new_pending, table):
    import harvest.factories.notification_factory as module

    constructor = Mock(
        side_effect=AssertionError("Import events must not instantiate notifications")
    )
    monkeypatch.setattr(module, "PlaceNotification", constructor)
    monkeypatch.setattr(module, "TargetNotification", constructor)
    record = {
        "eventSourceARN": f"arn:aws:dynamodb:x:1:table/{table}/stream/x",
        "dynamodb": {
            "OldImage": {"import_pending": {"BOOL": old_pending}},
            "NewImage": {"import_pending": {"BOOL": new_pending}},
        },
    }
    assert NotificationFactory(None, None, record).generate() == []
    constructor.assert_not_called()


def test_normal_domain_write_still_emits_notifications(monkeypatch):
    import harvest.factories.notification_factory as module

    constructor = Mock()
    constructor.return_value.generate.return_value = [{"notification_id": "normal"}]
    monkeypatch.setattr(module, "TargetNotification", constructor)
    record = {
        "eventSourceARN": "arn:aws:dynamodb:x:1:table/Targets/stream/x",
        "dynamodb": {
            "NewImage": {"target_id": {"S": "normal"}},
        },
    }
    assert NotificationFactory(None, None, record).generate() == [{"notification_id": "normal"}]


def test_notification_actor_without_optional_display_name_still_delivers():
    notification = BaseNotification.__new__(BaseNotification)
    notification.user_id = "legacy-user"
    notification.cognito = Mock()
    notification.cognito.show_user.return_value = [
        {"Attributes": [{"Name": "email", "Value": "legacy@example.invalid"}]}
    ]
    assert notification._BaseNotification__get_user_name() == "【不明】"


def test_notification_timestamp_fallback_accepts_updated_only_record():
    notification = BaseNotification.__new__(BaseNotification)
    notification.message = "message"
    notification.notify_users = [{"user_id": "recipient"}]
    notification.needs_strings_dict = {}
    notification.event_id = "legacy-event"
    notification.project_id = "project"
    notification.event_time = None
    notification.record = {"updated_at": "2026-01-01T00:00:00"}
    notification._BaseNotification__get_project_name = lambda: "project"
    notification._BaseNotification__get_user_name = lambda: "actor"
    result = notification.generate()
    assert result[0]["created_at"] == "2026-01-01T00:00:00"
