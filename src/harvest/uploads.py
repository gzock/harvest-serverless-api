"""Version-pinned quarantine uploads with resumable, idempotent finalization."""

import hashlib
import json
import os
import time
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeSerializer

from harvest.auth import Auth
from harvest.controllers.photo_controller import PhotoController
from harvest.exception import ActionDeniedError, PhotoSaveLimitExceededError


class UploadRateLimited(ValueError):
    status_code = 429


class UploadConflict(ValueError):
    status_code = 409


class UploadExpired(ValueError):
    status_code = 410


class UploadNotFound(ValueError):
    status_code = 404


class UploadService:
    MAX_BYTES = 4 * 1024 * 1024
    CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
    SESSION_SECONDS = 900
    LEASE_SECONDS = 90

    def __init__(self, host=None, port=None, *, table=None, s3=None, clock=None):
        self.host, self.port = host, port
        self.bucket = os.environ.get("UPLOAD_BUCKET_NAME", "")
        self.table = (
            table
            if table is not None
            else boto3.resource("dynamodb").Table(os.environ["UPLOAD_SESSIONS_TABLE"])
        )
        self.s3 = s3 if s3 is not None else boto3.client("s3")
        self.clock = clock or time.time
        self.actor_hourly_limit = self._positive_setting("UPLOAD_ACTOR_HOURLY_LIMIT", 100)
        self.target_window_limit = self._positive_setting("UPLOAD_TARGET_WINDOW_LIMIT", 10)
        self.target_window_seconds = self._positive_setting("UPLOAD_TARGET_WINDOW_SECONDS", 900)

    @staticmethod
    def _positive_setting(name, default):
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError:
            raise RuntimeError("Invalid upload quota configuration") from None
        if value < 1:
            raise RuntimeError("Invalid upload quota configuration")
        return value

    def _store_session(self, item, now):
        """Consume both fixed-window quotas and store the session atomically.

        Quota expiration is enforced by the window key, not DynamoDB's delayed
        TTL cleanup. Counts are deliberately not refunded: retrying rejected or
        abandoned uploads must not permit unlimited quarantine storage.
        """
        table_name = self.table.name

        def quota(kind, identity, window, limit):
            digest = hashlib.sha256(
                json.dumps(identity, separators=(",", ":")).encode()
            ).hexdigest()
            start = (now // window) * window
            return {
                "Update": {
                    "TableName": table_name,
                    "Key": {"session_id": f"quota:{kind}:{digest}:{start}"},
                    "UpdateExpression": "SET #ttl = :ttl ADD #count :one",
                    "ConditionExpression": "attribute_not_exists(#count) OR #count < :limit",
                    "ExpressionAttributeNames": {"#count": "count", "#ttl": "ttl"},
                    "ExpressionAttributeValues": {
                        ":ttl": start + window + 86400,
                        ":one": 1,
                        ":limit": limit,
                    },
                }
            }

        actions = [
            quota("actor", [item["actor_id"]], 3600, self.actor_hourly_limit),
            quota(
                "target",
                [item["actor_id"], item["project_id"], item["target_id"]],
                self.target_window_seconds,
                self.target_window_limit,
            ),
            {
                "Put": {
                    "TableName": table_name,
                    "Item": item,
                    "ConditionExpression": "attribute_not_exists(session_id)",
                }
            },
        ]
        try:
            # This client belongs to a DynamoDB Table resource and serializes
            # native Python values into AttributeValues, including transactions.
            self.table.meta.client.transact_write_items(
                TransactItems=actions, ClientRequestToken=item["session_id"]
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            reasons = error.response.get("CancellationReasons", [])
            if any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons[:2]):
                raise UploadRateLimited("Upload session quota exceeded; try again later") from None
            if len(reasons) > 2 and reasons[2].get("Code") == "ConditionalCheckFailed":
                raise UploadConflict("Upload session already exists") from None
            raise

    def _authorize(self, actor_id, project_id, target_id):
        auth = Auth(self.host, self.port, actor_id, project_id)
        if not actor_id or not auth.guard("reporter"):
            raise ActionDeniedError()
        photo = PhotoController(self.host, self.port)
        photo.set_project_id(project_id)
        photo.set_target_id(target_id)
        photo.set_user_id(actor_id)
        photo._target(target_id)
        return photo

    def create(self, actor_id, project_id, target_id, type, content_type, size):
        if (
            type not in ("before", "after")
            or content_type not in self.CONTENT_TYPES
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 < size <= self.MAX_BYTES
        ):
            raise ValueError("Invalid upload size or image type")
        photo = self._authorize(actor_id, project_id, target_id)
        if len(photo._target(target_id)["photos"][type]) >= 5:
            raise PhotoSaveLimitExceededError()
        now = int(self.clock())
        session_id, photo_id = str(uuid4()), str(uuid4())
        key = f"quarantine/{project_id}/{target_id}/{session_id}"
        item = {
            "session_id": session_id,
            "photo_id": photo_id,
            "actor_id": actor_id,
            "project_id": project_id,
            "target_id": target_id,
            "type": type,
            "content_type": content_type,
            "size": size,
            "object_key": key,
            "phase": "pending",
            "expires_at": now + self.SESSION_SECONDS,
            # Retain finalization metadata after URL expiry for retry/cleanup.
            "ttl": now + 7 * 86400,
        }
        self._store_session(item, now)
        upload = self.s3.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", size, size]],
            ExpiresIn=self.SESSION_SECONDS,
        )
        return {
            "session_id": session_id,
            "photo_id": photo_id,
            "expires_at": item["expires_at"],
            "upload": upload,
        }

    def _session(self, actor_id, project_id, target_id, session_id):
        item = self.table.get_item(Key={"session_id": session_id}, ConsistentRead=True).get("Item")
        if not item:
            raise UploadNotFound("Upload session not found")
        if any(
            item.get(key) != value
            for key, value in (
                ("actor_id", actor_id),
                ("project_id", project_id),
                ("target_id", target_id),
            )
        ):
            raise ActionDeniedError()
        return item

    @staticmethod
    def _result(item):
        return {"message": "successfully", "photo_id": item["photo_id"]}

    def complete(self, actor_id, project_id, target_id, session_id, version_id=None):
        photo = self._authorize(actor_id, project_id, target_id)
        item = self._session(actor_id, project_id, target_id, session_id)
        if item["phase"] == "completed":
            return self._result(item)
        now = int(self.clock())
        if int(item["ttl"]) <= now or (
            int(item["expires_at"]) <= now and not item.get("pinned_version")
        ):
            raise UploadExpired("Upload session expired")
        if version_id is not None and (not isinstance(version_id, str) or not version_id):
            raise ValueError("Invalid upload version")
        pinned = item.get("pinned_version")
        if pinned and version_id and pinned != version_id:
            raise UploadConflict("Upload version is already fixed")
        args = {"Bucket": self.bucket, "Key": item["object_key"]}
        if pinned or version_id:
            args["VersionId"] = pinned or version_id
        head = self.s3.head_object(**args)
        version = head.get("VersionId")
        if not version or version == "null":
            raise ValueError("Versioned upload storage is required")
        if (
            int(head["ContentLength"]) != int(item["size"])
            or head.get("ContentType") != item["content_type"]
        ):
            raise ValueError("Uploaded object does not match the upload session")
        if pinned and version != pinned:
            raise UploadConflict("Upload version does not match the session")
        lease = str(uuid4())
        try:
            self.table.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET phase = :processing, pinned_version = if_not_exists(pinned_version, :version), lease_token = :lease, lease_until = :until",
                ConditionExpression="phase <> :completed AND #ttl > :now AND (expires_at > :now OR attribute_exists(pinned_version)) AND (attribute_not_exists(lease_until) OR lease_until <= :now) AND (attribute_not_exists(pinned_version) OR pinned_version = :version)",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":processing": "processing",
                    ":completed": "completed",
                    ":version": version,
                    ":lease": lease,
                    ":until": now + self.LEASE_SECONDS,
                    ":now": now,
                },
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            latest = self._session(actor_id, project_id, target_id, session_id)
            if latest["phase"] == "completed":
                return self._result(latest)
            raise UploadConflict("Upload is being finalized; retry this session") from None
        try:
            response = self.s3.get_object(
                Bucket=self.bucket, Key=item["object_key"], VersionId=version
            )
            stream = response["Body"]
            try:
                data = stream.read(self.MAX_BYTES + 1)
            finally:
                stream.close()
            if len(data) != int(item["size"]):
                raise ValueError("Uploaded object length changed")
            # Never trust the uploaded MIME header or extension.
            if PhotoController.validate_image(data) != item["content_type"]:
                raise ValueError("Uploaded image format does not match its content type")
            # Stable server-generated ID makes retry after an unknown DB outcome
            # safe. The finalized bucket is never writable by the browser.
            # The session's lease and terminal state are part of the same
            # transaction as photo membership/counters. A stale worker cannot
            # publish after another worker completed (or the photo was deleted).
            serialize = TypeSerializer().serialize
            finalization = {
                "Update": {
                    "TableName": self.table.name,
                    "Key": {"session_id": serialize(session_id)},
                    "UpdateExpression": "SET phase = :completed, completed_at = :now REMOVE lease_token, lease_until",
                    "ConditionExpression": "phase = :processing AND lease_token = :lease AND pinned_version = :version AND lease_until > :now AND #ttl > :now",
                    "ExpressionAttributeNames": {"#ttl": "ttl"},
                    "ExpressionAttributeValues": {
                        key: serialize(value)
                        for key, value in {
                            ":completed": "completed",
                            ":processing": "processing",
                            ":now": int(self.clock()),
                            ":lease": lease,
                            ":version": version,
                        }.items()
                    },
                }
            }
            photo.create(
                target_id, item["type"], data, photo_id=item["photo_id"], finalization=finalization
            )
        except Exception:
            # Preserve the pinned version. A retried upload may not replace bytes
            # already written to the final bucket by a previous invocation.
            try:
                self.table.update_item(
                    Key={"session_id": session_id},
                    UpdateExpression="REMOVE lease_token, lease_until",
                    ConditionExpression="lease_token = :lease AND phase <> :completed",
                    ExpressionAttributeValues={":lease": lease, ":completed": "completed"},
                )
            except ClientError:
                pass  # A newer/completed lease must not be overwritten.
            raise
        # Quarantine versions are lifecycle-managed, including abandoned uploads.
        return self._result(item)
