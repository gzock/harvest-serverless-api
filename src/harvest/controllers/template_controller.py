import logging
import os
import re
import tempfile
import urllib.parse
import zipfile
from base64 import b64decode, b64encode
from io import BytesIO
from uuid import uuid4

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.print_settings import PrintArea

from harvest.drivers.s3_driver import S3Driver
from harvest.exception import (
    DuplicateTemplateNameExistsError,
    NeedsPropertyNotExistsError,
    PageBreaksCountInvalidError,
    PrintAreaNotExistsError,
    SettingPropertyCountInvalidError,
    SettingPropertyPositionInvalidError,
    TemplateMaxCountExceededError,
    TemplateNotFoundError,
    TemplateTypeInvalidError,
)
from harvest.utils.excel_doc_generator import ExcelDocGenerator

REPOSITORY_BUCKET_NAME = os.environ.get("REPOSITORY_BUCKET_NAME", "")

BASE_TEMPLATES_PATH = "templates/excel/"
DEFAULT_TEMPLATES_PATH = BASE_TEMPLATES_PATH + "defaults/"
USERS_TEMPLATES_PATH = BASE_TEMPLATES_PATH + "users/"
PROJECTS_TEMPLATES_PATH = BASE_TEMPLATES_PATH + "projects/"

BASE_LOCAL_WORKSPACE_PATH = "/tmp"
LOCAL_WORKSPACE_PATH = os.path.join(BASE_LOCAL_WORKSPACE_PATH, str(uuid4()))


class TemplateController:
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    user_id = None
    project_id = None
    template_id = None

    def __init__(self):
        self.__repo = S3Driver(REPOSITORY_BUCKET_NAME)

    def set_user_id(self, user_id):
        self.user_id = user_id

    def set_project_id(self, project_id):
        self.project_id = project_id

    @staticmethod
    def _safe_id(value):
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_.@+-]+", value)
            or value in (".", "..")
        ):
            raise TemplateNotFoundError()
        return value

    def get_template_path(self, template_id, include_defaults=True):
        template_id = self._safe_id(template_id)
        prefixes = []
        if self.user_id:
            prefixes.append(USERS_TEMPLATES_PATH + self._safe_id(self.user_id) + "/")
        if self.project_id:
            prefixes.append(PROJECTS_TEMPLATES_PATH + self._safe_id(self.project_id) + "/")
        if include_defaults:
            prefixes.append(DEFAULT_TEMPLATES_PATH)
        for prefix in prefixes:
            key = prefix + template_id
            if any(obj["Key"] == key for obj in self.__repo.list_objs(key)):
                return key
        raise TemplateNotFoundError()

    def get(self, template_id, needs_download_url=False):
        template_path = self.get_template_path(template_id)
        if needs_download_url:
            return self.__repo.create_presigned_url(template_path)
        # Consumers of this compatibility method own the returned temporary file.
        fd, local_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            self.__repo.download_obj(template_path, local_path)
        except Exception:
            os.unlink(local_path)
            raise
        return local_path

    def list(self, user_id=None, project_id=None):
        if not user_id:
            user_id = self.user_id
        if not project_id:
            project_id = self.project_id

        default_tmpl = self.list_default_templates()
        ret = {"default": default_tmpl}
        if user_id:
            user_tmpl = self.list_user_templates(user_id)
            ret.update({"user": user_tmpl})

        if project_id:
            project_tmpl = self.list_project_templates(project_id)
            ret.update({"project": project_tmpl})

        return ret

    def __list_templates(self, template_type, any_id=None):
        if template_type == "default":
            path = DEFAULT_TEMPLATES_PATH
        elif template_type == "user" and any_id:
            path = USERS_TEMPLATES_PATH + self._safe_id(any_id) + "/"
        elif template_type == "project" and any_id:
            path = PROJECTS_TEMPLATES_PATH + self._safe_id(any_id) + "/"
        else:
            raise TemplateTypeInvalidError

        ret = []
        objs = self.__repo.list_objs(path)
        template_names = [
            obj["Key"]
            for obj in objs
            if obj["Key"].startswith(path)
            and obj["Key"] != path
            and "/" not in obj["Key"][len(path) :]
        ]
        for name in template_names:
            tags = self.__repo.get_obj_tags(name)
            tags.update({"template_id": name.split("/")[-1]})

            for key in ["name", "description"]:
                tags[key] = b64decode(tags.get(key, "")).decode("utf-8")
            ret.append(tags)
        return ret

    def list_default_templates(self):
        return self.__list_templates("default")

    def list_user_templates(self, user_id):
        return self.__list_templates("user", user_id)

    def list_project_templates(self, project_id):
        # path = PROJECTS_TEMPLATES_PATH + project_id
        # ret = self.__repo.list_objs(path)
        # return [ obj["Key"].split("/")[-1] for obj in ret if obj["Key"] != DEFAULT_TEMPLATES_PATH ]
        return self.__list_templates("project", project_id)

    def validate(self, template_path, property_title, property_hierarchy, property_date):
        # webappでのアップロード時に付けられたテンプレートオプションが本当にそのテンプレートで使えるのかを検証する (orderをベースにする？)
        # $hierarchyがあるか？とか$dateがあるか？みたいな
        # validate失敗なら例外を返してwebapp側でその旨をユーザーに伝える必要がある
        # validate成功ならs3のobject-tagとして付与して保存 (保存自体は別のメソッドが担当するが)

        excel = ExcelDocGenerator(template_path)
        if not excel.has_name():
            raise NeedsPropertyNotExistsError("{name}")
        if not excel.has_photo():
            raise NeedsPropertyNotExistsError("{photo}")
        if not excel.has_type():
            raise NeedsPropertyNotExistsError("{type}")

        if property_title and not excel.has_title():
            raise NeedsPropertyNotExistsError("{title}")

        if property_hierarchy and not excel.has_hierarchy():
            raise NeedsPropertyNotExistsError("{hierarchy}")

        if property_date and not excel.has_date():
            raise NeedsPropertyNotExistsError("{date}")

        page_breaks = excel.get_page_breaks(row_num_only=True)
        if len(page_breaks) not in [0, 1]:
            raise PageBreaksCountInvalidError

        print_area = excel.get_print_area()
        if not print_area:
            raise PrintAreaNotExistsError
        min_col, min_row, max_col, max_row = range_boundaries(print_area)

        aggregate_cells = excel.aggregate_cell_values(2)
        placeholders = {"{name}", "{photo}", "{type}", "{title}", "{hierarchy}", "{date}"}
        for key, values in aggregate_cells.items():
            if key not in placeholders:
                continue  # Static labels, including repeated labels, are valid.
            if len(values) != 1:
                raise SettingPropertyCountInvalidError(key, len(values))
            for cell in values:
                if not (min_row <= cell.row <= max_row and min_col <= cell.column <= max_col):
                    raise SettingPropertyPositionInvalidError
                if cell.value != "{title}" and len(page_breaks) == 1 and cell.row <= page_breaks[0]:
                    raise SettingPropertyPositionInvalidError

        return True

    MAX_TEMPLATE_CELLS = 100_000
    MAX_TEMPLATE_ROWS = 10_000
    MAX_TEMPLATE_COLUMNS = 256

    @classmethod
    def _bounded_range(cls, reference):
        try:
            bounds = range_boundaries(reference)
            min_col, min_row, max_col, max_row = bounds
            if (
                any(value is None for value in bounds)
                or min_col < 1
                or min_row < 1
                or max_col < min_col
                or max_row < min_row
                or max_col > cls.MAX_TEMPLATE_COLUMNS
                or max_row > cls.MAX_TEMPLATE_ROWS
                or max_col * max_row > cls.MAX_TEMPLATE_CELLS
            ):
                raise ValueError
            return (max_col - min_col + 1) * (max_row - min_row + 1)
        except TypeError, ValueError:
            raise ValueError("Template cell range exceeds processing limits") from None

    @classmethod
    def _validate_xml_ranges(cls, archive):
        # A tiny ZIP can declare a million-row merge/dimension. Check coordinates
        # before openpyxl creates cells or iterates a rectangular worksheet.
        sheets = [
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ]
        if len(sheets) > 32:
            raise ValueError("Template contains too many worksheets")
        total_cells = 0
        for name in ["xl/workbook.xml", *sheets]:
            if archive.getinfo(name).file_size > 8 * 1024 * 1024:
                raise ValueError("Template XML exceeds processing limits")
            root = ElementTree.fromstring(archive.read(name))
            extent_row = extent_col = merges = cells = 0
            for node in root.iter():
                kind = node.tag.rsplit("}", 1)[-1]
                if kind in ("dimension", "mergeCell", "c"):
                    reference = node.get("r" if kind == "c" else "ref")
                    if not reference:
                        raise ValueError("Invalid template coordinate")
                    area = cls._bounded_range(reference)
                    _, _, last_col, last_row = range_boundaries(reference)
                    extent_row, extent_col = max(extent_row, last_row), max(extent_col, last_col)
                    if kind == "mergeCell":
                        merges += area
                    if kind == "c":
                        cells += 1
                    if max(merges, cells, extent_row * extent_col) > cls.MAX_TEMPLATE_CELLS:
                        raise ValueError("Template cell range exceeds processing limits")
                elif kind == "row" and int(node.get("r", "0")) > cls.MAX_TEMPLATE_ROWS:
                    raise ValueError("Template row exceeds processing limits")
                elif kind == "col" and int(node.get("max", "0")) > 16384:
                    raise ValueError("Template column exceeds processing limits")
                elif kind == "definedName" and node.get("name") == "_xlnm.Print_Area":
                    areas = PrintArea.from_string(node.text or "").ranges
                    if not areas:
                        raise ValueError("Invalid template print area")
                    for area in areas:
                        cls._bounded_range(str(area))
            total_cells += max(merges, cells, extent_row * extent_col)
            if total_cells > cls.MAX_TEMPLATE_CELLS:
                raise ValueError("Workbook total cell range exceeds processing limits")

    @staticmethod
    def validate_archive(data):
        if not isinstance(data, bytes) or len(data) > 8 * 1024 * 1024:
            raise ValueError("Template exceeds upload limit")
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                names = archive.namelist()
                if (
                    len(entries) > 4096
                    or len(set(names)) != len(names)
                    or sum(item.file_size for item in entries) > 64 * 1024 * 1024
                    or any(item.flag_bits & 1 for item in entries)
                    or "[Content_Types].xml" not in archive.namelist()
                    or "xl/workbook.xml" not in archive.namelist()
                ):
                    raise ValueError("Invalid or oversized workbook")
                TemplateController._validate_xml_ranges(archive)
        except zipfile.BadZipFile, ElementTree.ParseError, DefusedXmlException:
            raise ValueError("Invalid workbook") from None

    def create(
        self,
        template_type,
        template_data,
        parent_id,
        name,
        property_title,
        property_hierarchy,
        property_date,
        description=None,
        base64enc=False,
    ):
        if template_type == "user" and parent_id == self.user_id:
            upload_prefix = USERS_TEMPLATES_PATH + self._safe_id(parent_id) + "/"
            existing = self.list_user_templates(parent_id)
        elif template_type == "project" and parent_id == self.project_id:
            upload_prefix = PROJECTS_TEMPLATES_PATH + self._safe_id(parent_id) + "/"
            existing = self.list_project_templates(parent_id)
        else:
            raise TemplateTypeInvalidError()
        if len(existing) >= 5:
            raise TemplateMaxCountExceededError()
        if name in [item["name"] for item in existing]:
            raise DuplicateTemplateNameExistsError()
        if base64enc:
            if not isinstance(template_data, str) or len(template_data) > 12 * 1024 * 1024:
                raise ValueError("Invalid template data")
            template_data = b64decode(template_data, validate=True)
        self.validate_archive(template_data)
        new_id = str(uuid4())
        with tempfile.TemporaryDirectory(prefix="harvest-template-") as workspace:
            local_path = os.path.join(workspace, new_id + ".xlsx")
            with open(local_path, "wb") as stream:
                stream.write(template_data)
            self.validate(local_path, property_title, property_hierarchy, property_date)
            self.__repo.put_obj(
                upload_prefix + new_id,
                template_data,
                self.gen_tags(
                    name,
                    self._safe_id(self.user_id),
                    property_title,
                    property_hierarchy,
                    property_date,
                    description,
                ),
            )
        return {"message": "successfully"}

    def delete(self, template_id):
        key = self.get_template_path(template_id, include_defaults=False)
        return self.__repo.delete_obj(key)

    def gen_tags(
        self,
        name: str,
        user_id: str,
        title: bool,
        hierarchy: bool,
        date: bool,
        description: str | None = None,
    ) -> str:
        # TODO: type(user|project)っているかな？
        # TODO: created_atってなくても困らないよね？updateを許さないならlast_modifyで判断可能だし、作成日時を誰かが必要とするか？という話もあるし
        # TODO: ownerも困らないよね？prefixから判断できるし
        tags = [
            "name=%s" % urllib.parse.quote(b64encode(name.encode("utf-8")).decode("utf-8")),
            "created_by=%s" % user_id,
            "property_title=%s" % str(title),
            "property_hierarchy=%s" % str(hierarchy),
            "property_date=%s" % str(date),
        ]
        if description:
            tags.append(
                "description=%s"
                % urllib.parse.quote(b64encode(description.encode("utf-8")).decode("utf-8"))
            )
        return "&".join(tags)
