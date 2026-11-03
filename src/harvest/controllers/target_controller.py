import os
import logging
from uuid import uuid4
from datetime import datetime

from harvest.drivers.dynamodb_driver import DynamoDbDriver, resolve_table_name, active_project_check
from harvest.drivers.s3_driver import S3Driver
from harvest.controllers.place_controller import PlaceController
from harvest.utils.sorters import date_sort

BUCKET_NAME = os.environ.get("PHOTOS_BUCKET_NAME")


class TargetController(DynamoDbDriver):
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    def __init__(self, host, port):
        super().__init__(host, port, "Targets")
        super().set_partition_key("project_id")
        super().set_sort_key("target_id")
        self.place = PlaceController(host, port)
        self.__s3 = S3Driver(BUCKET_NAME)

    def set_project_id(self, project_id):
        self.project_id = str(project_id)
        self.place.set_project_id(self.project_id)

    def set_user_id(self, user_id):
        self.user_id = user_id

    def set_target_id(self, target_id):
        self.target_id = str(target_id)

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
        super().set_sort_key("target_id")
        sorted_items = date_sort(ret["Items"], key="created_at")
        return sorted_items

    def show(self, target_id=None):
        if not self.project_id:
            raise ValueError
        if not target_id:
            target_id = self.target_id
        # user_idを使って権限チェック
        return super().get_item(self.project_id, target_id)["Item"]

    def create(self, name, parent_place_id=None):
        if not self.project_id or not name:
            raise ValueError

        if parent_place_id and parent_place_id != self.project_id:
            # 親placeを取得
            parent = self.place.show(parent_place_id)
            hierarchy = parent["hierarchy"]
            # ヒエラルキに親place_idを足す
            hierarchy += "#{}".format(parent_place_id)
            hierarchy = hierarchy.split("#")
            if hierarchy[0] == self.project_id:
                del hierarchy[0]
                self.logger.info("root_place found.")
        else:
            parent_place_id = self.project_id
            hierarchy = None

        # TODO: important ここもトランザクション処理させないと、ここが成功して次が失敗したときの影響が大きすぎる
        new_id = str(uuid4())
        now = datetime.now().isoformat()
        transact_items = [active_project_check(self.project_id)]
        transact_items.append(
            {
                "Put": {
                    "TableName": resolve_table_name("Targets"),
                    "Item": {
                        "project_id": {"S": self.project_id},
                        "target_id": {"S": new_id},
                        "parent_place_id": {"S": parent_place_id},
                        "name": {"S": str(name)},
                        "photos": {
                            "M": {
                                "adopt": {"M": {"before": {"NULL": True}, "after": {"NULL": True}}},
                                "before": {"L": []},
                                "after": {"L": []},
                            }
                        },
                        "created_at": {"S": now},
                        "updated_at": {"S": now},
                        "created_by": {"S": self.user_id},
                        "updated_by": {"S": self.user_id},
                    },
                }
            }
        )

        if hierarchy:
            for place_id in hierarchy:
                transact_items.append(
                    {
                        "Update": {
                            "TableName": resolve_table_name("Places"),
                            "Key": {
                                "project_id": {"S": self.project_id},
                                "place_id": {"S": place_id},
                            },
                            "ConditionExpression": "attribute_exists(project_id)",
                            "UpdateExpression": "set photos.required = photos.required + :p, #updated_at = :updated_at, #updated_by = :updated_by",
                            "ExpressionAttributeValues": {
                                ":p": {"N": "1"},
                                ":updated_at": {"S": now},
                                ":updated_by": {"S": self.user_id},
                            },
                            "ExpressionAttributeNames": {
                                "#updated_at": "updated_at",
                                "#updated_by": "updated_by",
                            },
                        }
                    }
                )
        # Fence deletion of the immediate parent against this child insertion.
        # Reuse its ancestor Update because DynamoDB permits only one action per item.
        if parent_place_id != self.project_id:
            for action in transact_items:
                update = action.get("Update")
                if update and update["Key"]["place_id"]["S"] == parent_place_id:
                    update["UpdateExpression"] += ", children_version = :children_version"
                    update["ExpressionAttributeValues"][":children_version"] = {"S": str(uuid4())}
        self.logger.info("Creating target in a guarded transaction")
        ret = self.client.transact_write_items(
            ReturnConsumedCapacity="INDEXES", TransactItems=transact_items
        )
        self.logger.info("transact_write_items result: {}".format(str(ret)))
        return ret

    def update_name(self, name):
        if not self.project_id or not self.target_id:
            raise ValueError

        # user_idを使って権限チェック
        now = datetime.now().isoformat()
        return super().guarded_update_item(
            partition_key=self.project_id,
            sort_key=self.target_id,
            update_exp="set #name = :name, #updated_at = :updated_at, #updated_by = :updated_by",
            exp_attr_names={
                "#name": "name",
                "#updated_at": "updated_at",
                "#updated_by": "updated_by",
            },
            exp_attr_values={":name": name, ":updated_at": now, ":updated_by": self.user_id},
            cond_exp="attribute_exists(project_id)",
        )

    def delete(self, target_id=None):
        if not self.project_id or not (target_id or getattr(self, "target_id", None)):
            raise ValueError
        if not target_id:
            target_id = self.target_id

        transact_items = [active_project_check(self.project_id)]
        photo_ids = []
        # targetを取得
        target = self.show(target_id)
        self.guarded_update_item(
            self.project_id,
            "SET #status = :deleting",
            sort_key=target_id,
            exp_attr_names={"#status": "status", "#photos": "photos"},
            exp_attr_values={":deleting": "deleting", ":photos": target["photos"]},
            cond_exp="attribute_exists(project_id) AND #photos = :photos",
        )
        if target["project_id"] == target["parent_place_id"]:
            # project直下である場合
            hierarchy = []
        else:
            # parent_place_idを使ってplaceを取得
            parent = self.place.show(target["parent_place_id"])
            hierarchy = parent["hierarchy"]
            # ヒエラルキに親place_idを足す
            hierarchy += "#{}".format(target["parent_place_id"])
            hierarchy = hierarchy.split("#")
            if hierarchy[0] == self.project_id:
                del hierarchy[0]

        # adoptがあるなら写真撮影済みということ
        # 写真があるなら、placeのresult側も削除しないといけない
        cond_exp = "set photos.required = photos.required - :p, #updated_at = :updated_at, #updated_by = :updated_by"
        exp_attr_name = {"#updated_at": "updated_at", "#updated_by": "updated_by"}
        for type in ["before", "after"]:
            photo_ids.extend(target["photos"][type])
            if target["photos"]["adopt"][type]:
                cond_exp += ", photos.results.#{} = photos.results.#{} - :p".format(type, type)
                exp_attr_name.update({"#" + type: type})

        now = datetime.now().isoformat()
        transact_items.append(
            {
                "Delete": {
                    "TableName": resolve_table_name("Targets"),
                    "Key": {"project_id": {"S": self.project_id}, "target_id": {"S": target_id}},
                    "ConditionExpression": "#status = :deleting",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {":deleting": {"S": "deleting"}},
                }
            }
        )
        for place_id in hierarchy:
            transact_items.append(
                {
                    "Update": {
                        "TableName": resolve_table_name("Places"),
                        "Key": {"project_id": {"S": self.project_id}, "place_id": {"S": place_id}},
                        "ConditionExpression": "attribute_exists(project_id)",
                        "UpdateExpression": cond_exp,
                        "ExpressionAttributeValues": {
                            ":p": {"N": "1"},
                            ":updated_at": {"S": now},
                            ":updated_by": {"S": self.user_id},
                        },
                        "ExpressionAttributeNames": exp_attr_name,
                    }
                }
            )
        # S3 delete is idempotent. Keep the tombstoned target and its photo IDs
        # until all files are gone; failed calls can then safely retry.
        for photo_id in photo_ids:
            self.__s3.delete_obj(photo_id)
        return self.client.transact_write_items(TransactItems=transact_items)

    def delete_all(self):
        if not self.project_id:
            raise ValueError

        target_ids = []
        photo_ids = []
        targets = self.list(consistent_read=True)
        for target in targets:
            photo_ids.extend(target["photos"]["before"])
            photo_ids.extend(target["photos"]["after"])
            target_ids.append({"project_id": self.project_id, "target_id": target["target_id"]})

        for photo_id in photo_ids:
            self.__s3.delete_obj(photo_id)
        return super().batch_del_item(target_ids)
