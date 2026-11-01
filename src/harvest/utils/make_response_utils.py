import decimal
import json
import os

from decode_verify_jwt import AuthenticationError
from harvest.exception import HarvestAbstractException


def make_response_headers_for_cors():
    return {
        "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
        "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "http://localhost:4200"),
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def make_base_error_obj():
    return {"error": {"title": "", "message": "", "code": 0, "ref_url": ""}}


def make_response(status_code=200, body=None):
    if isinstance(body, (HarvestAbstractException, AuthenticationError)):
        status_code = getattr(body, "status_code", status_code)
        body = {
            "error": {
                "title": getattr(body, "title", "Invalid request"),
                "message": getattr(body, "message", "Invalid request"),
                "code": getattr(body, "code", 999),
                "ref_url": "",
            }
        }
    elif isinstance(body, Exception):
        body = {
            "error": {
                "title": "Unknown Internal Error",
                "message": "unknown internal error.",
                "code": 999,
                "ref_url": "",
            }
        }
    if isinstance(body, dict) and body.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
        body = {"message": "successfully"}
    try:
        encoded = json.dumps(body, cls=DecimalEncoder)
    except ValueError, TypeError:
        status_code = 500
        encoded = json.dumps({"error": {"code": 999, "message": "unknown internal error."}})
    return {"statusCode": status_code, "headers": make_response_headers_for_cors(), "body": encoded}


class DecimalEncoder(json.JSONEncoder):
    def default(self, value):
        if isinstance(value, decimal.Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        return super().default(value)
