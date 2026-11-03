"""Exercise the shipped workbook templates with the supported image/Excel libraries."""

from pathlib import Path
from zipfile import ZipFile

import pytest
from defusedxml.common import EntitiesForbidden
from openpyxl import Workbook, load_workbook
from PIL import Image

from harvest.utils.excel_doc_generator import ExcelDocGenerator


@pytest.mark.parametrize("template_number", [1, 2])
def test_shipped_template_generates_two_photos_without_temporary_image_leaks(
    tmp_path, template_number
):
    source = Path(__file__).parent / "fixtures" / f"basic_{template_number}.xlsx"
    image_path = tmp_path / "写真😀.png"
    Image.new("RGBA", (640, 480), (20, 100, 200, 128)).save(image_path)
    output = tmp_path / "施工報告😀.xlsx"
    document = ExcelDocGenerator(str(source))
    items = [
        {
            "photo": image_path.name,
            "type": "施工前",
            "name": "=1+1",
            "hierarchy": ["東京", "床"],
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "photo": image_path.name,
            "type": "施工後",
            "name": "写真😀",
            "hierarchy": ["東京", "壁"],
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    document.generate(
        "現場報告",
        items,
        str(output),
        needs_include_hierarchy=True,
        needs_print_settings=True,
        workspace=str(tmp_path),
    )
    workbook = load_workbook(output)
    sheet = workbook.active
    values = [cell.value for row in sheet for cell in row]
    assert "施工前" in values and "施工後" in values
    assert "写真😀" in values and "現場報告" in values
    formula_like_text = next(cell for row in sheet for cell in row if cell.value == "=1+1")
    assert formula_like_text.data_type == "s"
    assert sheet.print_area
    assert sheet.oddHeader.left.text == "現場報告"
    if template_number == 2:
        assert len(sheet.merged_cells.ranges) >= 2
    with ZipFile(output) as archive:
        assert len([name for name in archive.namelist() if name.startswith("xl/media/")]) == 2
    assert set(tmp_path.iterdir()) == {image_path, output}
    workbook.close()


def test_page_break_and_print_area_use_current_openpyxl_api(tmp_path):
    template = tmp_path / "print.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "現場, A"
    sheet.print_area = "B2:D9"
    workbook.save(template)
    document = ExcelDocGenerator(template)
    assert document.get_print_area_first_row() == 2
    assert document.get_print_area_last_row() == 9
    assert document.get_page_breaks(True) == []
    document.add_page_break(row=4)
    document.add_page_break(row=6)
    document.delete_page_break(4)
    document.save(template)
    reopened = ExcelDocGenerator(template)
    assert reopened.get_page_breaks(True) == [6]


def test_empty_report_is_rejected_before_modifying_workbook(tmp_path):
    template = Path(__file__).parent / "fixtures" / "basic_1.xlsx"
    document = ExcelDocGenerator(template)
    with pytest.raises(ValueError, match="At least one photo"):
        document.generate("empty", [], str(tmp_path / "empty.xlsx"))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "timestamp", ["2026-01-01T23:30:00", "2026-01-01T23:30:00Z", "2026-01-02T08:30:00+09:00"]
)
def test_report_date_interprets_legacy_naive_timestamps_as_utc(timestamp):
    assert ExcelDocGenerator().iso8601_to_simple(timestamp) == "2026/01/02"


def test_standard_zip_roundtrips_japanese_and_non_cp932_names(tmp_path):
    name = "報告書/写真😀𠮷.png"
    path = tmp_path / "report.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr(name, b"fixture")
    with ZipFile(path) as archive:
        assert archive.namelist() == [name]
        assert archive.getinfo(name).flag_bits & 0x800
        assert archive.read(name) == b"fixture"


def test_workbook_xml_entity_expansion_is_rejected(tmp_path):
    original = tmp_path / "original.xlsx"
    malicious = tmp_path / "malicious.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "fixture"
    workbook.save(original)
    with ZipFile(original) as source, ZipFile(malicious, "w") as destination:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                data = (
                    b'<!DOCTYPE worksheet [<!ENTITY expansion "forbidden entity">]>'
                    + data.replace(b">fixture<", b">&expansion;<")
                )
            destination.writestr(info, data)
    with pytest.raises(ValueError) as error:
        ExcelDocGenerator(malicious)
    assert isinstance(error.value.__cause__, EntitiesForbidden)
