import os
import logging
from uuid import uuid5, NAMESPACE_URL
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from harvest.controllers.project_controller import ProjectController
from harvest.controllers.project_user_controller import ProjectUserController
from harvest.drivers.cognito_driver import CognitoDriver

DYNAMO_HOST = os.environ.get("DYNAMO_HOST")
DYNAMO_PORT = os.environ.get("DYNAMO_PORT")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID")
ADMINS = ["owner", "admin"]
CLEANUP_DAYS = 14


class AdoptedPhotos(TypedDict):
    before: str
    after: str


class RecordPhotos(TypedDict):
    before: list[str]
    after: list[str]
    adopt: AdoptedPhotos


class NotificationRecord(TypedDict, total=False):
    project_id: str
    user_id: str
    name: str
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str
    start_on: str
    complete_on: str
    status: str
    role: str
    photos: RecordPhotos


class NotificationRecipient(TypedDict, total=False):
    user_id: str
    role: str
    status: str


class BaseNotification:
    create = "{project_name}: {user_name}さんが{type}:{name}を追加しました"
    update = "{project_name}: {user_name}さんが{type}:{old_name}を{name}に変更しました"
    delete = "{project_name}: {user_name}さんが{type}:{name}を削除しました"
    message = ""
    is_insert_event = False
    is_modify_event = False
    is_remove_event = False
    formatter = logging.Formatter(
        "[%(levelname)s]\t%(asctime)s.%(msecs)dZ\t%(aws_request_id)s\t[%(module)s#%(funcName)s %(lineno)d]\t%(message)s"
    )
    logger = logging.getLogger()

    def __init__(self, stream_record, host, port):
        self.needs_strings_dict: dict[str, str] = dict(self.__dict__.get("needs_strings_dict", {}))
        self.message = ""
        self.notify_users: list[NotificationRecipient] | None = None
        self.is_insert_event = stream_record["eventName"] == "INSERT"
        self.is_modify_event = stream_record["eventName"] == "MODIFY"
        self.is_remove_event = stream_record["eventName"] == "REMOVE"
        self.event_id = stream_record["eventSourceARN"] + ":" + stream_record["eventID"]
        self.event_time = stream_record["dynamodb"].get("ApproximateCreationDateTime")
        self.project = ProjectController(host, port)
        self.users = ProjectUserController(host, port)
        self.cognito = CognitoDriver(COGNITO_USER_POOL_ID)
        self.set_stream_record(stream_record)
        self.record: NotificationRecord = (
            stream_record["dynamodb"].get("NewImage") or stream_record["dynamodb"]["OldImage"]
        )
        self.old_record: NotificationRecord = {}
        if stream_record["dynamodb"].get("OldImage"):
            self.old_record = stream_record["dynamodb"]["OldImage"]
        self.project_id = self.record["project_id"]
        self.user_id = self.record.get("updated_by", self.record.get("created_by", ""))
        self.logger = logging.getLogger(__name__)

    def set_stream_record(self, record):
        self.users.set_project_id(record["dynamodb"]["Keys"]["project_id"])

    def __get_project_name(self):
        try:
            project = self.project.show(self.project_id)
        except KeyError:
            project = {"name": "【不明】"}

        return project["name"]

    def __get_user_name(self):
        if not self.user_id:
            return "【不明】"
        try:
            detail = self.cognito.show_user(self.user_id)[0]["Attributes"]
            detail = {item["Name"]: item["Value"] for item in detail}
        except IndexError, KeyError:
            detail = {"preferred_username": "【不明】"}

        return detail.get("preferred_username") or "【不明】"

    def select_message(self):
        if self.is_insert_event:
            self.message = self.create

        elif self.is_modify_event:
            self.message = self.update

        elif self.is_remove_event:
            self.message = self.delete
        return self.message

    def mapping(self):
        self.message = self.message.format_map(self.needs_strings_dict)

    def set_record_type(self, record_type):
        self.needs_strings_dict = {"type": record_type}

    def get_notify_users(self, target, user_id=None):
        if target not in ["all", "admins", "specify"]:
            # TODO: needs custom exception
            raise ValueError
        self.logger.info("target notify type: %s" % str(target))

        project_users = self.users.list()
        project_users = [user for user in project_users if user.get("status", "active") == "active"]

        # TODO: needs delete myself user_id
        if target == "all":
            project_users = [user for user in project_users if user["user_id"] != self.user_id]
        elif target == "admins":
            # filterd_users = filter(lambda user: user["role"] in ADMINS, project_users)
            # project_users = [user for user in filterd_users]
            project_users = [
                user
                for user in project_users
                if user["role"] in ADMINS and user["user_id"] != self.user_id
            ]
        elif target == "specify":
            if not user_id:
                # TODO: needs custom exception
                raise ValueError
            project_users = [{"user_id": user_id}]

        return project_users

    def generate(self):
        if not self.message:
            self.select_message()

        ret = []
        if self.message:
            if self.notify_users is None:
                self.notify_users = self.get_notify_users("all")
            self.needs_strings_dict["project_name"] = self.__get_project_name()
            self.needs_strings_dict["user_name"] = self.__get_user_name()

            self.mapping()
            new_id = str(uuid5(NAMESPACE_URL, self.event_id))

            if isinstance(self.event_time, datetime):
                timestamp = self.event_time
            elif self.event_time is not None:
                timestamp = datetime.fromtimestamp(float(self.event_time), timezone.utc)
            else:
                from harvest.utils.sorters import parse_timestamp

                timestamp = parse_timestamp(
                    self.record.get("updated_at") or self.record["created_at"]
                )
            # Keep the existing wire format until all readers accept offsets.
            iso_now = timestamp.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
            ttl = int((timestamp + timedelta(days=CLEANUP_DAYS)).timestamp())
            for user in {user["user_id"]: user for user in self.notify_users}.values():
                ret.append(
                    {
                        "user_id": user["user_id"],
                        "notification_id": new_id,
                        "project_id": self.project_id,
                        "created_at": iso_now,
                        "updated_at": iso_now,
                        "message": self.message,
                        "read": False,
                        "ttl": ttl,
                    }
                )
        return ret
