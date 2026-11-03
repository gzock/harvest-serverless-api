from base64 import b64encode
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from harvest.controllers.generate_controller import GenerateController
from harvest.controllers.template_controller import TemplateController


@pytest.fixture
def generator():
    obj = GenerateController.__new__(GenerateController)
    obj.user_id, obj.project_id = "user-a", "project-a"
    obj._GenerateController__hierarchy_cache = {}
    obj._GenerateController__s3_gen = Mock()
    obj._GenerateController__s3_repo = Mock()
    obj._GenerateController__s3_photos = Mock()
    obj._GenerateController__s3_photos.get_obj_bytes.return_value = b"photo-bytes"
    obj._GenerateController__get_photo_list = Mock(
        return_value=[
            {
                "photo": "photo-id",
                "name": "日本語",
                "type": "施工前",
                "hierarchy": ["../階層"],
                "created_at": "2026-10-22T00:00:00",
            }
        ]
    )
    return obj


def test_zip_is_scoped_and_cleans_work_files(generator, tmp_path):
    artifacts = {}
    generator._GenerateController__s3_gen.upload_obj.side_effect = lambda path, key: (
        artifacts.update({key: Path(path).read_bytes()})
    )
    key = generator.gen_zip(
        "project-a",
        title="現場写真",
        needs_include_hierarchy=True,
        output_id="job-a",
        workspace=str(tmp_path),
        needs_download_url=False,
    )
    assert key == "project-a/job-a/現場写真.zip"
    with ZipFile(BytesIO(artifacts[key])) as archive:
        assert archive.namelist() == ["_階層/日本語_施工前.jpg"]
        assert archive.read(archive.namelist()[0]) == b"photo-bytes"
    assert not list(tmp_path.iterdir())


def test_explicit_cp932_zip_compatibility(generator, tmp_path):
    artifacts = []
    generator._GenerateController__s3_gen.upload_obj.side_effect = lambda path, key: (
        artifacts.append(Path(path).read_bytes())
    )
    generator.gen_zip("project-a", "photos", char_enc="shift_jis", workspace=str(tmp_path))
    with ZipFile(BytesIO(artifacts[0]), metadata_encoding="cp932") as archive:
        assert archive.namelist() == ["日本語_施工前.jpg"]


def test_unsupported_cp932_character_fails_without_partial_artifact(generator, tmp_path):
    generator._GenerateController__get_photo_list.return_value[0]["name"] = "🏗️"
    with pytest.raises(UnicodeEncodeError):
        generator.gen_zip("project-a", "photos", char_enc="cp932", workspace=str(tmp_path))
    generator._GenerateController__s3_gen.upload_obj.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("title", ["../escape", "/tmp/escape", "a\\b", "bad\x00name", ""])
def test_title_traversal_rejected_before_read_or_write(generator, tmp_path, title):
    with pytest.raises(ValueError):
        generator.gen_zip("project-a", title, workspace=str(tmp_path))
    generator._GenerateController__s3_photos.get_obj_bytes.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_zip_download_failure_cleans_workspace(generator, tmp_path):
    generator._GenerateController__s3_photos.get_obj_bytes.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        generator.gen_zip("project-a", "report", workspace=str(tmp_path))
    assert not list(tmp_path.iterdir())
    generator._GenerateController__s3_gen.upload_obj.assert_not_called()


def test_generated_history_uses_legacy_key_and_ownership_tags(generator):
    s3 = generator._GenerateController__s3_gen
    s3.list_objs.return_value = [{"Key": "project-a/id-a/report.xlsx"}]
    s3.get_obj_tags.return_value = {
        "project_id": "project-a",
        "generated_file_id": "id-a",
        "name": b64encode("report.xlsx".encode()).decode(),
    }
    s3.create_presigned_url.return_value = "https://signed.invalid"
    assert generator.gen_download_url("id-a")["name"] == "report.xlsx"
    s3.list_objs.assert_called_once_with("project-a/id-a/")
    s3.create_presigned_url.assert_called_once_with("project-a/id-a/report.xlsx")


@pytest.mark.parametrize(
    "key,tags",
    [
        ("project-a/id-ab/report.xlsx", {"project_id": "project-a", "generated_file_id": "id-ab"}),
        ("project-a/id-a/report.xlsx", {"project_id": "project-b", "generated_file_id": "id-a"}),
        (
            "project-a/id-a/nested/report.xlsx",
            {"project_id": "project-a", "generated_file_id": "id-a"},
        ),
    ],
)
def test_generated_history_refuses_other_scope(generator, key, tags):
    s3 = generator._GenerateController__s3_gen
    s3.list_objs.return_value = [{"Key": key}]
    s3.get_obj_tags.return_value = tags
    with pytest.raises(ValueError):
        generator.gen_download_url("id-a")
    s3.create_presigned_url.assert_not_called()


def test_excel_download_failure_cleans_workspace(generator, tmp_path, monkeypatch):
    monkeypatch.setattr(TemplateController, "__init__", lambda self: None)
    monkeypatch.setattr(
        TemplateController,
        "get_template_path",
        lambda self, _: "templates/excel/defaults/basic_1.xlsx",
    )
    generator._GenerateController__s3_photos.download_obj.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        generator.gen_excel_doc("project-a", "report", workspace=str(tmp_path))
    assert not list(tmp_path.iterdir())
    generator._GenerateController__s3_gen.upload_obj.assert_not_called()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-10-22T00:00:00", "20261022090000"),
        ("2026-10-22T00:00:00Z", "20261022090000"),
        ("2026-10-22T09:00:00+09:00", "20261022090000"),
    ],
)
def test_report_dates_parse_legacy_and_timezone_aware(generator, value, expected):
    assert generator.iso8601_to_simple(value) == expected


def test_corrupt_adopted_photo_rejected(generator):
    with pytest.raises(ValueError):
        generator._GenerateController__get_photo_id_list(
            {"photos": {"before": ["owned"], "adopt": {"before": "foreign"}}}, "before"
        )
