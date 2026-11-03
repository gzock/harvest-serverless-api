from unittest.mock import Mock
import pytest

from harvest.drivers.cognito_driver import CognitoDriver
from harvest.drivers.dynamodb_driver import DynamoDbDriver
from harvest.drivers.s3_driver import S3Driver
from harvest.drivers.csv_driver import CsvDriver
from harvest.utils.csv_optimizer import CsvOptimizer
from harvest.utils.sorters import date_sort
from harvest.exception import (
    MaxHierarchyExceededError,
    ParentPlaceNotFoundError,
    DuplicatePlaceNameExistsError,
)


def cognito(client):
    driver = CognitoDriver.__new__(CognitoDriver)
    driver.pool_id = "test-pool"
    driver.cognito = client
    return driver


def test_cognito_users_advance_token_and_terminate():
    client = Mock()
    client.list_users.side_effect = [
        {"Users": [{"Username": "one"}], "PaginationToken": "next"},
        {"Users": [{"Username": "two"}]},
    ]
    assert len(cognito(client).list_users("name")) == 2
    assert client.list_users.call_args.kwargs["PaginationToken"] == "next"
    assert client.list_users.call_count == 2


def test_cognito_empty_and_repeated_token():
    client = Mock()
    client.list_users.return_value = {"Users": []}
    assert cognito(client).list_users("name") == []
    assert client.list_users.call_count == 1
    client.list_users.return_value = {"Users": [], "PaginationToken": "same"}
    with pytest.raises(RuntimeError):
        cognito(client).list_users("name")


def test_cognito_group_boundary_deduplicates_overlapping_pages():
    client = Mock()
    first = [{"Username": str(i)} for i in range(60)]
    client.list_users_in_group.side_effect = [
        {"Users": first, "NextToken": "page2"},
        {"Users": [{"Username": "59"}, {"Username": "60"}]},
    ]
    assert len(cognito(client).list_users_in_group("corp")) == 61
    assert client.list_users_in_group.call_count == 2
    assert client.list_users_in_group.call_args.kwargs["NextToken"] == "page2"


def test_cognito_all_groups():
    client = Mock()
    client.list_groups.side_effect = [
        {"Groups": [], "NextToken": "next"},
        {"Groups": [{"GroupName": "last"}]},
    ]
    assert cognito(client).list_groups()["Groups"] == [{"GroupName": "last"}]
    assert client.list_groups.call_args.kwargs["NextToken"] == "next"


def test_dynamo_empty_page_does_not_end_iteration():
    driver = DynamoDbDriver.__new__(DynamoDbDriver)
    table = Mock()
    table.query.side_effect = [
        {"Items": [], "LastEvaluatedKey": {"id": "a"}},
        {"Items": [{"id": "b"}]},
    ]
    driver._DynamoDbDriver__table = table
    driver.set_partition_key("project_id")
    assert driver.query("project")["Items"] == [{"id": "b"}]
    assert table.query.call_args.kwargs["ExclusiveStartKey"] == {"id": "a"}


def test_dynamo_batch_get_retries_unprocessed_and_uses_environment(monkeypatch):
    monkeypatch.setenv("TABLE_PROJECTS", "stage-projects")
    monkeypatch.setattr("harvest.drivers.dynamodb_driver.time.sleep", lambda _: None)
    driver = DynamoDbDriver.__new__(DynamoDbDriver)
    driver.dynamodb = Mock()
    pending = {"stage-projects": {"Keys": [{"project_id": "two"}]}}
    driver.dynamodb.batch_get_item.side_effect = [
        {"Responses": {"stage-projects": [{"project_id": "one"}]}, "UnprocessedKeys": pending},
        {"Responses": {"stage-projects": [{"project_id": "two"}]}},
    ]
    assert (
        len(driver.batch_get_items("Projects", [{"project_id": "one"}, {"project_id": "two"}])) == 2
    )
    assert driver.dynamodb.batch_get_item.call_args.kwargs["RequestItems"] == pending


def test_dynamo_incomplete_batch_raises(monkeypatch):
    monkeypatch.setattr("harvest.drivers.dynamodb_driver.time.sleep", lambda _: None)
    driver = DynamoDbDriver.__new__(DynamoDbDriver)
    driver.dynamodb = Mock()
    driver.dynamodb.batch_get_item.return_value = {
        "UnprocessedKeys": {"Projects": {"Keys": [{"id": "one"}]}}
    }
    with pytest.raises(RuntimeError, match="incomplete"):
        driver.batch_get_items("Projects", [{"id": "one"}], max_retries=1)


def test_s3_list_uses_next_token_even_after_empty_page():
    driver = S3Driver.__new__(S3Driver)
    driver.bucket_name = "test"
    driver._S3Driver__client = Mock()
    driver._S3Driver__client.list_objects_v2.side_effect = [
        {"IsTruncated": True, "NextContinuationToken": "next"},
        {"IsTruncated": False, "Contents": [{"Key": "last"}]},
    ]
    assert driver.list_objs("prefix/") == [{"Key": "last"}]
    assert driver._S3Driver__client.list_objects_v2.call_args.kwargs["ContinuationToken"] == "next"


def optimize(text):
    csv = CsvOptimizer(items=CsvDriver().parse(text.encode("utf-8")))
    csv.set_project_id("project")
    return csv.optimize()


def test_csv_japanese_headers_and_quoted_content_are_not_replaced():
    rows = CsvDriver().parse('親,種類,名前\n,場所,"名前,場所,そのまま"\n'.encode("cp932"))
    assert rows == [{"parent": "", "type": "place", "name": "名前,場所,そのまま"}]


@pytest.mark.parametrize(
    "text",
    [
        "parent,type,name\n,potato,x",
        "parent,type\n,place",
        "parent,type,name\n,place,x,extra",
        "parent,type,name\n,place,",
    ],
)
def test_csv_rejects_invalid_schema(text):
    with pytest.raises(ValueError):
        CsvDriver().parse(text.encode())


def test_csv_graph_is_order_independent_and_updates_ancestors():
    places, targets = optimize(
        "parent,type,name\nchild,target,photo\nroot,place,child\n,place,root"
    )
    root = next(p for p in places if p["name"] == "root")
    child = next(p for p in places if p["name"] == "child")
    assert child["hierarchy"] == "project#" + root["place_id"]
    assert root["photos_required_num"] == child["photos_required_num"] == 1
    assert targets[0]["parent_place_id"] == child["place_id"]


@pytest.mark.parametrize(
    "rows,error",
    [
        ("b,place,a\na,place,b", ValueError),
        ("missing,target,photo", ParentPlaceNotFoundError),
        (",place,same\n,place,same", DuplicatePlaceNameExistsError),
        (
            ",place,1\n1,place,2\n2,place,3\n3,place,4\n4,place,5\n5,place,6",
            MaxHierarchyExceededError,
        ),
    ],
)
def test_csv_graph_rejects_invalid_relationships(rows, error):
    with pytest.raises(error):
        optimize("parent,type,name\n" + rows)


def test_csv_allows_targets_at_project_root():
    places, targets = optimize("parent,type,name\n,target,photo")
    assert places == []
    assert targets[0]["parent_place_id"] == "project"


def test_sort_mixed_legacy_utc_and_offsets():
    rows = [
        {"date": "2026-01-01T09:00:01+09:00"},
        {"date": "2026-01-01T00:00:00"},
        {"date": "2025-12-31T23:59:59Z"},
    ]
    assert date_sort(rows, "date") == rows[::-1]


def test_dynamo_batch_write_retries_unprocessed_and_deduplicates_deletes(monkeypatch):
    monkeypatch.setattr("harvest.drivers.dynamodb_driver.time.sleep", lambda _: None)
    driver = DynamoDbDriver.__new__(DynamoDbDriver)
    driver.logical_table_name = "Targets"
    driver.dynamodb = Mock()
    request = {"Targets": [{"DeleteRequest": {"Key": {"project_id": "p", "target_id": "t"}}}]}
    driver.dynamodb.batch_write_item.side_effect = [
        {"UnprocessedItems": request},
        {"UnprocessedItems": {}},
    ]
    driver.batch_del_item([{"project_id": "p", "target_id": "t"}] * 2)
    assert driver.dynamodb.batch_write_item.call_args_list[0].kwargs["RequestItems"] == request
    assert driver.dynamodb.batch_write_item.call_args_list[1].kwargs["RequestItems"] == request


def test_driver_boto_resource_batch_contract_with_stubber(monkeypatch):
    import boto3
    from botocore.stub import Stubber

    driver = DynamoDbDriver.__new__(DynamoDbDriver)
    driver.logical_table_name = "Targets"
    driver.dynamodb = boto3.resource(
        "dynamodb",
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    expected = {
        "RequestItems": {
            "Targets": [{"DeleteRequest": {"Key": {"project_id": "p", "target_id": "t"}}}]
        }
    }
    with Stubber(driver.dynamodb.meta.client) as stubber:
        stubber.add_response("batch_write_item", {"UnprocessedItems": {}}, expected)
        driver.batch_del_item([{"project_id": "p", "target_id": "t"}])
        stubber.assert_no_pending_responses()


def test_project_create_and_owner_are_one_transaction(monkeypatch):
    from harvest.controllers.project_controller import ProjectController

    project = ProjectController.__new__(ProjectController)
    project.user_id = "owner"
    project.client = Mock()
    project.create("name", "2026-01-01", "2026-01-02")
    actions = project.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert [action["Put"]["TableName"] for action in actions] == ["Projects", "Roles"]
    assert actions[0]["Put"]["Item"]["project_id"] == actions[1]["Put"]["Item"]["project_id"]


def test_place_create_rejects_a_seventh_level():
    from harvest.controllers.place_controller import PlaceController
    from harvest.exception import PlaceHierarchyLimitExceededError

    place = PlaceController.__new__(PlaceController)
    place.project_id = "project"
    place.show = Mock(return_value={"hierarchy": "project#one#two#three#four#five"})
    with pytest.raises(PlaceHierarchyLimitExceededError):
        place.create("seventh", parent_place_id="sixth")
