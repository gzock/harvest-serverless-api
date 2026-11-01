"""Compatibility adapter around a validated API Gateway request."""

import base64
import json
import logging

from decode_verify_jwt import AuthenticationError, decode_verify_jwt


class RequestDecorator:
    logger = logging.getLogger(__name__)

    def __init__(self, event):
        if not isinstance(event, dict):
            raise ValueError("Invalid request event")
        context = event.get("requestContext") or {}
        self.__method = (
            event.get("httpMethod")
            or context.get("httpMethod")
            or context.get("http", {}).get("method", "")
        ).upper()
        self.__path = event.get("path", event.get("rawPath", ""))
        self.__path_params = event.get("pathParameters") or {}
        self.__query = event.get("queryStringParameters") or {}
        self.__body = event.get("body")
        self.__headers = event.get("headers") or {}
        self.__event = event
        self.__identity_id = None
        self.__username = None
        self.__user_context = None
        if self.__method != "OPTIONS":
            authorization = next(
                (value for key, value in self.__headers.items() if key.lower() == "authorization"),
                None,
            )
            if not authorization:
                raise AuthenticationError()
            claims = decode_verify_jwt(authorization)
            self.__user_context = claims
            self.__identity_id = claims["cognito:username"]
            self.__username = claims.get("preferred_username") or self.__identity_id
        # Allowlisted metadata only: no body, headers, claims, URL query or names.
        self.logger.info("request received", extra={"http_method": self.__method})

    def get_identity_id(self):
        return self.__identity_id

    def get_username(self):
        return self.__username

    def get_user_context(self):
        return dict(self.__user_context or {})

    def get_method(self):
        return self.__method

    def get_path(self):
        return self.__path

    def get_path_params(self):
        return self.__path_params

    def get_query(self):
        return self.__query

    def get_body(self):
        body = self.__body
        if self.__event.get("isBase64Encoded") and isinstance(body, str):
            body = base64.b64decode(body, validate=True).decode("utf-8")
        if body is None or body == "":
            return {}
        parsed = json.loads(body) if isinstance(body, str) else body
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object")
        return parsed

    def get_headers(self):
        return self.__headers

    def get_raw_event(self):
        return self.__event
