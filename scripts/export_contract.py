#!/usr/bin/env python3
"""Export the HTTP route inventory and Pydantic request schemas without AWS access.

Run with --check in CI to detect contract drift. Route decorators are the source
of truth. Response schemas intentionally stay open for retained legacy services.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest import http_api as api


BODY_MODELS = {
    ("projects", "post"): "ProjectInput",
    ("project_item", "put"): "ProjectInput",
    ("import_project", "post"): "ImportInput",
    ("membership", "post"): "MembershipInput",
    ("membership", "put"): "MembershipInput",
    ("places", "post"): "NamedInput",
    ("places", "put"): "NamedInput",
    ("create_target", "post"): "NamedInput",
    ("target_item", "put"): "NamedInput",
    ("photos", "post"): "PhotoInput",
    ("photos", "put"): "AdoptInput",
    ("generate", "post"): "GenerationInput",
    ("templates", "post"): "TemplateInput",
    ("notifications", "put"): "NotificationInput",
    ("corporation_users", "post"): "CorporationUserInput",
    ("create_generation_job", "post"): "GenerationInput",
    ("create_upload", "post"): "UploadInput",
    ("complete_upload", "post"): "CompleteUploadInput",
}
RETIRED = {
    "/projects/{project_id}/targets/{target_id}/photos": "Collection PUT referenced an undefined photo_id; use PUT on the photo item.",
    "/projects/{project_id}/templates/{template_id}": "Legacy PUT was a no-op. Create a replacement template and remove the old one.",
    "/corporation/users/{user_id}": "Legacy PUT was a no-op. Employee editing is not implemented.",
}


def route_inventory():
    source = Path(api.__file__).read_text()
    routes = []
    for function in ast.parse(source).body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "app":
                continue
            verb = decorator.func.attr
            if verb not in {"get", "post", "put", "patch", "delete", "route"}:
                continue
            path = re.sub(r"<([^>]+)>", r"{\1}", ast.literal_eval(decorator.args[0]))
            methods = [verb]
            if verb == "route":
                methods = next(
                    ast.literal_eval(item.value)
                    for item in decorator.keywords
                    if item.arg == "method"
                )
            routes.extend((path, method.lower(), function.name) for method in methods)
    return sorted(routes)


def response(description, schema=None):
    result = {"description": description}
    if schema is not None:
        result["content"] = {"application/json": {"schema": schema}}
    return result


def build_contract():
    error = {"$ref": "#/components/schemas/Error"}
    schemas = {
        name: getattr(api, name).model_json_schema(ref_template="#/components/schemas/{model}")
        for name in sorted(set(BODY_MODELS.values()))
    }
    schemas["Error"] = {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "required": ["title", "message", "code"],
                "properties": {
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                    "code": {"type": "integer"},
                    "ref_url": {"type": "string"},
                },
            }
        },
    }
    schemas["GenerationJobAccepted"] = {
        "type": "object",
        "required": ["job_id", "status"],
        "properties": {
            "job_id": {"type": "string"},
            "status": {"enum": ["queued", "running", "succeeded", "failed"]},
        },
    }
    schemas["GenerationJob"] = {
        "type": "object",
        "required": ["job_id", "status", "created_at", "updated_at"],
        "properties": {
            "job_id": {"type": "string"},
            "status": {"enum": ["queued", "running", "succeeded", "failed"]},
            "created_at": {"type": "number"},
            "updated_at": {"type": "number"},
            "error_code": {"type": "string"},
            "result": {
                "type": "object",
                "required": ["name", "download_url"],
                "properties": {
                    "name": {"type": "string"},
                    "download_url": {"type": "string", "format": "uri"},
                },
            },
        },
    }
    schemas["UploadSession"] = {
        "type": "object",
        "required": ["session_id", "photo_id", "expires_at", "upload"],
        "properties": {
            "session_id": {"type": "string"},
            "photo_id": {"type": "string"},
            "expires_at": {"type": "integer"},
            "upload": {
                "type": "object",
                "required": ["url", "fields"],
                "properties": {
                    "url": {"type": "string", "format": "uri"},
                    "fields": {"type": "object", "additionalProperties": {"type": "string"}},
                },
            },
        },
    }
    schemas["UploadComplete"] = {
        "type": "object",
        "required": ["message", "photo_id"],
        "properties": {"message": {"const": "successfully"}, "photo_id": {"type": "string"}},
    }
    paths = {}
    for path, method, function in route_inventory():
        operation = {
            "operationId": method + "_" + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_"),
            "summary": function.replace("_", " "),
            "responses": {
                "200": response(
                    "Legacy JSON result; service-specific response shape is retained.", {}
                ),
                "400": response("Invalid input", error),
                "401": response("Invalid or missing ID token", error),
                "403": response("Denied by resource or role authorization", error),
                "409": response("Conditional write or idempotency conflict", error),
                "500": response("Unexpected failure, sanitized", error),
                "503": response("Downstream AWS failure, sanitized", error),
            },
            "parameters": [
                {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                for name in re.findall(r"{([^}]+)}", path)
            ],
        }
        model = BODY_MODELS.get((function, method))
        if model:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + model}}
                },
            }
        if function == "retired_operation":
            operation["deprecated"] = True
            operation["description"] = RETIRED[path]
            operation["responses"] = {
                "401": response("Invalid or missing token", error),
                "405": response("Explicitly unsupported legacy operation", error),
            }
        if function == "create_upload":
            operation["responses"]["429"] = response(
                "Actor or target upload session rate limit exceeded",
                error,
            )
            operation["responses"].pop("200")
            operation["responses"]["201"] = response(
                "Quarantine upload session; send the form fields and image to the provided S3 URL, then complete the session.",
                {"$ref": "#/components/schemas/UploadSession"},
            )
        if function == "complete_upload":
            operation["responses"]["200"] = response(
                "Version-pinned image validated and attached to its target.",
                {"$ref": "#/components/schemas/UploadComplete"},
            )
            operation["responses"]["404"] = response(
                "Upload session not found or not owned by the caller", error
            )
            operation["responses"]["410"] = response("Upload session expired", error)
        if function == "create_generation_job":
            operation["parameters"].append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    "description": "Reuse the same key with identical input to retry submission safely.",
                }
            )
            operation["responses"].pop("200")
            operation["responses"]["202"] = response(
                "Durably queued generation job",
                {"$ref": "#/components/schemas/GenerationJobAccepted"},
            )
        if function == "get_generation_job":
            operation["responses"]["200"] = response(
                "Caller-owned job status; result URL expires after five minutes.",
                {"$ref": "#/components/schemas/GenerationJob"},
            )
            operation["responses"]["404"] = response(
                "Job is absent, expired, or belongs to another actor/project",
                {"type": "object", "properties": {"message": {"type": "string"}}},
            )
        if function == "corporation_users" and method == "get" and "{user_id}" in path:
            operation["responses"]["404"] = response(
                "Employee not found in this corporation",
                {"type": "object", "properties": {"message": {"type": "string"}}},
            )
        paths.setdefault(path, {})[method] = operation
    for path, operations in paths.items():
        operations["options"] = {
            "operationId": "options_" + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_"),
            "summary": "CORS preflight",
            "security": [],
            "parameters": [
                {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                for name in re.findall(r"{([^}]+)}", path)
            ],
            "responses": {
                "204": response("No body; CORS headers are emitted only for an allowed Origin.")
            },
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Harvest API",
            "version": "0.1.0",
            "description": "Generated from HTTP handlers and Pydantic models. Cognito ID tokens are accepted as Bearer tokens; raw ID tokens remain accepted for legacy clients. Original 50 methods comprise 47 implemented operations and three explicit retired 405 operations. Two generation-job operations and two upload-session operations are additive.",
        },
        "security": [{"CognitoIdToken": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "CognitoIdToken": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Cognito ID JWT",
                }
            },
            "schemas": schemas,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "contracts/openapi.json"
    text = json.dumps(build_contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not output.exists() or output.read_text() != text:
            raise SystemExit("OpenAPI is stale; run python scripts/export_contract.py")
        print("OpenAPI contract is up to date")
    else:
        output.parent.mkdir(exist_ok=True)
        output.write_text(text)
        print(f"Wrote {output}")


if __name__ == "__main__":
    main()
