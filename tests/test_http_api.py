"""HTTP contract tests: no network, SDK calls, credentials, or real user data."""

import base64
import importlib
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest
from botocore.exceptions import ClientError

from harvest import http_api as api
from harvest.exception import ActionDeniedError
from decode_verify_jwt import AuthenticationError

P = "/projects/project-a"
U = "/users/user-a/notifications"
PROJECT = {"name": "現場", "start_on": "2026-01-01", "complete_on": "2026-12-31"}
TEMPLATE = {
    "template_type": "project",
    "template_data": "eA==",
    "name": "帳票",
    "description": "",
    "property_title": True,
    "property_hierarchy": False,
    "property_date": False,
}
# One fixture per supported operation, including every formerly implemented route.
CASES = [
    ("GET", "/projects", None, "Project", "list_projects", (), {}),
    ("POST", "/projects", PROJECT, "Project", "create_project", (), PROJECT),
    ("GET", P, None, "Project", "show_project", (), {}),
    ("PUT", P, PROJECT, "Project", "update_project", (), PROJECT),
    ("DELETE", P, None, "Project", "delete_project", (), {}),
    (
        "POST",
        P + "/import",
        {"csv": "eA=="},
        "Project",
        "import_csv",
        ("eA==",),
        {"base64enc": True},
    ),
    ("GET", P + "/users", None, "ProjectUser", "list_users", (), {}),
    ("POST", P + "/users", {"action": "join"}, "ProjectUser", "join_user", (), {}),
    ("GET", P + "/users/member", None, "ProjectUser", "show_user", ("member",), {}),
    (
        "PUT",
        P + "/users/member",
        {"action": "accept"},
        "ProjectUser",
        "accept_user",
        ("member",),
        {},
    ),
    ("DELETE", P + "/users/member", None, "ProjectUser", "delete_user", ("member",), {}),
    ("GET", P + "/places", None, "Work", "list_children", ("project-a",), {}),
    ("POST", P + "/places", {"name": "棟"}, "Work", "create_place", ("棟", None), {}),
    ("GET", P + "/places/place-a", None, "Work", "show_place", (), {}),
    ("POST", P + "/places/place-a", {"name": "階"}, "Work", "create_place", ("階", "place-a"), {}),
    ("PUT", P + "/places/place-a", {"name": "階"}, "Work", "update_place", ("階",), {}),
    ("DELETE", P + "/places/place-a", None, "Work", "delete_place", ("place-a",), {}),
    ("GET", P + "/places/place-a/children", None, "Work", "list_children", ("place-a",), {}),
    ("POST", P + "/targets", {"name": "施工箇所"}, "Work", "create_target", ("施工箇所", None), {}),
    (
        "POST",
        P + "/places/place-a/targets",
        {"name": "施工箇所"},
        "Work",
        "create_target",
        ("施工箇所", "place-a"),
        {},
    ),
    ("GET", P + "/targets/target-a", None, "Work", "show_target", (), {}),
    ("PUT", P + "/targets/target-a", {"name": "改名"}, "Work", "update_target", ("改名",), {}),
    ("DELETE", P + "/targets/target-a", None, "Work", "delete_target", ("target-a",), {}),
    ("GET", P + "/targets/target-a/photos", None, "Work", "list_photo", ("target-a",), {}),
    (
        "POST",
        P + "/targets/target-a/photos",
        {"type": "before", "data": "eA=="},
        "Work",
        "create_photo",
        ("target-a", "before", b"x"),
        {},
    ),
    (
        "GET",
        P + "/targets/target-a/photos/photo-a",
        None,
        "Work",
        "show_photo",
        ("photo-a",),
        {"encode": True},
    ),
    (
        "PUT",
        P + "/targets/target-a/photos/photo-a",
        {"type": "after"},
        "Work",
        "update_photo",
        ("target-a", "after", "photo-a"),
        {},
    ),
    (
        "DELETE",
        P + "/targets/target-a/photos/photo-a",
        None,
        "Work",
        "delete_photo",
        ("target-a", "photo-a"),
        {},
    ),
    ("GET", P + "/generate", None, "Generate", "list", (), {}),
    (
        "POST",
        P + "/generate",
        {"type": "excel", "title": "帳票"},
        "Generate",
        "gen_excel_doc",
        ("project-a",),
        {
            "title": "帳票",
            "needs_include_hierarchy": False,
            "needs_date": False,
            "template_id": "basic_1.xlsx",
            "needs_print_settings": False,
            "needs_download_url": True,
            "needs_sort": False,
        },
    ),
    ("PUT", P + "/generate/file-a/download", None, "Generate", "gen_download_url", ("file-a",), {}),
    ("GET", P + "/templates", None, "Template", "list", (), {}),
    ("POST", P + "/templates", TEMPLATE, "Template", "create", (), TEMPLATE),
    ("GET", P + "/templates/template-a", None, "Template", "get", ("template-a",), {}),
    ("DELETE", P + "/templates/template-a", None, "Template", "delete", ("template-a",), {}),
    ("GET", U, None, "Notification", "list_notifications", (), {}),
    (
        "PUT",
        U,
        {"read": True, "notification_ids": ["notice-a"]},
        "Notification",
        "update_notification",
        (),
        {"read": True, "notification_ids": ["notice-a"]},
    ),
    ("DELETE", U, None, "Notification", "delete_notification", (), {}),
    ("GET", U + "/notice-a", None, "Notification", "show_notification", (), {}),
    (
        "PUT",
        U + "/notice-a",
        {"read": False},
        "Notification",
        "update_notification",
        (),
        {"read": False},
    ),
    ("DELETE", U + "/notice-a", None, "Notification", "delete_notification", (), {}),
    ("GET", "/corporation", None, "Corporation", "show", (), {}),
    ("GET", "/corporation/billing", None, "Corporation", "show", (), {}),
    ("GET", "/corporation/users", None, "Corporation", "list_users", (), {}),
    (
        "POST",
        "/corporation/users",
        {
            "username": "社員",
            "kana_username": "シャイン",
            "email": "user@example.invalid",
            "password": "test-only",
        },
        "Corporation",
        "create_user",
        (),
        {
            "corporation_id": "user-a",
            "username": "社員",
            "kana_username": "シャイン",
            "email": "user@example.invalid",
            "password": "test-only",
        },
    ),
    ("GET", "/corporation/users/employee-a", None, "Corporation", "list_users", (), {}),
    (
        "DELETE",
        "/corporation/users/employee-a",
        None,
        "Corporation",
        "delete_user",
        ("employee-a",),
        {},
    ),
]


def event(method, path, data=None, token="test-jwt", origin="https://app.example.invalid"):
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "headers": {**({"Authorization": "Bearer " + token} if token else {}), "Origin": origin},
        "requestContext": {
            "httpMethod": method,
            "path": path,
            "stage": "test",
            "requestId": "gateway-id",
        },
        "queryStringParameters": None,
        "pathParameters": {},
        "isBase64Encoded": False,
        "body": json.dumps(data) if data is not None else None,
    }


def invoke(method, path, data=None, **kwargs):
    return api.lambda_handler(
        event(method, path, data, **kwargs), SimpleNamespace(aws_request_id="request-test")
    )


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    monkeypatch.setenv("CORS_ORIGIN", "https://app.example.invalid")

    def verify(token):
        if token != "Bearer test-jwt":
            raise AuthenticationError()
        return {"cognito:username": "user-a", "sub": "subject-a"}

    monkeypatch.setattr("harvest.utils.request_decorator.decode_verify_jwt", verify)


@pytest.fixture
def services(monkeypatch):
    names = {
        "Project": "project",
        "ProjectUser": "project_user",
        "Work": "work",
        "Generate": "generate",
        "Template": "template",
        "Notification": "notification",
        "Corporation": "corporation",
    }
    mocks = {
        name: create_autospec(
            getattr(importlib.import_module("harvest." + module), name), instance=True
        )
        for name, module in names.items()
    }
    factory = Mock(side_effect=lambda name, project_id=None: mocks[name])
    monkeypatch.setattr(api, "service", factory)
    return mocks, factory


@pytest.mark.parametrize(
    "method,path,data,name,action,args,kwargs", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES]
)
def test_supported_route_dispatch_and_response(
    services, method, path, data, name, action, args, kwargs
):
    mocks, factory = services
    expected = {"ok": True}
    if path == "/corporation/billing":
        mocks[name].show.return_value = {"billing": {"total": Decimal("12.50")}}
        expected = {"total": 12.5}
    elif method == "GET" and path == "/corporation/users/employee-a":
        mocks[name].list_users.return_value = [{"user_id": "employee-a"}]
        expected = {"user_id": "employee-a"}
    else:
        getattr(mocks[name], action).return_value = expected
    result = invoke(method, path, data)
    assert result["statusCode"] == 200, result
    assert json.loads(result["body"]) == expected
    getattr(mocks[name], action).assert_called_once_with(*args, **kwargs)
    assert result["headers"]["Cache-Control"] == "no-store"
    assert result["headers"]["X-Request-Id"] == "request-test"
    assert result["headers"]["Access-Control-Allow-Origin"] == "https://app.example.invalid"
    with pytest.raises(LookupError):
        api.request_context.get()


@pytest.mark.parametrize("case", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES])
def test_every_route_authenticates_before_service_creation(services, case):
    _, factory = services
    result = invoke(case[0], case[1], case[2], token=None)
    assert result["statusCode"] == 401
    factory.assert_not_called()


@pytest.mark.parametrize("case", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES])
def test_domain_denials_are_403_for_every_route(services, case):
    mocks, _ = services
    getattr(mocks[case[3]], case[4]).side_effect = ActionDeniedError()
    assert invoke(case[0], case[1], case[2])["statusCode"] == 403


@pytest.mark.parametrize("data", [None, {}, [], {"name": ""}, {**PROJECT, "unexpected": True}])
def test_invalid_project_payload_does_not_mutate(services, data):
    mocks, _ = services
    assert invoke("POST", "/projects", data)["statusCode"] == 400
    mocks["Project"].create_project.assert_not_called()


@pytest.mark.parametrize(
    "method,path,data",
    [
        ("POST", P + "/targets/target-a/photos", {"type": "before", "data": "%%%"}),
        ("POST", P + "/targets/target-a/photos", {"type": "other", "data": "eA=="}),
        ("PUT", U, {"read": "true"}),
        ("POST", P + "/generate", {"type": "pdf", "title": "x"}),
        ("POST", P + "/users", {"action": "update", "role": "owner"}),
        ("PUT", P + "/users/member", {"action": "update"}),
    ],
)
def test_validation_rejects_invalid_operations(services, method, path, data):
    assert invoke(method, path, data)["statusCode"] == 400


def test_malformed_json_is_400(services):
    request = event("POST", "/projects")
    request["body"] = "{broken"
    assert api.lambda_handler(request, None)["statusCode"] == 400


def test_base64_gateway_body_is_decoded(services):
    mocks, _ = services
    mocks["Project"].create_project.return_value = {"id": "project-a"}
    request = event("POST", "/projects")
    request["body"] = base64.b64encode(json.dumps(PROJECT).encode()).decode()
    request["isBase64Encoded"] = True
    assert api.lambda_handler(request, None)["statusCode"] == 200
    mocks["Project"].create_project.assert_called_once_with(**PROJECT)


@pytest.mark.parametrize("path", [U, U + "/notice-a"])
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_other_users_notifications_forbidden_before_service(services, path, method):
    _, factory = services
    assert invoke(method, path.replace("user-a", "user-b"), {"read": True})["statusCode"] == 403
    factory.assert_not_called()


@pytest.mark.parametrize(
    "code,status",
    [
        ("ThrottlingException", 503),
        ("AccessDeniedException", 503),
        ("ConditionalCheckFailedException", 409),
    ],
)
def test_sdk_errors_have_safe_status_and_no_internal_details(services, code, status, caplog):
    mocks, _ = services
    mocks["Project"].list_projects.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "private-database-secret"}}, "Query"
    )
    result = invoke("GET", "/projects")
    assert result["statusCode"] == status
    assert "private-database-secret" not in result["body"] + caplog.text


def test_unexpected_error_is_500_and_no_secret(services, caplog):
    mocks, _ = services
    mocks["Project"].list_projects.side_effect = RuntimeError("private-token-secret")
    result = invoke("GET", "/projects")
    assert result["statusCode"] == 500
    assert "private-token-secret" not in result["body"] + caplog.text


@pytest.mark.parametrize(
    "reason,status",
    [
        ("ConditionalCheckFailed", 409),
        ("ProvisionedThroughputExceeded", 503),
        ("TransactionConflict", 503),
        ("ValidationError", 503),
    ],
)
def test_transaction_conflict_response_distinguishes_conditions_from_service_failures(
    services, reason, status, caplog
):
    mocks, _ = services
    mocks["Project"].list_projects.side_effect = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "private-row"},
            "CancellationReasons": [{"Code": "None"}, {"Code": reason, "Message": "private-row"}],
        },
        "TransactWriteItems",
    )
    result = invoke("GET", "/projects")
    assert result["statusCode"] == status
    assert "private-row" not in result["body"] + caplog.text


@pytest.mark.parametrize("path", sorted({c[1] for c in CASES}))
def test_preflight_needs_no_jwt_or_service(services, path):
    _, factory = services
    response = invoke("OPTIONS", path, token=None)
    assert response["statusCode"] == 204
    assert "Authorization" in response["headers"]["Access-Control-Allow-Headers"]
    factory.assert_not_called()


def test_unlisted_origin_gets_no_cors_header(services):
    result = invoke("OPTIONS", "/projects", token=None, origin="https://attacker.invalid")
    assert "Access-Control-Allow-Origin" not in result["headers"]


def test_photo_and_report_keep_legacy_wrapper(services):
    mocks, _ = services
    mocks["Work"].show_photo.return_value = "eA=="
    assert json.loads(invoke("GET", P + "/targets/target-a/photos/photo-a")["body"]) == {
        "data": "eA=="
    }
    mocks["Generate"].gen_excel_doc.return_value = "https://download.example.invalid"
    assert json.loads(
        invoke("POST", P + "/generate", {"type": "excel", "title": "帳票"})["body"]
    ) == {"download_url": "https://download.example.invalid"}


def test_corporation_unknown_user_is_404(services):
    mocks, _ = services
    mocks["Corporation"].list_users.return_value = []
    assert invoke("GET", "/corporation/users/foreign")["statusCode"] == 404


def test_unknown_route_is_404(services):
    _, factory = services
    assert invoke("GET", "/unknown")["statusCode"] == 404
    factory.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [P + "/targets/target-a/photos", P + "/templates/template-a", "/corporation/users/employee-a"],
)
def test_unimplemented_legacy_put_is_explicit_405(services, path):
    _, factory = services
    assert invoke("PUT", path, {})["statusCode"] == 405
    factory.assert_not_called()


@pytest.mark.parametrize(
    "action,data,expected_args",
    [
        ("reject", {"action": "reject"}, ("member",)),
        ("update_role", {"action": "update", "role": "worker"}, ("member", "worker")),
    ],
)
def test_membership_additional_actions(services, action, data, expected_args):
    mocks, _ = services
    method = "reject_user" if action == "reject" else action
    getattr(mocks["ProjectUser"], method).return_value = {"ok": True}
    assert invoke("PUT", P + "/users/member", data)["statusCode"] == 200
    getattr(mocks["ProjectUser"], method).assert_called_once_with(*expected_args)


def test_zip_generation_options_are_forwarded(services):
    mocks, _ = services
    mocks["Generate"].gen_zip.return_value = "https://download.example.invalid"
    data = {
        "type": "zip",
        "title": "写真",
        "char_enc": "cp932",
        "needs_all_photos": True,
        "needs_make_dir": False,
        "by_name": False,
        "needs_date": True,
        "needs_include_hierarchy": True,
    }
    assert invoke("POST", P + "/generate", data)["statusCode"] == 200
    mocks["Generate"].gen_zip.assert_called_once_with(
        "project-a",
        title="写真",
        char_enc="cp932",
        needs_all_photos=True,
        needs_make_dir=False,
        by_name=False,
        needs_date=True,
        needs_include_hierarchy=True,
        needs_download_url=True,
    )


@pytest.fixture
def jobs(monkeypatch):
    from harvest import jobs as module

    service = create_autospec(module.GenerationJobs, instance=True)
    factory = Mock(return_value=service)
    authorize = Mock()
    monkeypatch.setattr(module, "GenerationJobs", factory)
    monkeypatch.setattr(module, "authorize", authorize)
    return service, factory, authorize


def test_generation_job_accepts_idempotency_header(jobs):
    service, _, authorize = jobs
    service.create.return_value = {"job_id": "job-a", "status": "queued"}
    request = event("POST", P + "/generation-jobs", {"type": "excel", "title": "帳票"})
    request["headers"]["Idempotency-Key"] = "retry-key"
    result = api.lambda_handler(request, None)
    assert result["statusCode"] == 202
    assert json.loads(result["body"]) == {"job_id": "job-a", "status": "queued"}
    authorize.assert_called_once_with("user-a", "project-a")
    args = service.create.call_args.args
    assert args[:2] == ("user-a", "project-a")
    assert args[2] == api.GenerationInput(type="excel", title="帳票").model_dump()
    assert args[3] == "retry-key"


@pytest.mark.parametrize("state", ["queued", "running", "failed", "succeeded"])
def test_generation_job_status_response(jobs, state):
    service, _, authorize = jobs
    service.public.return_value = {
        "job_id": "job-a",
        "status": state,
        "created_at": 1,
        "updated_at": 2,
    }
    result = invoke("GET", P + "/generation-jobs/job-a")
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["status"] == state
    service.public.assert_called_once_with("user-a", "project-a", "job-a")


def test_generation_job_not_visible_is_404(jobs):
    service, _, _ = jobs
    service.public.return_value = None
    assert invoke("GET", P + "/generation-jobs/job-a")["statusCode"] == 404


@pytest.mark.parametrize(
    "method,path,data",
    [
        ("POST", P + "/generation-jobs", {"type": "excel", "title": "帳票"}),
        ("GET", P + "/generation-jobs/job-a", None),
    ],
)
def test_generation_job_denial_precedes_sdk_creation(jobs, method, path, data):
    _, factory, authorize = jobs
    authorize.side_effect = ActionDeniedError()
    assert invoke(method, path, data)["statusCode"] == 403
    factory.assert_not_called()
    authorize.reset_mock()
    assert invoke(method, path, data, token=None)["statusCode"] == 401
    authorize.assert_not_called()


def test_generation_job_idempotency_conflict_is_409(jobs):
    from harvest.jobs import JobConflict

    service, _, _ = jobs
    service.create.side_effect = JobConflict("private-config-digest")
    result = invoke("POST", P + "/generation-jobs", {"type": "excel", "title": "帳票"})
    assert result["statusCode"] == 409
    assert "private-config-digest" not in result["body"]


@pytest.mark.parametrize(
    "name,module,scope", [("Project", "project", "project-a"), ("Corporation", "corporation", None)]
)
def test_service_factory_binds_verified_actor_and_scope(monkeypatch, name, module, scope):
    instance = Mock()
    constructor = Mock(return_value=instance)
    monkeypatch.setattr(
        api.importlib,
        "import_module",
        lambda requested: (
            SimpleNamespace(**{name: constructor})
            if requested == "harvest." + module
            else pytest.fail(requested)
        ),
    )
    request = api.RequestDecorator(event("GET", "/projects"))
    token = api.request_context.set(request)
    try:
        assert api.service(name, scope) is instance
    finally:
        api.request_context.reset(token)
    (
        instance.set_corporation_id if name == "Corporation" else instance.set_user_id
    ).assert_called_once_with("user-a")
    if scope:
        instance.set_project_id.assert_called_once_with(scope)


def test_malformed_token_is_401_without_calling_services(services):
    _, factory = services
    assert invoke("GET", "/projects", token="invalid-test-token")["statusCode"] == 401
    factory.assert_not_called()


def test_openapi_matches_code_and_covers_all_original_methods():
    from pathlib import Path
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "export_contract", root / "scripts/export_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    contract = json.loads((root / "contracts/openapi.json").read_text())
    assert contract == module.build_contract()
    upload_responses = contract["paths"][
        "/projects/{project_id}/targets/{target_id}/upload-sessions"
    ]["post"]["responses"]
    assert upload_responses["429"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Error"
    }
    operations = {
        (path, method)
        for path, methods in contract["paths"].items()
        for method in methods
        if method != "options"
    }
    retired = {(path, "put") for path in module.RETIRED}
    assert len(retired) == 3
    new = {
        (P + "/generation-jobs", "post"),
        (P + "/generation-jobs/job-a", "get"),
        (P + "/targets/target-a/upload-sessions", "post"),
        (P + "/targets/target-a/upload-sessions/session-a/complete", "post"),
    }
    substitutions = {
        "project-a": "project_id",
        "place-a": "place_id",
        "target-a": "target_id",
        "photo-a": "photo_id",
        "user-a": "user_id",
        "member": "user_id",
        "employee-a": "user_id",
        "notice-a": "notification_id",
        "file-a": "generated_file_id",
        "template-a": "template_id",
        "job-a": "job_id",
        "session-a": "session_id",
    }

    def normalize(path):
        return "/".join(
            "{" + substitutions[part] + "}" if part in substitutions else part
            for part in path.split("/")
        )

    tested = {(normalize(case[1]), case[0].lower()) for case in CASES}
    assert len(tested) == 47
    assert tested | retired | {(normalize(path), method) for path, method in new} == operations


def test_legacy_sdk_success_response_remains_normalized(services):
    mocks, _ = services
    mocks["Project"].delete_project.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "provider-only"}
    }
    result = invoke("DELETE", P)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"message": "successfully"}


@pytest.mark.parametrize(
    "kind,action,expected",
    [
        (
            "zip",
            "gen_zip",
            {
                "by_name": False,
                "needs_make_dir": False,
                "needs_all_photos": False,
                "char_enc": "utf_8",
            },
        ),
        (
            "excel",
            "gen_excel_doc",
            {"template_id": "basic_1.xlsx", "needs_print_settings": False, "needs_sort": False},
        ),
    ],
)
def test_legacy_generation_defaults(services, kind, action, expected):
    mocks, _ = services
    getattr(mocks["Generate"], action).return_value = {"ok": True}
    result = invoke("POST", P + "/generate", {"type": kind})
    assert result["statusCode"] == 200
    getattr(mocks["Generate"], action).assert_called_once_with(
        "project-a",
        title="project-a",
        needs_include_hierarchy=False,
        needs_date=False,
        needs_download_url=True,
        **expected,
    )


def test_signed_test_jwt_passes_through_real_verifier(monkeypatch, services):
    import time
    import jwt
    import decode_verify_jwt as verifier
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("COGNITO_REGION", "ap-northeast-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "pool-test")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client-test")
    jwks = Mock()
    jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=private.public_key())
    monkeypatch.setattr(verifier, "_jwks_client", lambda issuer: jwks)
    monkeypatch.setattr(
        "harvest.utils.request_decorator.decode_verify_jwt", verifier.decode_verify_jwt
    )
    token = jwt.encode(
        {
            "iss": "https://cognito-idp.ap-northeast-1.amazonaws.com/pool-test",
            "aud": "client-test",
            "sub": "subject-a",
            "cognito:username": "user-a",
            "token_use": "id",
            "iat": int(time.time()) - 5,
            "exp": int(time.time()) + 300,
        },
        private,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    mocks, _ = services
    mocks["Project"].list_projects.return_value = []
    assert invoke("GET", "/projects", token=token)["statusCode"] == 200
    mocks["Project"].list_projects.assert_called_once()


def test_validation_does_not_log_sensitive_input(services, caplog):
    result = invoke("POST", "/corporation/users", {"password": "secret-password-no-log"})
    assert result["statusCode"] == 400
    assert "secret-password-no-log" not in result["body"] + caplog.text


@pytest.fixture
def uploads(monkeypatch):
    from harvest import uploads as module

    service = create_autospec(module.UploadService, instance=True)
    factory = Mock(return_value=service)
    monkeypatch.setattr(module, "UploadService", factory)
    return service, factory


UPLOADS = P + "/targets/target-a/upload-sessions"
UPLOAD_BODY = {"type": "before", "content_type": "image/jpeg", "size": 123}


def test_upload_session_creation_forwards_verified_actor(uploads):
    service, _ = uploads
    value = {
        "session_id": "session-a",
        "photo_id": "photo-a",
        "expires_at": 100,
        "upload": {"url": "https://upload.example.invalid", "fields": {"key": "quarantine/key"}},
    }
    service.create.return_value = value
    result = invoke("POST", UPLOADS, UPLOAD_BODY)
    assert result["statusCode"] == 201
    assert json.loads(result["body"]) == value
    service.create.assert_called_once_with("user-a", "project-a", "target-a", **UPLOAD_BODY)


def test_upload_rate_limit_uses_sanitized_error_contract(uploads, caplog):
    from harvest.uploads import UploadRateLimited

    service, _ = uploads
    service.create.side_effect = UploadRateLimited("private-rate-limit-key")
    result = invoke("POST", UPLOADS, UPLOAD_BODY)
    assert result["statusCode"] == 429
    assert json.loads(result["body"])["error"]["title"] == "Too many requests"
    assert "private-rate-limit-key" not in result["body"] + caplog.text


@pytest.mark.parametrize("version", [None, "version-a"])
def test_upload_completion_forwards_version_pin(uploads, version):
    service, _ = uploads
    service.complete.return_value = {"message": "successfully", "photo_id": "photo-a"}
    data = {"version_id": version} if version else {}
    result = invoke("POST", UPLOADS + "/session-a/complete", data)
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["photo_id"] == "photo-a"
    service.complete.assert_called_once_with("user-a", "project-a", "target-a", "session-a", **data)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {**UPLOAD_BODY, "size": True},
        {**UPLOAD_BODY, "size": "123"},
        {**UPLOAD_BODY, "size": 0},
        {**UPLOAD_BODY, "size": 4194305},
        {**UPLOAD_BODY, "content_type": "text/html"},
        {**UPLOAD_BODY, "type": "unknown"},
        {**UPLOAD_BODY, "actor_id": "other-user"},
    ],
)
def test_invalid_upload_input_is_400_before_create(uploads, data):
    service, _ = uploads
    assert invoke("POST", UPLOADS, data)["statusCode"] == 400
    service.create.assert_not_called()


@pytest.mark.parametrize("path", [UPLOADS, UPLOADS + "/session-a/complete"])
def test_upload_authentication_precedes_sdk_construction(uploads, path):
    _, factory = uploads
    assert invoke("POST", path, UPLOAD_BODY, token=None)["statusCode"] == 401
    factory.assert_not_called()


@pytest.mark.parametrize(
    "method,path,data",
    [
        ("create", UPLOADS, UPLOAD_BODY),
        ("complete", UPLOADS + "/session-a/complete", {}),
    ],
)
def test_upload_domain_denial_is_403(uploads, method, path, data):
    service, _ = uploads
    getattr(service, method).side_effect = ActionDeniedError()
    assert invoke("POST", path, data)["statusCode"] == 403


@pytest.mark.parametrize(
    "exception,status", [("UploadConflict", 409), ("UploadExpired", 410), ("UploadNotFound", 404)]
)
def test_upload_session_errors_preserve_status(uploads, exception, status, caplog):
    from harvest import uploads as module

    service, _ = uploads
    service.complete.side_effect = getattr(module, exception)("private-upload-session-detail")
    result = invoke("POST", UPLOADS + "/session-a/complete", {})
    assert result["statusCode"] == status
    assert "private-upload-session-detail" not in result["body"] + caplog.text


@pytest.mark.parametrize(
    "path",
    [
        P + "/generation-jobs",
        P + "/generation-jobs/job-a",
        UPLOADS,
        UPLOADS + "/session-a/complete",
    ],
)
def test_new_endpoint_preflight_does_not_construct_services(services, jobs, uploads, path):
    assert invoke("OPTIONS", path, token=None)["statusCode"] == 204
    services[1].assert_not_called()
    jobs[1].assert_not_called()
    uploads[1].assert_not_called()


@pytest.mark.parametrize(
    "data",
    [
        {"version_id": ""},
        {"version_id": "x" * 1025},
        {"version_id": 123},
        {"session_id": "foreign"},
    ],
)
def test_upload_completion_invalid_body_is_rejected_before_sdk(uploads, data):
    service, factory = uploads
    assert invoke("POST", UPLOADS + "/session-a/complete", data)["statusCode"] == 400
    service.complete.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize("flag", ["property_title", "property_hierarchy", "property_date"])
@pytest.mark.parametrize("value", ["False", "True", 0, 1])
def test_template_flags_require_json_booleans(services, flag, value):
    mocks, _ = services
    result = invoke("POST", P + "/templates", {**TEMPLATE, flag: value})
    assert result["statusCode"] == 400
    mocks["Template"].create.assert_not_called()


def test_template_upload_preserves_disabled_features(services):
    mocks, _ = services
    mocks["Template"].create.return_value = {"template_id": "new-template"}
    data = {
        **TEMPLATE,
        "property_title": False,
        "property_hierarchy": False,
        "property_date": False,
    }
    assert invoke("POST", P + "/templates", data)["statusCode"] == 200
    mocks["Template"].create.assert_called_once_with(**data)
