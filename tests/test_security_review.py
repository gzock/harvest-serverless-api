"""Negative cases found by the November auth/media/provisioning review."""

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from botocore.exceptions import ClientError
from openpyxl import Workbook

from harvest.controllers.corporation_controller import CorporationController
from harvest.controllers.project_user_controller import ProjectUserController
from harvest.controllers.template_controller import TemplateController
from harvest.exception import SettingPropertyPositionInvalidError


def test_deleted_cognito_member_remains_manageable_and_does_not_break_list(monkeypatch):
    cognito = Mock()
    cognito.show_user.side_effect = lambda name: [] if name == "deleted" else [{"Attributes": []}]
    monkeypatch.setattr(
        "harvest.controllers.project_user_controller.CognitoDriver", lambda *_: cognito
    )
    controller = ProjectUserController.__new__(ProjectUserController)
    controller.project_id = "project"
    controller.role = Mock()
    controller.role.list_users.return_value = [
        {"user_id": "deleted", "role": "worker"},
        {"user_id": "legacy", "role": "admin"},
    ]
    controller.role.show.return_value = {"user_id": "deleted", "role": "worker"}
    assert controller.list() == [
        {
            "user_id": "deleted",
            "username": "deleted",
            "email": "",
            "role": "worker",
            "status": "active",
        },
        {
            "user_id": "legacy",
            "username": "legacy",
            "email": "",
            "role": "admin",
            "status": "active",
        },
    ]
    assert controller.show("deleted")["username"] == "deleted"


def test_cognito_read_failure_is_not_hidden_as_deleted_account(monkeypatch):
    cognito = Mock()
    cognito.show_user.side_effect = ClientError(
        {"Error": {"Code": "TooManyRequestsException"}}, "ListUsers"
    )
    monkeypatch.setattr(
        "harvest.controllers.project_user_controller.CognitoDriver", lambda *_: cognito
    )
    controller = ProjectUserController.__new__(ProjectUserController)
    controller.role = Mock()
    controller.role.show.return_value = {"role": "worker"}
    with pytest.raises(ClientError):
        controller.show("member")


def corporation():
    controller = CorporationController.__new__(CorporationController)
    controller.require_corporation_admin = Mock(return_value="corporation")
    controller.cognito, controller.billing = Mock(), Mock()
    controller.cognito.create_user.return_value = {
        "Username": "new-employee",
        "Enabled": True,
        "UserStatus": "FORCE_CHANGE_PASSWORD",
    }
    return controller


@pytest.mark.parametrize("failure_stage", ["password", "group", "billing"])
def test_provisioning_failure_disables_only_just_created_user(failure_stage):
    controller = corporation()
    operation = {
        "password": controller.cognito.set_user_password,
        "group": controller.cognito.add_user_to_group,
        "billing": controller.billing.update,
    }[failure_stage]
    operation.side_effect = RuntimeError("simulated failure")
    with pytest.raises(RuntimeError, match="simulated failure"):
        controller.create_user(
            "corporation", "Employee", "カナ", "employee@example.invalid", "synthetic-only"
        )
    controller.cognito.disable_user.assert_called_once_with("new-employee")
    controller.cognito.cognito.admin_list_groups_for_user.assert_not_called()


def test_username_conflict_never_disables_existing_account():
    controller = corporation()
    controller.cognito.create_user.side_effect = ClientError(
        {"Error": {"Code": "UsernameExistsException"}}, "AdminCreateUser"
    )
    with pytest.raises(ClientError):
        controller.create_user(
            "corporation", "Employee", "カナ", "employee@example.invalid", "synthetic-only"
        )
    controller.cognito.disable_user.assert_not_called()


def test_successful_provisioning_does_not_require_optional_cognito_timestamps():
    controller = corporation()
    result = controller.create_user(
        "corporation", "Employee", "カナ", "employee@example.invalid", "synthetic-only"
    )
    assert result["Username"] == "new-employee"
    controller.billing.update.assert_called_once_with(
        event="create_user", user_id="corporation", member_id="new-employee"
    )


def workbook_with_labels(tmp_path, outside=False):
    workbook = Workbook()
    sheet = workbook.active
    for cell, value in {
        "A1": "{name}",
        "A2": "{photo}",
        "A3": "{type}",
        "B1": "Static label",
        "B2": "Static label",
    }.items():
        sheet[cell] = value
    if outside:
        sheet["A3"] = None
        sheet["C3"] = "{type}"
    sheet.print_area = "A1:B3"
    path = tmp_path / "template.xlsx"
    workbook.save(path)
    workbook.close()
    return path


def test_template_repeated_static_labels_are_not_duplicate_placeholders(tmp_path):
    path = workbook_with_labels(tmp_path)
    TemplateController.validate_archive(path.read_bytes())
    assert TemplateController.__new__(TemplateController).validate(path, False, False, False)


def test_placeholder_outside_print_columns_rejected(tmp_path):
    path = workbook_with_labels(tmp_path, outside=True)
    with pytest.raises(SettingPropertyPositionInvalidError):
        TemplateController.__new__(TemplateController).validate(path, False, False, False)


def malicious_workbook(worksheet, print_area=None):
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "xl/workbook.xml",
            "<workbook><definedNames>"
            + (
                f'<definedName name="_xlnm.Print_Area">{print_area}</definedName>'
                if print_area
                else ""
            )
            + "</definedNames></workbook>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "xml,area",
    [
        ('<worksheet><dimension ref="A1:XFD1048576"/></worksheet>', None),
        ('<worksheet><mergeCells><mergeCell ref="A1:XFD1048576"/></mergeCells></worksheet>', None),
        (
            '<worksheet><sheetData><row r="1048576"><c r="A1048576"/></row></sheetData></worksheet>',
            None,
        ),
        ('<worksheet><dimension ref="A1:B4"/></worksheet>', "'Sheet1'!$A$1:$XFD$1048576"),
        ('<!DOCTYPE worksheet [<!ENTITY data "forbidden">]><worksheet>&data;</worksheet>', None),
    ],
)
def test_tiny_zip_with_unbounded_xml_ranges_is_rejected_before_openpyxl(xml, area):
    data = malicious_workbook(xml, area)
    assert len(data) < 4096
    with pytest.raises(ValueError):
        TemplateController.validate_archive(data)


@pytest.mark.parametrize("number", [1, 2])
def test_existing_shipped_templates_remain_accepted(number):
    TemplateController.validate_archive(
        (Path(__file__).parent / "fixtures" / f"basic_{number}.xlsx").read_bytes()
    )
