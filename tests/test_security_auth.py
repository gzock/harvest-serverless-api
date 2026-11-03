import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import Mock

import decode_verify_jwt as verifier
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from harvest.auth import Auth
from harvest.utils.make_response_utils import make_response
from harvest.utils.request_decorator import RequestDecorator


@pytest.fixture
def token_factory(monkeypatch):
    monkeypatch.setenv("COGNITO_REGION", "ap-northeast-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "pool-test")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client-test")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = Mock()
    jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=private.public_key())
    monkeypatch.setattr(verifier, "_jwks_client", lambda issuer: jwks)

    def make(**overrides):
        claims = {
            "iss": "https://cognito-idp.ap-northeast-1.amazonaws.com/pool-test",
            "aud": "client-test",
            "sub": "subject",
            "cognito:username": "user-a",
            "preferred_username": "Private Person",
            "token_use": "id",
            "iat": int(time.time()) - 5,
            "exp": int(time.time()) + 300,
        }
        claims.update(overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "key-1"})

    return make


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": 1},
        {"iat": int(time.time()) + 3600},
        {"iss": "https://attacker.invalid"},
        {"aud": "another-client"},
        {"token_use": "access"},
        {"exp": None},
        {"sub": None},
        {"cognito:username": None},
        {"cognito:username": ""},
    ],
)
def test_jwt_rejects_wrong_claims(token_factory, changes):
    with pytest.raises(verifier.AuthenticationError):
        verifier.decode_verify_jwt(token_factory(**changes))


def test_jwt_accepts_existing_and_bearer_contract(token_factory):
    token = token_factory()
    assert verifier.decode_verify_jwt(token)["cognito:username"] == "user-a"
    assert verifier.decode_verify_jwt("Bearer " + token)["sub"] == "subject"


def test_jwt_rejects_other_signature(token_factory):
    good = token_factory()
    payload = jwt.decode(good, options={"verify_signature": False})
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(verifier.AuthenticationError):
        verifier.decode_verify_jwt(
            jwt.encode(payload, other, algorithm="RS256", headers={"kid": "key-1"})
        )


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "Basic abc",
        "Bearer abc extra",
        "malformed",
        jwt.encode({"sub": "fake"}, "test-only-HMAC-key-with-at-least-32-bytes", algorithm="HS256"),
    ],
)
def test_invalid_authorization_rejected(token_factory, token):
    with pytest.raises(verifier.AuthenticationError):
        verifier.decode_verify_jwt(token)


def event(headers=None, method="POST"):
    return {
        "requestContext": {"httpMethod": method, "identity": {"cognitoIdentityId": "untrusted"}},
        "path": "/projects",
        "headers": headers or {},
        "body": '{"password":"never-log-this"}',
    }


def test_adapter_case_insensitive_and_logs_no_secrets(token_factory, caplog):
    token = token_factory()
    with caplog.at_level(logging.INFO):
        request = RequestDecorator(event({"authorization": "Bearer " + token}))
    assert request.get_identity_id() == "user-a"
    assert request.get_body() == {"password": "never-log-this"}
    assert token not in caplog.text
    assert "never-log-this" not in caplog.text
    assert "Private Person" not in caplog.text


def test_missing_auth_does_not_trust_gateway_identity():
    with pytest.raises(verifier.AuthenticationError):
        RequestDecorator(event())
    assert RequestDecorator(event(method="OPTIONS")).get_identity_id() is None


def test_response_keeps_error_shape_without_logging_url(caplog):
    with caplog.at_level(logging.DEBUG):
        result = make_response(body={"download_url": "https://example.invalid/?secret=one"})
    assert "secret=one" not in caplog.text
    assert json.loads(result["body"])["download_url"]
    assert make_response(body=verifier.AuthenticationError())["statusCode"] == 401
    assert make_response(body=None)["body"] == "null"


@pytest.fixture
def auth(monkeypatch):
    controller = Mock()
    controller.project_is_available.return_value = True
    monkeypatch.setattr("harvest.auth.RoleController", lambda *_: controller)
    result = Auth(None, None, "user-a", "project-a")
    return result, controller


@pytest.mark.parametrize(
    "role,minimum,allowed",
    [
        ("guest", "reporter", False),
        ("owner", "admin", True),
        ("worker", None, True),
        ("invalid", "admin", False),
    ],
)
def test_role_matrix(auth, role, minimum, allowed):
    guard, controller = auth
    controller.show.return_value = {"role": role}
    assert guard.guard(minimum) is allowed


def test_revoked_or_switched_membership_does_not_use_cached_role(auth):
    guard, controller = auth
    controller.show.return_value = {"role": "owner", "status": "active"}
    assert guard.guard("admin")
    controller.show.return_value = {"role": "owner", "status": "reject"}
    assert not guard.guard()
    guard.set_project_id("project-b")
    controller.show.return_value = None
    assert not guard.guard()


def test_deleted_project_does_not_grant_access(auth):
    guard, controller = auth
    controller.show.return_value = {"role": "owner"}
    controller.project_is_available.return_value = False
    assert not guard.guard("owner")


def test_deletion_retry_explicitly_requests_deleting_access(auth):
    guard, controller = auth
    controller.show.return_value = {"role": "owner"}
    assert guard.guard("owner", allow_deleting=True)
    controller.project_is_available.assert_called_once_with(allow_deleting=True)
