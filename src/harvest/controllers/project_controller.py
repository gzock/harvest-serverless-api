import logging
from uuid import uuid4, UUID
from datetime import datetime
from base64 import urlsafe_b64encode, urlsafe_b64decode

from harvest.drivers.dynamodb_driver import DynamoDbDriver, resolve_table_name
from boto3.dynamodb.types import TypeSerializer
from harvest.controllers.role_controller import RoleController
from harvest.controllers.target_controller import TargetController
from harvest.controllers.place_controller import PlaceController
from harvest.utils.sorters import date_sort


class ProjectController(DynamoDbDriver):
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    def __init__(self, host, port):
        super().__init__(host, port, "Projects")
        super().set_partition_key("project_id")
        self.role = RoleController(host, port)
        self.place = PlaceController(host, port)
        self.target = TargetController(host, port)

    def set_user_id(self, user_id):
        self.user_id = str(user_id)
        self.role.set_user_id(str(user_id))

    def set_project_id(self, project_id):
        # if re.match("[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", project_id):
        if len(project_id) == 22:
            project_id = str(UUID(bytes=urlsafe_b64decode(project_id + "==")))
        self.project_id = str(project_id)
        self.role.set_project_id(str(project_id))
        self.place.set_project_id(str(project_id))
        self.target.set_project_id(str(project_id))

    def list_project_roles(self):
        if not self.user_id:
            raise ValueError

        return self.role.list_projects()

    def list_project_ids(self):
        if not self.user_id:
            raise ValueError

        return [item["project_id"] for item in self.list_project_roles()]

    def list_projects(self):
        if not self.user_id:
            raise ValueError

        ret = []
        roles = self.list_project_roles()
        self.logger.info("project roles: " + str(roles))

        # keys = [ {"project_id": role["project_id"]} for role in roles if role["status"] == "active" ] # realy... only this.

        # this code is required for usres who don't have status property.
        keys = []
        for role in roles:
            if "status" not in role:
                keys.append({"project_id": role["project_id"]})
                self.logger.info(
                    "status property not have. in this case, think of it as an active user."
                )
            elif role["status"] == "active":
                keys.append({"project_id": role["project_id"]})
        # one liner
        # keys = [ {"project_id": role["project_id"]} for role in roles if "status" not in role or role["status"] == "active" ]

        if keys:
            projects = self.batch_get_items("Projects", keys)

            self.logger.info("projects count: %d" % len(projects))
            self.logger.info("top 50 projects only... projects: " + str(projects))

            for project in projects:
                for role in roles:
                    if project["project_id"] == role["project_id"]:
                        project.update({"role": role["role"]})
                        project.update(
                            {"project_code": self.gen_project_code(project["project_id"])}
                        )
                        ret.append(project)

            ret = date_sort(ret, key="created_at")
            self.logger.info("return for projects with role: " + str(ret))
        return ret

    def show(self, project_id=None):
        if not project_id:
            project_id = self.project_id
        return super().get_item(project_id)["Item"]

    def gen_project_code(self, project_id):
        if not project_id:
            project_id = self.project_id

        self.logger.info("specified project_id: " + project_id)
        project_code = urlsafe_b64encode(UUID(project_id).bytes).decode("ASCII").rstrip("=")
        self.logger.info("convert project_id to project_code: " + project_code)
        return project_code

    def create(self, name, start_on, complete_on):
        if not self.user_id or not name:
            raise ValueError

        # そのユーザにpj作成権限があるかどうかを確認
        new_id = str(uuid4())
        now = datetime.now().isoformat()

        project = {
            "project_id": new_id,
            "name": str(name),
            "created_at": now,
            "updated_at": now,
            "start_on": start_on,
            "complete_on": complete_on,
            "created_by": self.user_id,
            "updated_by": self.user_id,
        }
        owner = {
            "project_id": new_id,
            "user_id": self.user_id,
            "role": "owner",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "created_by": self.user_id,
            "updated_by": self.user_id,
        }
        serialize = TypeSerializer().serialize
        return self.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": resolve_table_name(table),
                        "Item": {key: serialize(value) for key, value in item.items()},
                        "ConditionExpression": "attribute_not_exists(project_id)",
                    }
                }
                for table, item in (("Projects", project), ("Roles", owner))
            ]
        )

    def update(self, name, start_on, complete_on):
        if not self.user_id or not self.project_id:
            raise ValueError

        self.logger.info("update project properties: %s, %s, %s" % (name, start_on, complete_on))
        # TODO: 日付のバリデーションをすべき
        now = datetime.now().isoformat()
        return super().update_item(
            partition_key=self.project_id,
            update_exp="set #name = :name, #start_on = :start_on, #complete_on = :complete_on, #updated_at = :updated_at, #updated_by = :updated_by",
            exp_attr_names={
                "#name": "name",
                "#status": "status",
                "#start_on": "start_on",
                "#complete_on": "complete_on",
                "#updated_at": "updated_at",
                "#updated_by": "updated_by",
            },
            exp_attr_values={
                ":name": name,
                ":start_on": start_on,
                ":deleting": "deleting",
                ":importing": "importing",
                ":complete_on": complete_on,
                ":updated_at": now,
                ":updated_by": self.user_id,
            },
            cond_exp="attribute_exists(project_id) AND (attribute_not_exists(#status) OR (#status <> :deleting AND #status <> :importing))",
        )

    def delete(self, project_id=""):
        if not self.user_id:
            raise ValueError
        # user_idを使って権限チェック
        if not project_id:
            project_id = self.project_id

        self.set_project_id(project_id)
        self.update_item(
            project_id,
            "SET #status = :deleting",
            exp_attr_names={"#status": "status"},
            exp_attr_values={":deleting": "deleting", ":importing": "importing"},
            cond_exp="attribute_exists(project_id) AND (attribute_not_exists(#status) OR #status <> :importing)",
        )
        # Keep the owner and project until cleanup succeeds, so a failed operation
        # remains authorized and can be retried. All writers reject deleting projects.
        self.target.delete_all()
        self.place.delete_all()
        role_keys = [
            {"project_id": project_id, "user_id": role["user_id"]}
            for role in self.role.query(project_id, consistent_read=True)["Items"]
            if role["user_id"] != self.user_id
        ]
        self.role.batch_del_item(role_keys)
        return self.client.transact_write_items(
            TransactItems=[
                {
                    "Delete": {
                        "TableName": resolve_table_name("Roles"),
                        "Key": {"project_id": {"S": project_id}, "user_id": {"S": self.user_id}},
                    }
                },
                {
                    "Delete": {
                        "TableName": resolve_table_name("Projects"),
                        "Key": {"project_id": {"S": project_id}},
                        "ConditionExpression": "#status = :deleting",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {":deleting": {"S": "deleting"}},
                    }
                },
            ]
        )
