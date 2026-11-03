from unittest.mock import Mock

import pytest
from harvest.controllers.corporation_controller import CorporationController
from harvest.controllers.role_controller import RoleController
from harvest.controllers.template_controller import TemplateController
from harvest.exception import ActionDeniedError, TemplateNotFoundError


@pytest.fixture
def templates():
    obj = TemplateController.__new__(TemplateController)
    obj.user_id = "user-a"
    obj.project_id = "project-a"
    repo = Mock()
    repo.list_objs.side_effect = lambda prefix: [
        {"Key": key}
        for key in [
            "templates/excel/projects/project-a/mine",
            "templates/excel/projects/project-b/foreign",
            "templates/excel/defaults/basic_1.xlsx",
            "templates/excel/users/user-a/personal",
        ]
        if key.startswith(prefix)
    ]
    obj._TemplateController__repo = repo
    return obj, repo


@pytest.mark.parametrize(
    "template_id", ["foreign", "project-b", "../foreign", "mine-extra", "", "/tmp/escape"]
)
def test_template_scope_and_exact_id(templates, template_id):
    obj, repo = templates
    with pytest.raises(TemplateNotFoundError):
        obj.delete(template_id)
    repo.delete_obj.assert_not_called()


def test_default_readonly_and_owned_delete(templates):
    obj, repo = templates
    assert obj.get_template_path("basic_1.xlsx") == "templates/excel/defaults/basic_1.xlsx"
    with pytest.raises(TemplateNotFoundError):
        obj.delete("basic_1.xlsx")
    obj.delete("mine")
    repo.delete_obj.assert_called_once_with("templates/excel/projects/project-a/mine")


@pytest.fixture
def corporation(monkeypatch):
    obj = CorporationController.__new__(CorporationController)
    obj.corporation_id = "corp-a"
    obj.cognito = Mock()
    obj.project = Mock()
    obj.project_user = Mock()
    obj.cognito.show_group.return_value = {"GroupName": "corp-a"}

    def get_user(**kwargs):
        name = kwargs["Username"]
        return {
            "Username": name,
            "Enabled": True,
            "UserAttributes": [
                {
                    "Name": "custom:user_type",
                    "Value": "corporation" if name == "corp-a" else "employee",
                }
            ],
        }

    obj.cognito.cognito.admin_get_user.side_effect = get_user
    obj.cognito.cognito.admin_list_groups_for_user.return_value = {
        "Groups": [{"GroupName": "corp-b"}]
    }
    obj.cognito.disable_user.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    return obj


def test_corporation_cannot_disable_foreign_employee(corporation):
    with pytest.raises(ActionDeniedError):
        corporation.delete_user("foreign-user")
    corporation.cognito.disable_user.assert_not_called()


def test_corporation_cannot_disable_itself(corporation):
    with pytest.raises(ActionDeniedError):
        corporation.delete_user("corp-a")
    corporation.cognito.disable_user.assert_not_called()


def test_corporation_checks_server_admin_group(corporation):
    corporation.cognito.show_group.return_value = {"GroupName": "corp-b"}
    with pytest.raises(ActionDeniedError):
        corporation.delete_user("user-a")
    corporation.cognito.disable_user.assert_not_called()


def test_corporation_checks_all_membership_pages(corporation):
    corporation.cognito.cognito.admin_list_groups_for_user.side_effect = [
        {"Groups": [{"GroupName": "other"}], "NextToken": "page-two"},
        {"Groups": [{"GroupName": "corp-a"}]},
    ]
    assert corporation.delete_user("employee-a") == {"message": "successfully"}
    corporation.cognito.disable_user.assert_called_once_with("employee-a")
    assert (
        corporation.cognito.cognito.admin_list_groups_for_user.call_args.kwargs["NextToken"]
        == "page-two"
    )


def test_role_delete_atomically_excludes_owner():
    obj = RoleController.__new__(RoleController)
    obj.project_id, obj.user_id = "project-a", "actor"
    obj.client = Mock()
    obj.delete("target")
    args = obj.client.transact_write_items.call_args.kwargs["TransactItems"][1]["Delete"]
    assert "#role <> :owner" in args["ConditionExpression"]
    assert args["ExpressionAttributeValues"][":owner"] == {"S": "owner"}


@pytest.mark.parametrize("role", ["owner", "superadmin", ""])
def test_role_cannot_promote_to_owner_or_unknown(role):
    obj = RoleController.__new__(RoleController)
    with pytest.raises(ActionDeniedError):
        obj.update(role, "target")


def test_role_creation_is_atomic_with_active_project_check():
    obj = RoleController.__new__(RoleController)
    obj.project_id, obj.user_id = "project-a", "actor"
    obj.client = Mock()
    obj.create("worker", status="request")
    writes = obj.client.transact_write_items.call_args.kwargs["TransactItems"]
    assert writes[0]["ConditionCheck"]["Key"]["project_id"] == {"S": "project-a"}
    assert ":deleting" in writes[0]["ConditionCheck"]["ConditionExpression"]
    assert writes[1]["Put"]["ConditionExpression"] == "attribute_not_exists(project_id)"


def test_corporation_create_cannot_target_another_corporation(corporation):
    with pytest.raises(ActionDeniedError):
        corporation.create_user("corp-b", "User", "User", "user@example.invalid", "test-password")
    corporation.cognito.create_user.assert_not_called()
