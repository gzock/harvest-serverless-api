import logging
import os
import time
import random
import boto3
from botocore.config import Config
from boto3.dynamodb.types import TypeSerializer
from boto3.dynamodb.conditions import Key


def resolve_table_name(logical_name):
    """Resolve logical names consistently for resource and transaction APIs."""
    fallback = (
        os.environ.get("TABLE_BILLINGS", logical_name) if logical_name == "Bills" else logical_name
    )
    return os.environ.get("TABLE_" + logical_name.upper(), fallback)


def active_project_check(project_id):
    return {
        "ConditionCheck": {
            "TableName": resolve_table_name("Projects"),
            "Key": {"project_id": {"S": project_id}},
            "ConditionExpression": "attribute_exists(project_id) AND (attribute_not_exists(#status) OR (#status <> :deleting AND #status <> :importing))",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {
                ":deleting": {"S": "deleting"},
                ":importing": {"S": "importing"},
            },
        }
    }


class DynamoDbDriver:
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    def __init__(self, host, port, table_name):
        # if not host or not port:
        #  raise ValueError
        self.logical_table_name = table_name
        self.__dynamo_host = host
        self.__dynamo_port = port
        self.logger.info("connecting dynamo host: {}, port: {}".format(host, port))

        self.dynamodb, self.client = self.__connect(host, port)
        self.__table = self.dynamodb.Table(resolve_table_name(table_name))

    def __connect(self, host, port):
        options = {
            "region_name": os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "ap-northeast-1",
            "config": Config(
                connect_timeout=3, read_timeout=10, retries={"mode": "standard", "max_attempts": 2}
            ),
        }
        if host and port:
            options["endpoint_url"] = f"http://{host}:{port}"
        return boto3.resource("dynamodb", **options), boto3.client("dynamodb", **options)

    def set_partition_key(self, key):
        self.__partition_key = key

    def set_sort_key(self, key):
        self.__sort_key = key

    def get_item(self, partition_key, sort_key=None, consistent_read=False):
        if partition_key and not sort_key:
            keys = {self.__partition_key: partition_key}
        elif partition_key and sort_key:
            keys = {
                self.__partition_key: partition_key,
                self.__sort_key: sort_key,
            }
        else:
            raise ValueError

        return self.__table.get_item(Key=keys, ConsistentRead=consistent_read)

    def put_item(self, item, cond_exp=None):
        if not isinstance(item, dict):
            raise TypeError
        if cond_exp:
            return self.__table.put_item(
                Item=item, ConditionExpression=cond_exp, ReturnValues="ALL_OLD"
            )
        else:
            return self.__table.put_item(Item=item, ReturnValues="ALL_OLD")

    def _batch_write(self, requests, max_retries=8):
        results = []
        name = resolve_table_name(self.logical_table_name)
        for offset in range(0, len(requests), 25):
            pending = {name: requests[offset : offset + 25]}
            for attempt in range(max_retries + 1):
                response = self.dynamodb.batch_write_item(RequestItems=pending)
                pending = response.get("UnprocessedItems", {})
                if not pending:
                    results.append(response)
                    break
                if attempt == max_retries:
                    raise RuntimeError("DynamoDB batch write remained incomplete")
                time.sleep(random.uniform(0, min(0.05 * 2**attempt, 1)))
        return results

    def batch_put_item(self, items):
        return self._batch_write([{"PutRequest": {"Item": item}} for item in items])

    def batch_del_item(self, keys):
        unique = {repr(sorted(key.items())): key for key in keys}
        return self._batch_write([{"DeleteRequest": {"Key": key}} for key in unique.values()])

    def _all_pages(self, operation, **kwargs):
        items, scanned, seen = [], 0, set()
        while True:
            page = operation(**kwargs)
            items.extend(page.get("Items", []))
            scanned += page.get("ScannedCount", 0)
            cursor = page.get("LastEvaluatedKey")
            if not cursor:
                return {**page, "Items": items, "Count": len(items), "ScannedCount": scanned}
            marker = repr(sorted(cursor.items()))
            if marker in seen:
                raise RuntimeError("DynamoDB repeated a pagination cursor")
            seen.add(marker)
            kwargs["ExclusiveStartKey"] = cursor

    def scan(self):
        return self._all_pages(self.__table.scan)

    def query(
        self, partition_key, sort_key=None, index=None, begins_with=False, consistent_read=False
    ):
        if partition_key is None:
            raise ValueError("Partition key is required")
        condition = Key(self.__partition_key).eq(partition_key)
        if sort_key is not None:
            condition &= (
                Key(self.__sort_key).begins_with(sort_key)
                if begins_with
                else Key(self.__sort_key).eq(sort_key)
            )
        if index and consistent_read:
            raise ValueError("Strongly consistent reads require a base table, not a GSI")
        args = {"KeyConditionExpression": condition, "ConsistentRead": consistent_read}
        if index:
            args["IndexName"] = index
        return self._all_pages(self.__table.query, **args)

    def batch_get_items(self, logical_table, keys, max_retries=8):
        """Read all requested keys, including throttled UnprocessedKeys."""
        result = []
        name = resolve_table_name(logical_table)
        for offset in range(0, len(keys), 100):
            pending = {name: {"Keys": keys[offset : offset + 100], "ConsistentRead": False}}
            for attempt in range(max_retries + 1):
                response = self.dynamodb.batch_get_item(RequestItems=pending)
                result.extend(response.get("Responses", {}).get(name, []))
                pending = response.get("UnprocessedKeys", {})
                if not pending:
                    break
                if attempt == max_retries:
                    raise RuntimeError("DynamoDB batch read remained incomplete")
                time.sleep(random.uniform(0, min(0.05 * 2**attempt, 1)))
        return result

    def update_item(
        self,
        partition_key,
        update_exp,
        sort_key=None,
        exp_attr_names=None,
        exp_attr_values=None,
        cond_exp=None,
        ret_value="UPDATED_NEW",
    ):

        if partition_key and not sort_key:
            keys = {self.__partition_key: partition_key}
        elif partition_key and sort_key:
            keys = {self.__partition_key: partition_key, self.__sort_key: sort_key}
        else:
            raise ValueError

        kwargs = {"Key": keys, "UpdateExpression": update_exp, "ReturnValues": ret_value}
        for key, value in (
            ("ExpressionAttributeNames", exp_attr_names),
            ("ExpressionAttributeValues", exp_attr_values),
            ("ConditionExpression", cond_exp),
        ):
            if value is not None:
                kwargs[key] = value
        return self.__table.update_item(**kwargs)

    def guarded_update_item(
        self,
        partition_key,
        update_exp,
        sort_key=None,
        exp_attr_names=None,
        exp_attr_values=None,
        cond_exp=None,
        ret_value="UPDATED_NEW",
    ):
        serialize = TypeSerializer().serialize
        keys = {self.__partition_key: serialize(partition_key)}
        if sort_key is not None:
            keys[self.__sort_key] = serialize(sort_key)
        action = {
            "TableName": resolve_table_name(self.logical_table_name),
            "Key": keys,
            "UpdateExpression": update_exp,
        }
        if exp_attr_names:
            action["ExpressionAttributeNames"] = exp_attr_names
        if exp_attr_values:
            action["ExpressionAttributeValues"] = {
                key: serialize(value) for key, value in exp_attr_values.items()
            }
        if cond_exp:
            action["ConditionExpression"] = cond_exp
        return self.client.transact_write_items(
            TransactItems=[active_project_check(partition_key), {"Update": action}]
        )

    def guarded_put_item(self, item, cond_exp="attribute_not_exists(project_id)"):
        serialize = TypeSerializer().serialize
        action = {
            "TableName": resolve_table_name(self.logical_table_name),
            "Item": {key: serialize(value) for key, value in item.items()},
            "ConditionExpression": cond_exp,
        }
        return self.client.transact_write_items(
            TransactItems=[active_project_check(item["project_id"]), {"Put": action}]
        )

    def guarded_delete_item(self, partition_key, sort_key):
        serialize = TypeSerializer().serialize
        action = {
            "TableName": resolve_table_name(self.logical_table_name),
            "Key": {
                self.__partition_key: serialize(partition_key),
                self.__sort_key: serialize(sort_key),
            },
            "ConditionExpression": "attribute_exists(project_id)",
        }
        return self.client.transact_write_items(
            TransactItems=[active_project_check(partition_key), {"Delete": action}]
        )

    def delete_item(self, partition_key, sort_key=None):

        if partition_key and not sort_key:
            keys = {self.__partition_key: partition_key}
        elif partition_key and sort_key:
            keys = {self.__partition_key: partition_key, self.__sort_key: sort_key}
        else:
            raise ValueError

        return self.__table.delete_item(
            Key=keys, ConditionExpression="attribute_exists({})".format(self.__partition_key)
        )
