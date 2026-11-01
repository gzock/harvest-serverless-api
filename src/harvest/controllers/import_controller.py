"""Resumable CSV imports fenced by a project lock and an atomic journal cursor.

A failed invocation never compensates by deleting rows. The same CSV resumes the
persisted plan (including generated IDs) after the lease expires. All normal
project operations reject the importing state until every batch is published.
"""

from base64 import b64decode
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from uuid import uuid4
from typing import TypedDict

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from harvest.drivers.csv_driver import CsvDriver, MAX_CSV_BYTES
from harvest.drivers.dynamodb_driver import resolve_table_name
from harvest.utils.csv_optimizer import CsvOptimizer
from harvest.controllers.place_controller import PlaceController


class ImportEntry(TypedDict):
    table: str
    key: str
    item: dict[str, object]


class ImportInProgressError(ValueError):
    status_code = 409
    code = 409


class ImportPaused(RuntimeError):
    """A recovery invocation exhausted its time budget; retry the durable plan."""


class ImportController:
    CHUNK_SIZE = 75
    LEASE_SECONDS = 90
    RETENTION_SECONDS = 30 * 86400
    project_id = None
    import_digest = None
    user_id = None

    def __init__(self, host, port, filename=None):
        self.place_controller = PlaceController(host, port)
        if filename:
            self.open_csv(filename)

    def set_project_id(self, project_id):
        self.project_id = project_id

    def set_user_id(self, user_id):
        self.user_id = user_id

    def open_csv(self, filename, workspace="/tmp", base64enc=False):
        if base64enc:
            if not isinstance(filename, str) or len(filename) > 1400000:
                raise ValueError("Invalid CSV payload")
            data = b64decode(filename, validate=True)
        else:
            with open(filename, "rb") as source:
                data = source.read(MAX_CSV_BYTES + 1)
        rows = CsvDriver().parse(data)
        self.import_digest = sha256(data).hexdigest()
        csv = CsvOptimizer(items=rows)
        csv.set_project_id(self.project_id)
        self.places, self.targets = csv.optimize()
        return self.places, self.targets

    def convert(self, places=None, targets=None, rewrite=True):
        if not self.project_id:
            raise ValueError("Project is required")
        places = self.places if places is None else places
        targets = self.targets if targets is None else targets
        if any(item["project_id"] != self.project_id for item in places + targets):
            raise ValueError("Import project mismatch")
        converted_places = [
            self.make_place_obj(
                p["place_id"],
                p["parent_place_id"],
                p["hierarchy"],
                p["name"],
                p["photos_required_num"],
            )
            for p in places
        ]
        converted_targets = [
            self.make_target_obj(t["target_id"], t["parent_place_id"], t["name"]) for t in targets
        ]
        if rewrite:
            self.places, self.targets = converted_places, converted_targets
        return converted_places, converted_targets

    @property
    def journal(self):
        return self.place_controller.dynamodb.Table(resolve_table_name("Imports"))

    @staticmethod
    def _serialize(values):
        serializer = TypeSerializer()
        return {key: serializer.serialize(value) for key, value in values.items()}

    def _key(self, operation):
        return {"project_id": self.project_id, "operation_id": operation}

    def _read(self, operation):
        return self.journal.get_item(Key=self._key(operation), ConsistentRead=True).get("Item")

    def _transact(self, actions):
        return self.place_controller.client.transact_write_items(TransactItems=actions)

    @staticmethod
    def _completed_expired(item, now):
        return item.get("phase") == "completed" and item.get("ttl", now + 1) <= now

    def _stage(self, entries, operation):
        now = int(time.time())
        existing = self._read(operation)
        if existing and not self._completed_expired(existing, now):
            return existing, None
        records = []
        for offset in range(0, len(entries), self.CHUNK_SIZE):
            record = {
                **self._key(f"{operation}#{offset // self.CHUNK_SIZE}"),
                "entries": entries[offset : offset + self.CHUNK_SIZE],
            }
            # Leave ample headroom below DynamoDB's item/transaction byte limits.
            if len(json.dumps(record, ensure_ascii=False).encode()) > 300_000:
                raise ValueError("Import batch exceeds the journal size limit")
            records.append(record)
        meta = {
            **self._key(operation),
            "phase": "writing",
            "cursor": 0,
            "chunks": len(records),
            "count": len(entries),
            "actor_id": self.user_id or "",
            "created_at": int(time.time()),
        }
        actions = [
            {
                "Put": {
                    "TableName": resolve_table_name("Imports"),
                    "Item": self._serialize(record),
                    # TTL deletion is asynchronous and can remove metadata and
                    # chunks in either order. Replace expired completed plans
                    # atomically with the project lock, preserving active plans.
                    "ConditionExpression": "attribute_not_exists(project_id) OR #ttl <= :now",
                    "ExpressionAttributeNames": {"#ttl": "ttl"},
                    "ExpressionAttributeValues": self._serialize({":now": now}),
                }
            }
            for record in [meta, *records]
        ]
        lease = str(uuid4())
        try:
            self._transact([self._project_action(operation, lease, acquire=True), *actions])
        except ClientError as error:
            # A concurrent identical request may have atomically staged its plan.
            existing = self._read(operation)
            if existing and not self._completed_expired(existing, now):
                return existing, None
            if error.response["Error"]["Code"] == "TransactionCanceledException":
                raise ImportInProgressError("Project is busy; retry the same CSV later") from error
            raise
        return meta, lease

    def _project_action(self, operation, lease, acquire=False):
        values = {
            ":operation": operation,
            ":lease": lease,
            ":importing": "importing",
            ":until": int(time.time()) + self.LEASE_SECONDS,
        }
        if acquire:
            values.update({":now": int(time.time()), ":active": "active"})
            condition = (
                "attribute_exists(project_id) AND "
                "(attribute_not_exists(#status) OR #status = :active OR "
                "(#status = :importing AND import_operation = :operation "
                "AND (import_lease_until < :now OR import_lease = :lease)))"
            )
            expression = "SET #status = :importing, import_operation = :operation, import_lease = :lease, import_lease_until = :until"
        else:
            condition = (
                "#status = :importing AND import_operation = :operation AND import_lease = :lease"
            )
            expression = "SET import_lease_until = :until"
        return {
            "Update": {
                "TableName": resolve_table_name("Projects"),
                "Key": self._serialize({"project_id": self.project_id}),
                "UpdateExpression": expression,
                "ConditionExpression": condition,
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": self._serialize(values),
            }
        }

    def _progress(self, operation, meta, phase, cursor):
        return {
            "Update": {
                "TableName": resolve_table_name("Imports"),
                "Key": self._serialize(self._key(operation)),
                "UpdateExpression": "SET #phase = :next_phase, #cursor = :next_cursor",
                "ConditionExpression": "#phase = :phase AND #cursor = :cursor",
                "ExpressionAttributeNames": {"#phase": "phase", "#cursor": "cursor"},
                "ExpressionAttributeValues": self._serialize(
                    {
                        ":phase": meta["phase"],
                        ":cursor": meta["cursor"],
                        ":next_phase": phase,
                        ":next_cursor": cursor,
                    }
                ),
            }
        }

    def _publish_action(self, entry):
        item = entry["item"]
        # Compare every staged attribute before publication. Out-of-band edits
        # cannot be overwritten or deleted by retry/cleanup.
        names = {f"#f{i}": key for i, key in enumerate(item)}
        values = {f":v{i}": value for i, value in enumerate(item.values())}
        condition = " AND ".join(f"#f{i} = :v{i}" for i in range(len(item)))
        return {
            "Update": {
                "TableName": resolve_table_name(entry["table"]),
                "Key": self._serialize(
                    {"project_id": self.project_id, entry["key"]: item[entry["key"]]}
                ),
                "UpdateExpression": "REMOVE import_pending",
                "ConditionExpression": condition,
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": self._serialize(values),
            }
        }

    def resume(self, operation, _lease=None, should_continue=None):
        """Resume a stored plan; HTTP callers must first authorize project admin.

        Internal recovery workers may also call this using the persisted key.
        Lease fencing prevents an old invocation from writing after takeover.
        """
        meta = self._read(operation)
        if not meta:
            raise ValueError("Import journal not found")
        if meta["phase"] == "completed":
            return {"message": "successfully", "imported": int(meta["count"])}
        lease = _lease or str(uuid4())
        try:
            self._transact(
                [
                    self._project_action(operation, lease, acquire=True),
                    self._progress(operation, meta, meta["phase"], meta["cursor"]),
                ]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "TransactionCanceledException":
                raise ImportInProgressError(
                    "Import is busy; retry the same CSV after 90 seconds"
                ) from error
            raise
        try:
            while True:
                if should_continue is not None and not should_continue():
                    raise ImportPaused("Import recovery time budget exhausted")
                meta = self._read(operation)
                if meta["phase"] == "completed":
                    return {"message": "successfully", "imported": int(meta["count"])}
                index = int(meta["cursor"])
                if index == int(meta["chunks"]):
                    self._finish(operation, meta, lease)
                    return {"message": "successfully", "imported": int(meta["count"])}
                chunk = self._read(f"{operation}#{index}")
                if not chunk:
                    raise RuntimeError("Import journal chunk missing; operator recovery required")
                if meta["phase"] == "writing":
                    actions = [
                        {
                            "Put": {
                                "TableName": resolve_table_name(entry["table"]),
                                "Item": self._serialize(entry["item"]),
                                "ConditionExpression": "attribute_not_exists(project_id)",
                            }
                        }
                        for entry in chunk["entries"]
                    ]
                    next_phase = "publishing" if index + 1 == int(meta["chunks"]) else "writing"
                    next_cursor = 0 if next_phase == "publishing" else index + 1
                else:
                    actions = [self._publish_action(entry) for entry in chunk["entries"]]
                    next_phase, next_cursor = "publishing", index + 1
                self._transact(
                    [
                        self._project_action(operation, lease),
                        self._progress(operation, meta, next_phase, next_cursor),
                        *actions,
                    ]
                )
        except Exception:
            # A real process timeout leaves the bounded lease; ordinary errors
            # release it for immediate user/recovery retry, without deleting data.
            try:
                self.place_controller.dynamodb.Table(resolve_table_name("Projects")).update_item(
                    Key={"project_id": self.project_id},
                    UpdateExpression="SET import_lease_until = :expired",
                    ConditionExpression="import_operation = :operation AND import_lease = :lease",
                    ExpressionAttributeValues={
                        ":expired": 0,
                        ":operation": operation,
                        ":lease": lease,
                    },
                )
            except Exception:
                pass
            raise

    def _finish(self, operation, meta, lease):
        project = self._project_action(operation, lease)["Update"]
        project["UpdateExpression"] = (
            "REMOVE #status, import_operation, import_lease, import_lease_until"
        )
        del project["ExpressionAttributeValues"][":until"]
        terminal = self._progress(operation, meta, "completed", meta["cursor"])["Update"]
        terminal["UpdateExpression"] += ", #ttl = :ttl"
        terminal["ExpressionAttributeNames"]["#ttl"] = "ttl"
        terminal["ExpressionAttributeValues"][":ttl"] = {
            "N": str(int(time.time()) + self.RETENTION_SECONDS)
        }
        # Terminal metadata and all plan chunks expire together; active journals
        # deliberately have no TTL because they are needed to recover the lock.
        actions = [{"Update": project}, {"Update": terminal}]
        for index in range(int(meta["chunks"])):
            actions.append(
                {
                    "Update": {
                        "TableName": resolve_table_name("Imports"),
                        "Key": self._serialize(self._key(f"{operation}#{index}")),
                        "UpdateExpression": "SET #ttl = :ttl",
                        "ExpressionAttributeNames": {"#ttl": "ttl"},
                        "ExpressionAttributeValues": {
                            ":ttl": terminal["ExpressionAttributeValues"][":ttl"]
                        },
                    }
                }
            )
        self._transact(actions)

    def batch_create(self, places=None, targets=None):
        places = self.places if places is None else places
        targets = self.targets if targets is None else targets
        entries: list[ImportEntry] = [
            {"table": "Places", "key": "place_id", "item": dict(item)} for item in places
        ]
        entries += [
            {"table": "Targets", "key": "target_id", "item": dict(item)} for item in targets
        ]
        if not entries or len(entries) > 300:
            raise ValueError("Import must contain 1 to 300 records")
        operation = (
            self.import_digest or sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        )
        for entry in entries:
            item = entry["item"]
            if item.get("project_id") != self.project_id:
                raise ValueError("Import project mismatch")
            name = item.get("name")
            if not isinstance(name, str) or not 1 <= len(name) <= 255:
                raise ValueError("CSV names must contain 1 to 255 characters")
            item.update(import_operation=operation, import_pending=True)
        _, lease = self._stage(entries, operation)
        return self.resume(operation, _lease=lease)

    def make_place_obj(self, new_id, parent_place_id, hierarchy, name, photos_required_num=0):
        now = datetime.now(timezone.utc).isoformat()
        return {
            "project_id": self.project_id,
            "place_id": new_id,
            "parent_place_id": parent_place_id,
            "hierarchy": hierarchy,
            "name": str(name),
            "photos": {"required": photos_required_num, "results": {"before": 0, "after": 0}},
            "created_at": now,
            "updated_at": now,
        }

    def make_target_obj(self, new_id, parent_place_id, name):
        now = datetime.now(timezone.utc).isoformat()
        return {
            "project_id": self.project_id,
            "target_id": new_id,
            "parent_place_id": parent_place_id,
            "name": name,
            "photos": {"adopt": {"before": None, "after": None}, "before": [], "after": []},
            "created_at": now,
            "updated_at": now,
        }
