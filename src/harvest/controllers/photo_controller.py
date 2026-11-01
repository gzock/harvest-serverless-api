"""Project-scoped photo operations with optimistic DynamoDB transactions."""

import copy
import os
import warnings
from base64 import b64encode
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlencode
from uuid import uuid4

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from harvest.controllers.place_controller import PlaceController
from harvest.controllers.role_controller import RoleController
from harvest.drivers.dynamodb_driver import DynamoDbDriver
from harvest.drivers.s3_driver import S3Driver
from harvest.exception import (
    AdoptedPhotoIsCanNotDeleteError,
    LastOnePhotoIsCanNotDeleteError,
    PhotoIsNotFoundError,
    PhotoSaveLimitExceededError,
)
from PIL import Image, UnidentifiedImageError

BUCKET_NAME = os.environ.get("PHOTOS_BUCKET_NAME", "")


class PhotoController(DynamoDbDriver):
    def __init__(self, host, port):
        super().__init__(host, port, "Targets")
        self.set_partition_key("project_id")
        self.set_sort_key("target_id")
        self.place = PlaceController(host, port)
        self.role = RoleController(host, port)
        self.__s3 = S3Driver(BUCKET_NAME)
        self.project_id = self.target_id = self.user_id = self.photo_id = None

    def set_project_id(self, project_id):
        self.project_id = project_id
        self.place.set_project_id(project_id)
        self.role.set_project_id(project_id)

    def set_user_id(self, user_id):
        self.user_id = user_id

    def set_target_id(self, target_id):
        self.target_id = str(target_id)

    def set_photo_id(self, photo_id):
        self.photo_id = photo_id

    def _target(self, target_id):
        if not self.project_id or not target_id:
            raise PhotoIsNotFoundError()
        target = super().get_item(self.project_id, target_id, consistent_read=True).get("Item")
        if (
            not target
            or target.get("project_id") != self.project_id
            or target.get("target_id") != target_id
            or target.get("status") == "deleting"
        ):
            raise PhotoIsNotFoundError()
        return target

    @staticmethod
    def _photo_type(target, photo_id):
        for kind in ("before", "after"):
            if photo_id in target.get("photos", {}).get(kind, []):
                return kind
        raise PhotoIsNotFoundError()

    def list(self, target_id):
        target = self._target(target_id)
        return copy.deepcopy(target["photos"])

    def show(self, photo_id, encode=False):
        target = self._target(self.target_id)
        self._photo_type(target, photo_id)
        data = self.__s3.get_obj_bytes(photo_id)
        return b64encode(data).decode("ascii") if encode else data

    @staticmethod
    def validate_image(data):
        if not isinstance(data, bytes) or not data or len(data) > 4 * 1024 * 1024:
            raise ValueError("Photo exceeds upload limit")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    if (
                        image.format not in ("JPEG", "PNG", "WEBP")
                        or image.width * image.height > 32_000_000
                    ):
                        raise ValueError("Unsupported or oversized photo")
                    image_format = image.format
                    image.verify()
                # JPEG verify() validates the container/header, not the compressed
                # pixel stream. Decode a fresh image before persisting bytes that
                # would otherwise fail only when a later report is generated.
                with Image.open(BytesIO(data)) as decoded:
                    decoded.load()
                return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[
                    image_format
                ]
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
        ):
            raise ValueError("Invalid photo") from None

    def _transaction(self, target, photos, increment_type=None):
        serialize = TypeSerializer().serialize
        now = datetime.now(timezone.utc).isoformat()
        values = {
            ":expected": target["photos"],
            ":photos": photos,
            ":at": now,
            ":actor": self.user_id,
            ":deleting": "deleting",
        }
        updates = [
            {
                "ConditionCheck": {
                    "TableName": os.environ.get("TABLE_PROJECTS", "Projects"),
                    "Key": {"project_id": {"S": self.project_id}},
                    "ConditionExpression": "attribute_exists(project_id) AND (attribute_not_exists(#status) OR (#status <> :deleting AND #status <> :importing))",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":deleting": {"S": "deleting"},
                        ":importing": {"S": "importing"},
                    },
                }
            },
            {
                "Update": {
                    "TableName": os.environ.get("TABLE_TARGETS", "Targets"),
                    "Key": {
                        "project_id": {"S": self.project_id},
                        "target_id": {"S": target["target_id"]},
                    },
                    "ConditionExpression": "attribute_exists(project_id) AND photos = :expected AND (attribute_not_exists(#status) OR #status <> :deleting)",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "UpdateExpression": "SET photos = :photos, updated_at = :at, updated_by = :actor",
                    "ExpressionAttributeValues": {
                        key: serialize(value) for key, value in values.items()
                    },
                }
            },
        ]
        if increment_type and target["parent_place_id"] != self.project_id:
            parent = self.place.show(target["parent_place_id"])
            hierarchy = [value for value in parent.get("hierarchy", "").split("#") if value]
            hierarchy.append(target["parent_place_id"])
            for place_id in dict.fromkeys(hierarchy):
                if place_id == self.project_id:
                    continue
                updates.append(
                    {
                        "Update": {
                            "TableName": os.environ.get("TABLE_PLACES", "Places"),
                            "Key": {
                                "project_id": {"S": self.project_id},
                                "place_id": {"S": place_id},
                            },
                            "ConditionExpression": "attribute_exists(project_id)",
                            "UpdateExpression": "SET photos.results.#kind = photos.results.#kind + :one, updated_at = :at, updated_by = :actor",
                            "ExpressionAttributeNames": {"#kind": increment_type},
                            "ExpressionAttributeValues": {
                                ":one": {"N": "1"},
                                ":at": {"S": now},
                                ":actor": {"S": self.user_id},
                            },
                        }
                    }
                )
        return {"TransactItems": updates, "ClientRequestToken": str(uuid4())}

    def _write(self, target, photos, increment_type=None):
        return self.client.transact_write_items(**self._transaction(target, photos, increment_type))

    def create(self, target_id, type, data, photo_id=None, *, finalization=None):
        if type not in ("before", "after") or not self.user_id:
            raise ValueError("Invalid photo request")
        target = self._target(target_id)
        idempotent = photo_id is not None
        if photo_id is not None:
            for kind in ("before", "after"):
                if photo_id in target["photos"][kind]:
                    if kind != type:
                        raise PhotoIsNotFoundError()
                    if finalization is not None:
                        # Recover older sessions whose photo committed before the
                        # finalization response. The snapshot and lease still
                        # participate in one transaction; never resurrect a photo
                        # after a later successful finalize/delete sequence.
                        transaction = self._transaction(target, target["photos"])
                        transaction["TransactItems"].append(finalization)
                        self.client.transact_write_items(**transaction)
                    return {"message": "successfully"}
        if len(target["photos"][type]) >= 5:
            raise PhotoSaveLimitExceededError()
        self.validate_image(data)
        photo_id = photo_id or str(uuid4())
        tags = urlencode(
            {
                "project_id": self.project_id,
                "target_id": target_id,
                "user_id": self.user_id,
                "type": type,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        photos = copy.deepcopy(target["photos"])
        photos[type].append(photo_id)
        photos.setdefault("adopt", {})[type] = photo_id
        transaction = self._transaction(
            target, photos, increment_type=type if len(target["photos"][type]) == 0 else None
        )
        if finalization is not None:
            transaction["TransactItems"].append(finalization)
        self.__s3.put_obj(photo_id, data, tags)
        try:
            return self.client.transact_write_items(**transaction)
        except ClientError as error:
            # A cancelled/conditionally rejected transaction cannot have committed.
            # For transport timeouts the outcome is unknown: retain the object so
            # a committed DB reference can never point to a deleted image.
            if not idempotent and error.response.get("Error", {}).get("Code") in (
                "TransactionCanceledException",
                "ConditionalCheckFailedException",
                "ValidationException",
            ):
                self.__s3.delete_obj(photo_id)
            raise

    def update_adopt(self, target_id, type, photo_id):
        if type not in ("before", "after"):
            raise ValueError("Invalid photo type")
        target = self._target(target_id)
        if self._photo_type(target, photo_id) != type:
            raise PhotoIsNotFoundError()
        photos = copy.deepcopy(target["photos"])
        photos.setdefault("adopt", {})[type] = photo_id
        return self._write(target, photos)

    def delete(self, target_id, photo_id):
        target = self._target(target_id)
        kind = self._photo_type(target, photo_id)
        if len(target["photos"][kind]) <= 1:
            raise LastOnePhotoIsCanNotDeleteError()
        if target["photos"].get("adopt", {}).get(kind) == photo_id:
            raise AdoptedPhotoIsCanNotDeleteError()
        photos = copy.deepcopy(target["photos"])
        photos[kind].remove(photo_id)
        result = self._write(target, photos)
        self.__s3.delete_obj(photo_id)
        return result
