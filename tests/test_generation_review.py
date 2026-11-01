"""Report compatibility regressions discovered in the independent November review."""

from base64 import b64encode
from pathlib import Path
from unittest.mock import Mock

import pytest
from openpyxl import Workbook, load_workbook
from PIL import Image

from harvest.controllers.generate_controller import GenerateController
from harvest.utils.excel_doc_generator import ExcelDocGenerator


def test_excel_preserves_full_print_rectangle_and_clears_disabled_options(tmp_path):
    template = tmp_path / "template.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    for coordinate, value in {
        "B2": "{photo}",
        "B3": "{name}",
        "B4": "{type}",
        "B5": "{date}",
        "B6": "{hierarchy}",
        "H2": "Inspector signature",
    }.items():
        sheet[coordinate] = value
    sheet.print_area = "B2:H6"
    workbook.save(template)
    workbook.close()
    photo = tmp_path / "photo.png"
    Image.new("RGB", (2, 2), "white").save(photo)
    output = tmp_path / "result.xlsx"
    ExcelDocGenerator(template).generate(
        "report",
        [{"name": name, "photo": photo.name, "type": "before"} for name in ("one", "two")],
        output,
        workspace=str(tmp_path),
    )
    result = load_workbook(output)
    try:
        sheet = result.active
        assert sheet.print_area == "'Sheet'!$B$2:$H$11"
        assert sheet["H2"].value == sheet["H7"].value == "Inspector signature"
        assert sheet["B3"].value == "one" and sheet["B8"].value == "two"
        assert not {"{date}", "{hierarchy}"}.intersection(
            cell.value for row in sheet for cell in row
        )
    finally:
        result.close()


@pytest.fixture
def publisher():
    controller = GenerateController.__new__(GenerateController)
    controller.user_id, controller.project_id = "actor", "project"
    controller._GenerateController__s3_gen = Mock()
    return controller


def test_long_japanese_report_name_fits_s3_tags_and_remains_downloadable(publisher, tmp_path):
    s3 = publisher._GenerateController__s3_gen
    filename = "現場" * 80 + ".xlsx"
    path = tmp_path / "result.xlsx"
    path.write_bytes(b"workbook")

    def tagging(key, tags):
        # S3's service-side limit applies to each tag value, including base64 text.
        assert all(len(value) <= 256 for value in tags.values())
        s3.get_obj_tags.return_value = tags

    s3.put_obj_tags.side_effect = tagging
    key = publisher._publish(path, "project", "report-id", filename, "excel", False)
    s3.list_objs.return_value = [{"Key": key}]
    assert publisher.list()[0]["name"] == filename
    assert publisher.gen_download_url("report-id")["name"] == filename


def test_maximum_accepted_title_can_be_downloaded_after_extension_is_added(publisher):
    filename = "a" * 240 + ".xlsx"
    s3 = publisher._GenerateController__s3_gen
    s3.list_objs.return_value = [{"Key": "project/report-id/" + filename}]
    s3.get_obj_tags.return_value = {
        "project_id": "project",
        "generated_file_id": "report-id",
        "name": b64encode(filename.encode()).decode(),
    }
    assert publisher.gen_download_url("report-id")["name"] == filename


def test_utf8_object_key_limit_is_checked_before_publishing(publisher):
    s3 = publisher._GenerateController__s3_gen
    with pytest.raises(ValueError, match="key"):
        publisher._publish(Path("unused"), "p" * 80, "j" * 80, "🏗" * 240 + ".zip", "zip", False)
    s3.upload_obj.assert_not_called()
    s3.put_obj_tags.assert_not_called()
