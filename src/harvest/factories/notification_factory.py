from copy import deepcopy
from boto3.dynamodb.types import TypeDeserializer

from harvest.commons.notifications.project_notification import ProjectNotification
from harvest.commons.notifications.place_notification import PlaceNotification
from harvest.commons.notifications.target_notification import TargetNotification
from harvest.commons.notifications.photo_notification import PhotoNotification
from harvest.commons.notifications.project_user_notification import ProjectUserNotification


class NotificationFactory:
    def __init__(self, host, port, stream_record=None):
        self.host, self.port = host, port
        if stream_record is not None:
            self.set_stream_record(stream_record)

    def serialize(self, stream_record):
        record = deepcopy(stream_record)
        deserialize = TypeDeserializer().deserialize
        for field in ("NewImage", "OldImage", "Keys"):
            if field in record["dynamodb"]:
                record["dynamodb"][field] = {
                    key: deserialize(value) for key, value in record["dynamodb"][field].items()
                }
        return record

    def set_stream_record(self, stream_record):
        self.__stream_record = self.serialize(stream_record)

    def generate(self):
        record = self.__stream_record
        table = record["eventSourceARN"].split(":")[5].split("/")[1]
        # Resolve environment-specific table names before selecting domain events.
        from harvest.drivers.dynamodb_driver import resolve_table_name

        types = {
            resolve_table_name("Projects"): ProjectNotification,
            resolve_table_name("Places"): PlaceNotification,
            resolve_table_name("Targets"): TargetNotification,
            resolve_table_name("Roles"): ProjectUserNotification,
        }
        notification_type = types.get(table)
        if notification_type is None:
            raise ValueError("Unexpected notification source table")
        old = record["dynamodb"].get("OldImage", {})
        new = record["dynamodb"].get("NewImage", {})
        # Imported rows stay private until the journal commits. Also suppress the
        # final removal of the staging marker, whose old image is still pending.
        if old.get("import_pending") is True or new.get("import_pending") is True:
            return []
        if (
            notification_type is TargetNotification
            and old.get("photos") != new.get("photos")
            and old
            and new
        ):
            notification_type = PhotoNotification
        return notification_type(record, self.host, self.port).generate()
