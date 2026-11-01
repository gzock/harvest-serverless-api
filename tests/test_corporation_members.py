"""Optional Cognito profiles cannot break a corporation's list or billing snapshot."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from harvest.commons.billing.billing import Billing
from harvest.controllers.corporation_controller import CorporationController
from harvest.exception import ActionDeniedError


def corporation(users):
    controller = CorporationController.__new__(CorporationController)
    controller.corporation_id = "corporation"
    controller.cognito = Mock()
    controller.cognito.list_users_in_group.return_value = users
    return controller


def test_minimal_enabled_group_member_needs_no_optional_profile_fields():
    controller = corporation(
        [
            {
                "Username": "employee",
                "Enabled": True,
                "Attributes": [{"Name": "custom:user_type", "Value": "employee"}],
            }
        ]
    )
    users = controller.list_users()
    assert users == [
        {"user_id": "employee", "user_type": "employee", "kana_username": "", "created_at": ""}
    ]
    controller.cognito.list_users_in_group.assert_called_once_with(group_name="corporation")
    billing = Billing.__new__(Billing)
    billing.set_users(users)
    assert billing.users == users
    assert billing.calc_price(len(billing.users)) == 550


def test_optional_profile_attributes_preserve_values_and_strip_internal_fields():
    attrs = {
        "custom:user_type": "employee",
        "custom:kana_username": "テスト",
        "custom:created_at": "legacy-date",
        "custom:pricing_plan": "standard",
        "email_verified": "true",
        "sub": "internal",
        "email": "member@example.invalid",
        "preferred_username": "Member",
    }
    controller = corporation(
        [
            {
                "Username": "employee",
                "Enabled": True,
                "Attributes": [{"Name": key, "Value": value} for key, value in attrs.items()],
            }
        ]
    )
    assert controller.list_users() == [
        {
            "user_id": "employee",
            "user_type": "employee",
            "kana_username": "テスト",
            "created_at": "legacy-date",
            "email": "member@example.invalid",
            "preferred_username": "Member",
        }
    ]


def test_absent_user_type_does_not_invent_employee_privilege():
    created = datetime(2026, 10, 1, tzinfo=timezone.utc)
    controller = corporation([{"Username": "legacy", "Enabled": True, "UserCreateDate": created}])
    assert controller.list_users()[0] == {
        "user_id": "legacy",
        "user_type": "",
        "kana_username": "",
        "created_at": created.isoformat(),
    }
    controller.require_corporation_admin = Mock(return_value="corporation")
    controller.cognito.cognito.admin_get_user.return_value = {
        "Username": "legacy",
        "Enabled": True,
        "UserAttributes": [],
    }
    with pytest.raises(ActionDeniedError):
        controller.require_owned_employee("legacy")
    controller.cognito.cognito.admin_list_groups_for_user.assert_not_called()


def test_disabled_or_unidentified_rows_are_not_billable_members():
    controller = corporation(
        [
            {"Username": "disabled", "Enabled": False},
            {"Username": "unknown"},
            {"Enabled": True},
            {"Username": "", "Enabled": True},
        ]
    )
    assert controller.list_users() == []


def test_existing_non_employee_group_members_remain_in_snapshot():
    # Do not silently change established invoice counts while fixing display fields.
    controller = corporation(
        [
            {
                "Username": "corporation",
                "Enabled": True,
                "Attributes": [{"Name": "custom:user_type", "Value": "corporation"}],
            }
        ]
    )
    assert controller.list_users()[0]["user_type"] == "corporation"
