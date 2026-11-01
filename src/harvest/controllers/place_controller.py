import logging
from uuid import uuid4
from datetime import datetime

from harvest.drivers.dynamodb_driver import DynamoDbDriver, active_project_check, resolve_table_name
from boto3.dynamodb.types import TypeSerializer
from boto3.dynamodb.conditions import Key
from harvest.exception import PlaceHierarchyLimitExceededError, NotMeetRequirementsToDeleteError
from harvest.utils.sorters import date_sort


class PlaceController(DynamoDbDriver):
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    def __init__(self, host, port):
        super().__init__(host, port, "Places")
        super().set_partition_key("project_id")
        super().set_sort_key("place_id")
        #    self.target = TargetController(host, port)
        self.logger.info("################################")

    def set_project_id(self, project_id):
        self.project_id = str(project_id)

    #    self.target.set_project_id(str(project_id))

    def set_user_id(self, user_id):
        self.user_id = user_id

    def set_place_id(self, place_id):
        self.place_id = str(place_id)

    def list(self, project_id=None, consistent_read=False):
        if not project_id and self.project_id:
            project_id = self.project_id

        ret = super().query(partition_key=project_id, consistent_read=consistent_read)
        sorted_items = date_sort(ret["Items"], key="created_at")
        return sorted_items

    def list_children(self, parent_place_id):
        if not self.project_id or not parent_place_id:
            raise ValueError

        super().set_sort_key("parent_place_id")
        ret = super().query(
            partition_key=self.project_id, sort_key=parent_place_id, index="ParentPlacesIndex"
        )
        super().set_sort_key("place_id")
        sorted_items = date_sort(ret["Items"], key="created_at")
        return sorted_items

    def show(self, place_id=None):
        if not self.project_id:
            raise ValueError
        if not place_id:
            place_id = self.place_id
        # user_idを使って権限チェック
        return super().get_item(self.project_id, place_id)["Item"]

    def create(self, name, parent_place_id=None):
        if not self.project_id or not name:
            raise ValueError

        if parent_place_id and parent_place_id != self.project_id:
            # 親placeを取得
            parent = self.show(parent_place_id)
            hierarchy = parent["hierarchy"]
            if len(hierarchy.split("#")) >= 5:
                raise PlaceHierarchyLimitExceededError()

            # ヒエラルキに親place_idを足す
            hierarchy += "#{}".format(parent_place_id)
        else:
            parent_place_id = self.project_id
            hierarchy = self.project_id

        # そのユーザにpj作成権限があるかどうかを確認
        new_id = str(uuid4())
        now = datetime.now().isoformat()

        item = {
            "project_id": self.project_id,
            "place_id": new_id,
            "parent_place_id": parent_place_id,
            "hierarchy": hierarchy,
            "name": str(name),
            "photos": {
                "required": 0,
                "results": {
                    "before": 0,
                    "after": 0,
                },
            },
            "created_at": now,
            "updated_at": now,
            "created_by": self.user_id,
            "updated_by": self.user_id,
        }
        serialize = TypeSerializer().serialize
        actions = [active_project_check(self.project_id)]
        if parent_place_id != self.project_id:
            actions.append(
                {
                    "Update": {
                        "TableName": resolve_table_name("Places"),
                        "Key": {
                            "project_id": {"S": self.project_id},
                            "place_id": {"S": parent_place_id},
                        },
                        "ConditionExpression": "attribute_exists(project_id)",
                        "UpdateExpression": "SET children_version = :version",
                        "ExpressionAttributeValues": {":version": {"S": str(uuid4())}},
                    }
                }
            )
        actions.append(
            {
                "Put": {
                    "TableName": resolve_table_name("Places"),
                    "Item": {key: serialize(value) for key, value in item.items()},
                    "ConditionExpression": "attribute_not_exists(project_id)",
                }
            }
        )
        return self.client.transact_write_items(TransactItems=actions)

    def update_name(self, name):
        if not self.project_id or not self.place_id:
            raise ValueError

        # user_idを使って権限チェック
        now = datetime.now().isoformat()
        return super().guarded_update_item(
            partition_key=self.project_id,
            sort_key=self.place_id,
            update_exp="set #name = :name, #updated_at = :updated_at, #updated_by = :updated_by",
            exp_attr_names={
                "#name": "name",
                "#updated_at": "updated_at",
                "#updated_by": "updated_by",
            },
            exp_attr_values={":name": name, ":updated_at": now, ":updated_by": self.user_id},
            cond_exp="attribute_exists(project_id)",
        )

    def delete(self, place_id=None):
        place_id = place_id or getattr(self, "place_id", None)
        if not self.project_id or not place_id:
            raise ValueError
        # Read the fence before checking children. Every concurrent child creation
        # changes this token in the same transaction as its insertion. Missing tokens
        # support existing rows without a migration or an unbounded counter.
        parent = super().get_item(self.project_id, place_id, consistent_read=True)["Item"]
        for name in ("Places", "Targets"):
            table = self.dynamodb.Table(resolve_table_name(name))
            cursor = None
            while True:
                request = {
                    "KeyConditionExpression": Key("project_id").eq(self.project_id),
                    "ConsistentRead": True,
                    "ProjectionExpression": "parent_place_id",
                }
                if cursor:
                    request["ExclusiveStartKey"] = cursor
                page = table.query(**request)
                if any(item.get("parent_place_id") == place_id for item in page.get("Items", [])):
                    raise NotMeetRequirementsToDeleteError()
                cursor = page.get("LastEvaluatedKey")
                if not cursor:
                    break
        action = {
            "TableName": resolve_table_name("Places"),
            "Key": {"project_id": {"S": self.project_id}, "place_id": {"S": place_id}},
            "ConditionExpression": "attribute_exists(project_id) AND attribute_not_exists(children_version)",
        }
        if "children_version" in parent:
            action["ConditionExpression"] = (
                "attribute_exists(project_id) AND children_version = :version"
            )
            action["ExpressionAttributeValues"] = {
                ":version": TypeSerializer().serialize(parent["children_version"])
            }
        return self.client.transact_write_items(
            TransactItems=[active_project_check(self.project_id), {"Delete": action}]
        )

    def delete_all(self):
        if not self.project_id:
            raise ValueError

        places = self.list(consistent_read=True)
        place_ids = [
            {"project_id": self.project_id, "place_id": place["place_id"]} for place in places
        ]
        return super().batch_del_item(place_ids)
