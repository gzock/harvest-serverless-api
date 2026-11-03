"""Scoped report generation; temporary files are owned by a single invocation."""

import os
import re
import tempfile
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from harvest.controllers.photo_controller import PhotoController
from harvest.controllers.place_controller import PlaceController
from harvest.controllers.target_controller import TargetController
from harvest.controllers.template_controller import TemplateController
from harvest.drivers.s3_driver import S3Driver
from harvest.utils.excel_doc_generator import ExcelDocGenerator
from harvest.utils.sorters import date_sort

PHOTOS_BUCKET_NAME = os.environ.get("PHOTOS_BUCKET_NAME", "")
GENERATED_BUCKET_NAME = os.environ.get("GENERATED_BUCKET_NAME", "")
REPOSITORY_BUCKET_NAME = os.environ.get("REPOSITORY_BUCKET_NAME", "")


class CP932ZipInfo(ZipInfo):
    """Narrow compatibility adapter for the explicitly requested legacy format."""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp932", errors="strict"), self.flag_bits & ~0x800


class GenerateController:
    def __init__(self, host, port):
        self.photo = PhotoController(host, port)
        self.target = TargetController(host, port)
        self.place = PlaceController(host, port)
        self.__s3_photos = S3Driver(PHOTOS_BUCKET_NAME)
        self.__s3_gen = S3Driver(GENERATED_BUCKET_NAME)
        self.__s3_repo = S3Driver(REPOSITORY_BUCKET_NAME)
        self.__hierarchy_cache = {}
        self.user_id = self.project_id = None

    def set_user_id(self, user_id):
        self.user_id = user_id

    def set_project_id(self, project_id):
        self.project_id = project_id

    @staticmethod
    def _filename(value, max_length=240):
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > max_length
            or value in (".", "..")
            or re.search(r"[/\\\x00-\x1f\x7f]", value)
        ):
            raise ValueError("Invalid report filename")
        return value

    @staticmethod
    def _archive_segment(value):
        result = re.sub(r"[/\\:\x00-\x1f\x7f]", "_", str(value)).strip(". ")
        return result[:180] or "untitled"

    def list(self, project_id=None):
        project_id = project_id or self.project_id
        prefix = TemplateController._safe_id(project_id) + "/"
        result = []
        for obj in self.__s3_gen.list_objs(prefix):
            key = obj["Key"]
            remainder = key.removeprefix(prefix)
            if not key.startswith(prefix) or len(remainder.split("/")) != 2:
                continue
            file_id, filename = remainder.split("/")
            tags = self.__s3_gen.get_obj_tags(key)
            if tags.get("project_id") != project_id or tags.get("generated_file_id") != file_id:
                continue
            # The key already contains the complete Unicode filename. Legacy
            # base64 name tags cannot represent long titles within S3's tag limit.
            tags["name"] = filename
            result.append(tags)
        return result

    def gen_hierarchy(self, places, place_id):
        original_id = place_id
        if original_id in self.__hierarchy_cache:
            return self.__hierarchy_cache[original_id]
        hierarchy: list[str] = []
        seen: set[str] = set()
        for _ in range(5):
            if place_id not in places:
                break
            if place_id in seen:
                raise ValueError("Cyclic place hierarchy")
            seen.add(place_id)
            hierarchy.insert(0, places[place_id]["name"])
            place_id = places[place_id]["parent_place_id"]
        self.__hierarchy_cache[original_id] = hierarchy
        return hierarchy

    def convert_simple_place_dict(self, places):
        return {
            place["place_id"]: {"name": place["name"], "parent_place_id": place["parent_place_id"]}
            for place in places
        }

    def __get_photo_id_list(self, target, photo_type, all_photos=False):
        photos = target["photos"]
        if all_photos:
            return list(photos[photo_type])
        adopted = photos.get("adopt", {}).get(photo_type)
        if not adopted:
            return []
        if adopted not in photos[photo_type]:
            raise ValueError("Adopted photo is not owned by target")
        return [adopted]

    def __get_photo_list(
        self,
        project_id,
        by_name=True,
        needs_sort=True,
        reverse=False,
        needs_include_hierarchy=False,
        needs_all_photos=False,
        needs_date=False,
    ):
        targets = self.target.list(project_id)
        places = (
            self.convert_simple_place_dict(self.place.list(project_id))
            if needs_include_hierarchy
            else {}
        )
        result = []
        for target in targets:
            if target.get("project_id") != project_id:
                raise ValueError("Target is outside requested project")
            hierarchy = (
                self.gen_hierarchy(places, target["parent_place_id"])
                if needs_include_hierarchy
                else []
            )
            name = target["name"] if by_name else target["target_id"]
            for kind in ("before", "after"):
                for photo_id in self.__get_photo_id_list(target, kind, all_photos=needs_all_photos):
                    TemplateController._safe_id(photo_id)
                    created_at = target["updated_at"]
                    if needs_date:
                        tags = self.__s3_photos.get_obj_tags(photo_id)
                        created_at = tags.get("created_at", created_at)
                    result.append(
                        {
                            "name": name,
                            "type": ("施工前" if kind == "before" else "施工後")
                            if by_name
                            else kind,
                            "photo": photo_id,
                            "parent_place_id": target["parent_place_id"],
                            "created_at": created_at,
                            "hierarchy": hierarchy,
                        }
                    )
        return date_sort(result, key="created_at", reverse=reverse) if needs_sort else result

    def _publish(self, path, project_id, file_id, filename, kind, needs_download_url):
        key = f"{project_id}/{file_id}/{filename}"
        if len(key.encode("utf-8")) > 1024:
            raise ValueError("Generated object key exceeds S3's UTF-8 byte limit")
        tags = {
            "generated_file_id": file_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": self.user_id,
            "project_id": project_id,
            "type": kind,
        }
        encoded_name = b64encode(filename.encode("utf-8")).decode("ascii")
        if len(encoded_name) <= 256:
            tags["name"] = encoded_name  # Preserve existing tags where they fit.
        self.__s3_gen.upload_obj(path, key)
        self.__s3_gen.put_obj_tags(key, tags)
        if not needs_download_url:
            return key
        return {"download_url": self.__s3_gen.create_presigned_url(key), "name": filename}

    def gen_zip(
        self,
        project_id,
        title,
        by_name=True,
        needs_include_hierarchy=False,
        needs_make_dir=True,
        workspace="/tmp",
        needs_download_url=True,
        needs_date=False,
        char_enc="utf_8",
        needs_all_photos=False,
        output_id=None,
    ):
        TemplateController._safe_id(project_id)
        file_id = TemplateController._safe_id(output_id or str(uuid4()))
        filename = self._filename(title) + ".zip"
        if char_enc not in ("utf_8", "utf-8", "shift_jis", "cp932"):
            raise ValueError("Unsupported ZIP filename encoding")
        names = set()
        with tempfile.TemporaryDirectory(prefix="harvest-report-", dir=workspace) as directory:
            path = os.path.join(directory, "result.zip")
            with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                for photo in self.__get_photo_list(
                    project_id,
                    by_name=by_name,
                    needs_sort=False,
                    needs_include_hierarchy=needs_include_hierarchy,
                    needs_all_photos=needs_all_photos,
                    needs_date=needs_date,
                ):
                    name = (
                        self._archive_segment(photo["name"])
                        + "_"
                        + self._archive_segment(photo["type"])
                    )
                    if needs_include_hierarchy and photo["hierarchy"]:
                        parts = [self._archive_segment(value) for value in photo["hierarchy"]]
                        separator = "/" if needs_make_dir else "_"
                        name = separator.join(parts + [name])
                    if needs_date:
                        name += "_" + self.iso8601_to_simple(photo["created_at"])
                    candidate, suffix = name + ".jpg", 1
                    while candidate in names:
                        candidate = f"{name}_{suffix}.jpg"
                        suffix += 1
                    names.add(candidate)
                    info = (
                        CP932ZipInfo(candidate)
                        if char_enc in ("shift_jis", "cp932")
                        else ZipInfo(candidate)
                    )
                    info.compress_type = ZIP_DEFLATED
                    archive.writestr(info, self.__s3_photos.get_obj_bytes(photo["photo"]))
            return self._publish(path, project_id, file_id, filename, "zip", needs_download_url)

    def create_download_url(self, project_id, gen_type=None):
        if gen_type != "zip":
            raise ValueError("Unsupported generated file type")
        return self.gen_zip(project_id, title="photos_result")["download_url"]

    def gen_excel_doc(
        self,
        project_id,
        title,
        needs_sort=True,
        needs_include_hierarchy=False,
        needs_date=False,
        needs_print_settings=False,
        workspace="/tmp",
        template_id="basic_1.xlsx",
        needs_download_url=True,
        output_id=None,
    ):
        TemplateController._safe_id(project_id)
        TemplateController._safe_id(template_id)
        file_id = TemplateController._safe_id(output_id or str(uuid4()))
        filename = self._filename(title) + ".xlsx"
        templates = TemplateController()
        templates.set_user_id(self.user_id)
        templates.set_project_id(project_id)
        template_key = templates.get_template_path(template_id)
        with tempfile.TemporaryDirectory(prefix="harvest-report-", dir=workspace) as directory:
            template_path = os.path.join(directory, "template.xlsx")
            output_path = os.path.join(directory, "result.xlsx")
            photos = self.__get_photo_list(
                project_id,
                needs_sort=needs_sort,
                needs_include_hierarchy=needs_include_hierarchy,
                needs_date=needs_date,
            )
            for photo in photos:
                self.__s3_photos.download_obj(
                    photo["photo"], os.path.join(directory, photo["photo"])
                )
            self.__s3_repo.download_obj(template_key, template_path)
            with open(template_path, "rb") as stream:
                TemplateController.validate_archive(stream.read(8 * 1024 * 1024 + 1))
            excel = ExcelDocGenerator(template_path)
            excel.generate(
                title=title,
                items=photos,
                save_filename=output_path,
                needs_include_hierarchy=needs_include_hierarchy,
                needs_date=needs_date,
                needs_print_settings=needs_print_settings,
                workspace=directory,
            )
            return self._publish(
                output_path, project_id, file_id, filename, "excel", needs_download_url
            )

    def gen_download_url(self, generated_file_id, project_id=None):
        project_id = project_id or self.project_id
        TemplateController._safe_id(project_id)
        TemplateController._safe_id(generated_file_id)
        prefix = f"{project_id}/{generated_file_id}/"
        matches = []
        for obj in self.__s3_gen.list_objs(prefix):
            key = obj["Key"]
            if not key.startswith(prefix):
                continue
            filename = key[len(prefix) :]
            if "/" not in filename and filename.endswith((".zip", ".xlsx")):
                self._filename(filename, max_length=245)
                matches.append(key)
        if len(matches) != 1:
            raise ValueError("Generated file not found")
        key = matches[0]
        tags = self.__s3_gen.get_obj_tags(key)
        if (
            tags.get("project_id") != project_id
            or tags.get("generated_file_id") != generated_file_id
        ):
            raise ValueError("Generated file is outside requested scope")
        return {
            "download_url": self.__s3_gen.create_presigned_url(key),
            "name": key[len(prefix) :],
        }

    def iso8601_to_simple(self, iso8601):
        value = datetime.fromisoformat(iso8601.replace("Z", "+00:00"))
        if value.tzinfo is None:
            # Historical Lambda datetimes were UTC without an offset.
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone(timedelta(hours=9))).strftime("%Y%m%d%H%M%S")
