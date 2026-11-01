"""Exercise child creation/deletion races against an atomic in-memory transaction store."""

from copy import deepcopy

import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from harvest.controllers.place_controller import PlaceController
from harvest.controllers.target_controller import TargetController
from harvest.exception import NotMeetRequirementsToDeleteError


class Store:
    def __init__(self, version=None):
        parent = {
            "project_id": "project",
            "place_id": "parent",
            "parent_place_id": "project",
            "hierarchy": "project",
            "name": "Parent",
            "created_at": "2026-01-01",
            "photos": {"required": 0, "results": {"before": 0, "after": 0}},
        }
        if version is not None:
            parent["children_version"] = version
        self.rows = {
            "Projects": {"project": {"project_id": "project"}},
            "Places": {"parent": parent},
            "Targets": {},
        }
        self.before_transaction = None
        self.strong_queries = []
        self.read_requests = []

    def Table(self, name):
        store = self

        class Table:
            def get_item(self, **request):
                store.read_requests.append(request)
                key = request["Key"].get("place_id")
                value = store.rows[name].get(key)
                return {"Item": deepcopy(value)} if value else {}

            def query(self, **request):
                store.strong_queries.append(request)
                assert request["ConsistentRead"] is True
                assert "IndexName" not in request
                return {"Items": deepcopy(list(store.rows[name].values()))}

        return Table()

    def transact_write_items(self, **request):
        if self.before_transaction:
            hook, self.before_transaction = self.before_transaction, None
            hook()
        snapshot = deepcopy(self.rows)
        decode = TypeDeserializer().deserialize
        for operation in request["TransactItems"]:
            kind, action = next(iter(operation.items()))
            table = action["TableName"]
            values = {k: decode(v) for k, v in action.get("ExpressionAttributeValues", {}).items()}
            item = {k: decode(v) for k, v in action.get("Item", action.get("Key", {})).items()}
            identity = item.get("place_id", item.get("target_id", item["project_id"]))
            current = snapshot[table].get(identity)
            condition = action.get("ConditionExpression", "")
            denied = "attribute_exists(project_id)" in condition and current is None
            denied |= "attribute_not_exists(project_id)" in condition and current is not None
            if "attribute_not_exists(children_version)" in condition:
                denied |= current is not None and "children_version" in current
            if "children_version = :version" in condition:
                denied |= current is None or current.get("children_version") != values[":version"]
            if denied:
                raise ClientError(
                    {"Error": {"Code": "TransactionCanceledException", "Message": "condition"}},
                    "TransactWriteItems",
                )
            if kind == "Put":
                snapshot[table][identity] = item
            elif kind == "Delete":
                snapshot[table].pop(identity, None)
            elif kind == "Update":
                expression = action["UpdateExpression"]
                if "children_version = :version" in expression:
                    current["children_version"] = values[":version"]
                if "children_version = :children_version" in expression:
                    current["children_version"] = values[":children_version"]
                if "photos.required = photos.required + :p" in expression:
                    current["photos"]["required"] += values[":p"]
        self.rows = snapshot
        return {}


def controllers(store):
    place = PlaceController.__new__(PlaceController)
    place.project_id, place.user_id, place.place_id = "project", "actor", "parent"
    place.set_partition_key("project_id")
    place.set_sort_key("place_id")
    place.dynamodb = place.client = store
    place._DynamoDbDriver__table = store.Table("Places")
    target = TargetController.__new__(TargetController)
    target.project_id, target.user_id, target.place, target.client = (
        "project",
        "actor",
        place,
        store,
    )
    return place, target


@pytest.mark.parametrize("kind", ["place", "target"])
@pytest.mark.parametrize("version", [None, "previous-version"])
def test_child_committing_after_empty_check_prevents_parent_deletion(kind, version):
    store = Store(version)
    place, target = controllers(store)
    child = place if kind == "place" else target
    store.before_transaction = lambda: child.create("Child", "parent")
    with pytest.raises(ClientError):
        place.delete("parent")
    assert "parent" in store.rows["Places"]
    assert len(store.rows["Places"] if kind == "place" else store.rows["Targets"]) == (
        2 if kind == "place" else 1
    )
    assert store.rows["Places"]["parent"]["children_version"] != version
    assert any(request.get("ConsistentRead") is True for request in store.read_requests)
    assert len(store.strong_queries) == 2


@pytest.mark.parametrize("kind", ["place", "target"])
def test_parent_deletion_after_child_parent_lookup_prevents_orphan(kind):
    store = Store()
    place, target = controllers(store)
    child = place if kind == "place" else target
    store.before_transaction = lambda: place.delete("parent")
    with pytest.raises(ClientError):
        child.create("Child", "parent")
    assert store.rows["Places"] == {}
    assert store.rows["Targets"] == {}


@pytest.mark.parametrize("kind", ["place", "target"])
def test_existing_child_blocks_parent_delete_without_gsi_visibility(kind):
    store = Store()
    place, target = controllers(store)
    child = place if kind == "place" else target
    child.create("Child", "parent")
    with pytest.raises(NotMeetRequirementsToDeleteError):
        place.delete("parent")
    assert "parent" in store.rows["Places"]


@pytest.mark.parametrize("version", [None, "previous-version"])
def test_empty_legacy_or_versioned_parent_can_be_deleted(version):
    store = Store(version)
    place, _ = controllers(store)
    place.delete("parent")
    assert store.rows["Places"] == {}


@pytest.mark.parametrize("kind", ["place", "target"])
def test_project_id_parent_is_root_without_phantom_place_update(kind):
    store = Store()
    place, target = controllers(store)
    child = place if kind == "place" else target
    child.create("Root child", "project")
    created = [
        item
        for table in ("Places", "Targets")
        for key, item in store.rows[table].items()
        if key != "parent"
    ]
    assert len(created) == 1
    assert created[0]["parent_place_id"] == "project"
    assert "children_version" not in store.rows["Places"]["parent"]
