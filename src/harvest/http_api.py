"""HTTP boundary shared by every REST Lambda; domain services retain their contracts."""

import base64
import importlib
import json
import os
import time
from contextvars import ContextVar
from typing import Annotated, Any, Literal

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response
from aws_lambda_powertools.event_handler.exceptions import NotFoundError
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError
from decode_verify_jwt import AuthenticationError

from harvest.exception import ActionDeniedError, HarvestAbstractException
from harvest.utils.make_response_utils import DecimalEncoder
from harvest.utils.request_decorator import RequestDecorator

logger = Logger(service="harvest-api", log_uncaught_exceptions=False)
app = APIGatewayRestResolver(debug=False)
request_context: ContextVar[RequestDecorator] = ContextVar("request_context")
Name = Annotated[str, Field(min_length=1, max_length=256)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class NamedInput(Input):
    name: Name


class ProjectInput(NamedInput):
    start_on: str
    complete_on: str


class ImportInput(Input):
    csv: Annotated[str, Field(min_length=1, max_length=8_000_000)]


class MembershipInput(Input):
    action: Literal["join", "accept", "reject", "update"]
    role: Literal["guest", "reporter", "worker", "admin", "owner"] | None = None


class PhotoInput(Input):
    type: Literal["before", "after"]
    data: Annotated[str, Field(min_length=1, max_length=8_000_000)]


class AdoptInput(Input):
    type: Literal["before", "after"]


class UploadInput(Input):
    type: Literal["before", "after"]
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size: Annotated[StrictInt, Field(ge=1, le=4 * 1024 * 1024)]


class CompleteUploadInput(Input):
    version_id: Annotated[str, Field(min_length=1, max_length=1024)] | None = None


class NotificationInput(Input):
    read: StrictBool
    notification_ids: list[str] | None = None


class CorporationUserInput(Input):
    username: Name
    kana_username: Name
    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


class TemplateInput(Input):
    template_type: Literal["user", "project"]
    template_data: Annotated[str, Field(min_length=1, max_length=8_000_000)]
    name: Name
    description: str
    property_title: StrictBool
    property_hierarchy: StrictBool
    property_date: StrictBool


class GenerationInput(Input):
    type: Literal["zip", "excel"]
    title: Name | None = None
    template_id: str = "basic_1.xlsx"
    by_name: StrictBool = False
    needs_include_hierarchy: StrictBool = False
    needs_make_dir: StrictBool = False
    needs_all_photos: StrictBool = False
    needs_date: StrictBool = False
    needs_print_settings: StrictBool = False
    needs_sort: StrictBool = False
    force_download: StrictBool = False
    needs_download_url: StrictBool = True
    char_enc: Literal["utf_8", "utf-8", "shift_jis", "cp932"] = "utf_8"


def service(name: str, project_id: str | None = None):
    """Import only the feature being called; authentication precedes SDK creation."""
    modules = {"ProjectUser": "project_user", "Notification": "notification"}
    cls = getattr(importlib.import_module("harvest." + modules.get(name, name.lower())), name)
    instance = cls(os.getenv("DYNAMO_HOST"), os.getenv("DYNAMO_PORT"))
    actor = request_context.get().get_identity_id()
    if name == "Corporation":
        instance.set_corporation_id(actor)
    else:
        instance.set_user_id(actor)
    if project_id is not None:
        instance.set_project_id(project_id)
    return instance


def body(model: type[BaseModel]) -> dict:
    return model.model_validate(request_context.get().get_body()).model_dump(exclude_none=True)


def response(value, status: int = 200) -> Response:
    if isinstance(value, dict) and value.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
        value = {"message": "successfully"}
    return Response(status, "application/json", json.dumps(value, cls=DecimalEncoder))


def method() -> str:
    return request_context.get().get_method().upper()


@app.route("/projects", method=["GET", "POST"])
def projects():
    project = service("Project")
    return response(
        project.list_projects()
        if method() == "GET"
        else project.create_project(**body(ProjectInput))
    )


@app.route("/projects/<project_id>", method=["GET", "PUT", "DELETE"])
def project_item(project_id: str):
    project = service("Project", project_id)
    if method() == "GET":
        return response(project.show_project())
    if method() == "DELETE":
        return response(project.delete_project())
    return response(project.update_project(**body(ProjectInput)))


@app.post("/projects/<project_id>/import")
def import_project(project_id: str):
    return response(
        service("Project", project_id).import_csv(body(ImportInput)["csv"], base64enc=True)
    )


@app.route("/projects/<project_id>/users", method=["GET", "POST"])
@app.route("/projects/<project_id>/users/<user_id>", method=["GET", "PUT", "DELETE"])
def membership(project_id: str, user_id: str | None = None):
    users = service("ProjectUser", project_id)
    if method() == "GET":
        return response(users.show_user(user_id) if user_id else users.list_users())
    if method() == "DELETE":
        return response(users.delete_user(user_id))
    data = body(MembershipInput)
    action = data["action"]
    if method() == "POST" and action == "join":
        return response(users.join_user())
    if method() == "PUT" and user_id:
        if action == "accept":
            return response(users.accept_user(user_id))
        if action == "reject":
            return response(users.reject_user(user_id))
        if action == "update" and data.get("role"):
            return response(users.update_role(user_id, data["role"]))
    raise ValueError("Invalid membership action")


@app.route("/projects/<project_id>/places", method=["GET", "POST"])
@app.route("/projects/<project_id>/places/<place_id>", method=["GET", "POST", "PUT", "DELETE"])
def places(project_id: str, place_id: str | None = None):
    work = service("Work", project_id)
    if place_id:
        work.set_place_id(place_id)
    if method() == "GET":
        return response(work.show_place() if place_id else work.list_children(project_id))
    if method() == "DELETE":
        return response(work.delete_place(place_id))
    data = body(NamedInput)
    if method() == "POST":
        return response(work.create_place(data["name"], place_id))
    return response(work.update_place(data["name"]))


@app.get("/projects/<project_id>/places/<place_id>/children")
def place_children(project_id: str, place_id: str):
    return response(service("Work", project_id).list_children(place_id))


@app.post("/projects/<project_id>/targets")
@app.post("/projects/<project_id>/places/<place_id>/targets")
def create_target(project_id: str, place_id: str | None = None):
    return response(service("Work", project_id).create_target(body(NamedInput)["name"], place_id))


@app.route("/projects/<project_id>/targets/<target_id>", method=["GET", "PUT", "DELETE"])
def target_item(project_id: str, target_id: str):
    work = service("Work", project_id)
    work.set_target_id(target_id)
    if method() == "GET":
        return response(work.show_target())
    if method() == "DELETE":
        return response(work.delete_target(target_id))
    return response(work.update_target(body(NamedInput)["name"]))


@app.route("/projects/<project_id>/targets/<target_id>/photos", method=["GET", "POST"])
@app.route(
    "/projects/<project_id>/targets/<target_id>/photos/<photo_id>", method=["GET", "PUT", "DELETE"]
)
def photos(project_id: str, target_id: str, photo_id: str | None = None):
    work = service("Work", project_id)
    work.set_target_id(target_id)
    if photo_id:
        work.set_photo_id(photo_id)
    if method() == "GET":
        value = work.show_photo(photo_id, encode=True) if photo_id else work.list_photo(target_id)
        return response({"data": value} if isinstance(value, str) else value)
    if method() == "DELETE":
        return response(work.delete_photo(target_id, photo_id))
    if method() == "PUT":
        return response(work.update_photo(target_id, body(AdoptInput)["type"], photo_id))
    data = body(PhotoInput)
    return response(
        work.create_photo(target_id, data["type"], base64.b64decode(data["data"], validate=True))
    )


@app.post("/projects/<project_id>/targets/<target_id>/upload-sessions")
def create_upload(project_id: str, target_id: str):
    from harvest.uploads import UploadService

    config = body(UploadInput)
    uploads = UploadService(os.getenv("DYNAMO_HOST"), os.getenv("DYNAMO_PORT"))
    return response(
        uploads.create(request_context.get().get_identity_id(), project_id, target_id, **config),
        201,
    )


@app.post("/projects/<project_id>/targets/<target_id>/upload-sessions/<session_id>/complete")
def complete_upload(project_id: str, target_id: str, session_id: str):
    from harvest.uploads import UploadService

    config = body(CompleteUploadInput)
    uploads = UploadService(os.getenv("DYNAMO_HOST"), os.getenv("DYNAMO_PORT"))
    return response(
        uploads.complete(
            request_context.get().get_identity_id(), project_id, target_id, session_id, **config
        )
    )


def generate_report(project_id: str, data: dict):
    gen = service("Generate", project_id)
    common = {key: data[key] for key in ("title", "needs_include_hierarchy", "needs_date")}
    common["needs_download_url"] = data["needs_download_url"]
    if data["type"] == "zip":
        options = {
            key: data[key] for key in ("by_name", "needs_make_dir", "needs_all_photos", "char_enc")
        }
        return gen.gen_zip(project_id, **common, **options)
    options = {key: data[key] for key in ("template_id", "needs_print_settings", "needs_sort")}
    return gen.gen_excel_doc(project_id, **common, **options)


@app.route("/projects/<project_id>/generate", method=["GET", "POST"])
def generate(project_id: str):
    if method() == "GET":
        value = service("Generate", project_id).list()
    else:
        config = body(GenerationInput)
        config.setdefault("title", project_id)
        value = generate_report(project_id, config)
    return response({"download_url": value} if isinstance(value, str) else value)


@app.put("/projects/<project_id>/generate/<generated_file_id>/download")
def download(project_id: str, generated_file_id: str):
    return response(service("Generate", project_id).gen_download_url(generated_file_id))


@app.route("/projects/<project_id>/templates", method=["GET", "POST"])
@app.route("/projects/<project_id>/templates/<template_id>", method=["GET", "DELETE"])
def templates(project_id: str, template_id: str | None = None):
    tmpl = service("Template", project_id)
    if method() == "GET":
        return response(tmpl.get(template_id) if template_id else tmpl.list())
    if method() == "DELETE":
        return response(tmpl.delete(template_id))
    return response(tmpl.create(**body(TemplateInput)))


@app.route("/users/<user_id>/notifications", method=["GET", "PUT", "DELETE"])
@app.route("/users/<user_id>/notifications/<notification_id>", method=["GET", "PUT", "DELETE"])
def notifications(user_id: str, notification_id: str | None = None):
    if user_id != request_context.get().get_identity_id():
        raise ActionDeniedError()
    notification = service("Notification")
    if notification_id:
        notification.set_notification_id(notification_id)
    if method() == "GET":
        return response(
            notification.show_notification()
            if notification_id
            else notification.list_notifications()
        )
    if method() == "DELETE":
        return response(notification.delete_notification())
    return response(notification.update_notification(**body(NotificationInput)))


@app.get("/corporation")
@app.get("/corporation/billing")
def corporation():
    # Both legacy paths returned the complete corporation document.
    return response(service("Corporation").show())


@app.route("/corporation/users", method=["GET", "POST"])
@app.route("/corporation/users/<user_id>", method=["GET", "DELETE"])
def corporation_users(user_id: str | None = None):
    corp = service("Corporation")
    if method() == "GET":
        # The item-looking legacy GET is an alias for the corporation member list.
        return response(corp.list_users())
    if method() == "DELETE":
        return response(corp.delete_user(user_id))
    return response(
        corp.create_user(
            corporation_id=request_context.get().get_identity_id(), **body(CorporationUserInput)
        )
    )


@app.put("/projects/<project_id>/targets/<target_id>/photos")
@app.put("/projects/<project_id>/templates/<template_id>")
@app.put("/corporation/users/<user_id>")
def retired_operation(**kwargs):
    return response(
        {
            "error": {
                "title": "Method not allowed",
                "message": "This operation is not supported.",
                "code": 405,
            }
        },
        405,
    )


@app.post("/projects/<project_id>/generation-jobs")
def create_generation_job(project_id: str):
    from harvest.jobs import GenerationJobs, authorize

    actor = request_context.get().get_identity_id()
    authorize(actor, project_id)
    config = body(GenerationInput)
    config.setdefault("title", project_id)
    headers = {key.lower(): value for key, value in request_context.get().get_headers().items()}
    item = GenerationJobs().create(actor, project_id, config, headers.get("idempotency-key"))
    return response({"job_id": item["job_id"], "status": item["status"]}, 202)


@app.get("/projects/<project_id>/generation-jobs/<job_id>")
def get_generation_job(project_id: str, job_id: str):
    from harvest.jobs import GenerationJobs, authorize

    actor = request_context.get().get_identity_id()
    authorize(actor, project_id)
    item = GenerationJobs().public(actor, project_id, job_id)
    return response(item) if item else response({"message": "Not found"}, 404)


def error_response(status: int, title: str, code: int = 999, message: str | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {"error": {"title": title, "message": message or title, "code": code, "ref_url": ""}}
        ),
    }


def exception_response(exc: Exception) -> dict:
    if isinstance(exc, NotFoundError):
        return error_response(404, "Not found", 404)
    if isinstance(exc, AuthenticationError):
        return error_response(401, exc.title, exc.code, exc.message)
    if isinstance(exc, ActionDeniedError):
        return error_response(403, exc.title, exc.code, exc.message)
    if isinstance(exc, HarvestAbstractException):
        return error_response(
            getattr(exc, "status_code", 400),
            getattr(exc, "title", "Invalid request"),
            getattr(exc, "code", 999),
            getattr(exc, "message", None),
        )
    if isinstance(exc, (ValidationError, ValueError, KeyError, TypeError)):
        status = getattr(exc, "status_code", 400)
        titles = {
            400: "Invalid request",
            404: "Not found",
            409: "Conflict",
            410: "Expired",
            429: "Too many requests",
        }
        status = status if status in titles else 400
        return error_response(status, titles[status])
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        conflict = code == "ConditionalCheckFailedException" or (
            code == "TransactionCanceledException"
            and any(
                reason.get("Code") == "ConditionalCheckFailed"
                for reason in exc.response.get("CancellationReasons", [])
            )
        )
        return error_response(
            409 if conflict else 503, "Conflict" if conflict else "Service unavailable"
        )
    return error_response(500, "Internal server error")


@app.exception_handler(Exception)
def route_exception(exc: Exception) -> Response:
    # Catch inside the resolver: its default unhandled-exception logger includes
    # exception messages, which may contain provider payloads or credentials.
    result = exception_response(exc)
    return Response(result["statusCode"], "application/json", result["body"])


def lambda_handler(event: dict, context) -> dict:
    """Only allowlisted metadata is logged. No event, body, token or exception text."""
    started = time.monotonic()
    token = None
    result: dict[str, Any]
    request_id = getattr(context, "aws_request_id", "local")
    try:
        if event.get("httpMethod", "").upper() == "OPTIONS":
            result = {"statusCode": 204, "headers": {}, "body": ""}
        else:
            token = request_context.set(RequestDecorator(event))
            result = app.resolve(event, context)
    except Exception as exc:
        result = exception_response(exc)
    finally:
        if token is not None:
            request_context.reset(token)
    headers = result.setdefault("headers", {})
    headers.update(
        {
            "X-Request-Id": request_id,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
    )
    incoming = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    allowed = {
        origin.strip() for origin in os.getenv("CORS_ORIGIN", "http://localhost:4200").split(",")
    }
    origin = incoming.get("origin")
    if origin in allowed:
        headers.update(
            {
                "Access-Control-Allow-Origin": origin,
                "Vary": "Origin",
                "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
                "Access-Control-Allow-Headers": "Content-Type,Authorization,Idempotency-Key",
                "Access-Control-Expose-Headers": "X-Request-Id",
            }
        )
    logger.info(
        "request_completed",
        request_id=request_id,
        status=result["statusCode"],
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return result
