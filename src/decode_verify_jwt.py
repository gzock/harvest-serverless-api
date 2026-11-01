"""Lazy Cognito ID-token validation for the existing public API contract."""

import os
from functools import lru_cache

import jwt


class AuthenticationError(ValueError):
    status_code = 401
    code = 100
    title = "Unauthenticated"
    message = "A valid authentication token is required."

    def __str__(self):
        return self.message


@lru_cache(maxsize=8)
def _jwks_client(issuer):
    # Constructing/importing this module never performs network I/O. PyJWKClient
    # refreshes the cache when a previously unseen signing key is encountered.
    return jwt.PyJWKClient(
        issuer + "/.well-known/jwks.json", timeout=5, cache_jwk_set=True, lifespan=300
    )


def decode_verify_jwt(token):
    if not isinstance(token, str) or not token.strip() or len(token) > 16384:
        raise AuthenticationError()
    parts = token.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1]
    elif len(parts) == 1:
        token = parts[0]  # Existing clients send the bare ID token.
    else:
        raise AuthenticationError()
    region = os.environ.get("COGNITO_REGION", os.environ.get("AWS_REGION", "ap-northeast-1"))
    pool_id = os.environ.get("COGNITO_USER_POOL_ID")
    client_id = os.environ.get("COGNITO_CLIENT_ID")
    if not pool_id or not client_id:
        raise RuntimeError("Cognito authentication configuration is missing")
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AuthenticationError()
        key = _jwks_client(issuer).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=client_id,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub", "token_use", "cognito:username"]
            },
        )
        if claims["token_use"] != "id" or not all(
            isinstance(claims.get(name), str) and claims[name]
            for name in ("sub", "cognito:username")
        ):
            raise AuthenticationError()
        return claims
    except jwt.PyJWTError:
        # Never expose the token, claims or JWKS errors to clients/logs.
        raise AuthenticationError() from None
