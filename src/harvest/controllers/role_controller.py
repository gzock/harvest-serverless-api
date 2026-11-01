import logging
import os
from datetime import datetime

from boto3.dynamodb.types import TypeSerializer
from harvest.drivers.dynamodb_driver import (
    DynamoDbDriver,
    active_project_check,
    resolve_table_name,
)
from harvest.exception import ActionDeniedError


class RoleController(DynamoDbDriver):
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    user_id = None
    project_id = None

    def __init__(self, host, port):
        super().__init__(host, port, "Roles")
        super().set_partition_key("project_id")
        super().set_sort_key("user_id")

    def set_user_id(self, user_id):
        self.user_id = str(user_id) if user_id else None
        if self.user_id and self.project_id:
            return self.show()

    def set_project_id(self, project_id):
        self.project_id = str(project_id) if project_id else None
        if self.user_id and self.project_id:
            return self.show()

    def project_is_available(self, allow_deleting=False, allow_importing=False):
        if not self.project_id:
            return False
        result = self.client.get_item(
            TableName=os.environ.get("TABLE_PROJECTS", "Projects"),
            Key={"project_id": {"S": self.project_id}},
            ConsistentRead=True,
            ProjectionExpression="project_id, #status",
            ExpressionAttributeNames={"#status": "status"},
        ).get("Item")
        state = result.get("status", {}).get("S") if result else None
        return (
            bool(result)
            and (allow_deleting or state != "deleting")
            and (allow_importing or state != "importing")
        )

    def list_users(self):
        if not self.project_id:
            raise ValueError
        return super().query(self.project_id)["Items"]

    def list_active_users(self):
        users = self.list_users()
        return [user for user in users if user.get("status", "active") == "active"]

    def list_projects(self):
        if not self.user_id:
            raise ValueError
        super().set_partition_key("user_id")
        super().set_sort_key(None)
        ret = super().query(self.user_id, index="UserRolesIndex")
        super().set_partition_key("project_id")
        super().set_sort_key("user_id")
        return ret["Items"]

    def show(self, user_id=None):
        if not self.project_id:
            raise ValueError

        if not user_id:
            user_id = self.user_id

        # user_idを使って権限チェック
        ret = super().get_item(self.project_id, user_id, consistent_read=True)

        if "Item" in ret:
            return ret["Item"]

    def create(self, role, user_id=None, project_id=None, status=None):
        if role not in ("guest", "reporter", "worker", "admin", "owner"):
            raise ValueError

        if not user_id:
            user_id = self.user_id

        if not project_id:
            project_id = self.project_id

        if not status:
            status = "active"

        now = datetime.now().isoformat()
        item = {
            "project_id": project_id,
            "user_id": user_id,
            "role": role,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "created_by": self.user_id,
            "updated_by": self.user_id,
        }
        serialize = TypeSerializer().serialize
        return self.client.transact_write_items(
            TransactItems=[
                active_project_check(project_id),
                {
                    "Put": {
                        "TableName": resolve_table_name("Roles"),
                        "Item": {key: serialize(value) for key, value in item.items()},
                        "ConditionExpression": "attribute_not_exists(project_id)",
                    }
                },
            ]
        )

    def update(self, role, user_id=None):
        if role not in ("guest", "reporter", "worker", "admin"):
            raise ActionDeniedError()
        if not self.project_id:
            raise ValueError

        if not user_id:
            user_id = self.user_id

        now = datetime.now().isoformat()
        return super().guarded_update_item(
            partition_key=self.project_id,
            sort_key=user_id,
            update_exp="set #role = :role, #updated_at = :updated_at, #updated_by = :updated_by",
            exp_attr_names={
                "#role": "role",
                "#updated_at": "updated_at",
                "#updated_by": "updated_by",
            },
            exp_attr_values={
                ":role": role,
                ":updated_at": now,
                ":updated_by": self.user_id,
                ":owner": "owner",
            },
            cond_exp="attribute_exists(user_id) AND #role <> :owner",
        )

    def update_status(self, status, user_id=None):
        if status not in ("active", "request", "reject"):
            raise ValueError("Invalid membership status")
        if not self.project_id:
            raise ValueError

        if not user_id:
            user_id = self.user_id

        now = datetime.now().isoformat()
        return super().guarded_update_item(
            partition_key=self.project_id,
            sort_key=user_id,
            update_exp="set #status = :status, #updated_at = :updated_at, #updated_by = :updated_by",
            exp_attr_names={
                "#status": "status",
                "#role": "role",
                "#updated_at": "updated_at",
                "#updated_by": "updated_by",
            },
            exp_attr_values={
                ":status": status,
                ":updated_at": now,
                ":updated_by": self.user_id,
                ":owner": "owner",
            },
            cond_exp="attribute_exists(user_id) AND #role <> :owner",
        )

    def delete(self, user_id=None):
        if not self.project_id or not self.user_id:
            raise ValueError

        if not user_id:
            user_id = self.user_id

        # Owners may only be removed by the explicit whole-project deletion flow.
        return self.client.transact_write_items(
            TransactItems=[
                active_project_check(self.project_id),
                {
                    "Delete": {
                        "TableName": os.environ.get("TABLE_ROLES", "Roles"),
                        "Key": {"project_id": {"S": self.project_id}, "user_id": {"S": user_id}},
                        "ConditionExpression": "attribute_exists(user_id) AND #role <> :owner",
                        "ExpressionAttributeNames": {"#role": "role"},
                        "ExpressionAttributeValues": {":owner": {"S": "owner"}},
                    }
                },
            ]
        )

    def delete_all(self):
        users = self.list_users()
        user_ids = [{"project_id": self.project_id, "user_id": user["user_id"]} for user in users]
        return super().batch_del_item(user_ids)
