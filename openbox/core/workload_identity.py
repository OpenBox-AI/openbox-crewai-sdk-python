"""IAM v3 wire contract, kept in parity with openbox-sdk-python 1.4.0.

This module intentionally has no dependency on the base SDK's OTel runtime.
It validates Core bootstrap metadata and signs Keycloak private-key assertions.
"""

from __future__ import annotations

import base64
import json
import math
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from openbox.core.errors import OpenBoxAuthError, OpenBoxConfigError, OpenBoxNetworkError

AUTH_BOOTSTRAP_PATH = "/api/v3/auth/bootstrap"
WORKLOAD_TOKEN_HEADER = "X-OpenBox-Workload-Token"
REFRESH_SKEW_SECONDS = 30
MAX_CACHE_SECONDS = 300


def load_workload_private_key(pem: str) -> rsa.RSAPrivateKey:
    """Validate locally without including key material in errors."""
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise OpenBoxConfigError(
            "Invalid workload_private_key: expected an unencrypted PKCS8 PEM RSA private key."
        ) from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise OpenBoxConfigError(
            "Invalid workload_private_key: expected an RSA private key of at least 2048 bits."
        )
    return key


def parse_optional_workload_private_key(pem: str | None) -> str | None:
    if pem is None:
        return None
    normalized = pem.strip()
    load_workload_private_key(normalized)
    return normalized


@dataclass(frozen=True)
class WorkloadBootstrapDocument:
    token_endpoint: str
    issuer: str
    audience: str
    client_id: str
    service_account_id: str
    activation_version: str
    identity_source: str
    kid: str


@dataclass(frozen=True)
class WorkloadAccessToken:
    value: str = field(repr=False)
    expires_at: datetime

    def is_fresh(self) -> bool:
        return (
            datetime.now(timezone.utc) + timedelta(seconds=REFRESH_SKEW_SECONDS) < self.expires_at
        )


def _require_string(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise OpenBoxConfigError(f"Workload bootstrap: {name!r} must be a non-empty string.")
    return value.strip()


def _require_uuid(data: dict[str, Any], name: str) -> str:
    value = _require_string(data, name)
    try:
        parsed = UUID(value)
    except ValueError:
        raise OpenBoxConfigError(f"Workload bootstrap: {name!r} must be a UUID.") from None
    if str(parsed) != value.lower():
        raise OpenBoxConfigError(f"Workload bootstrap: {name!r} must be a canonical UUID.")
    return str(parsed)


def parse_bootstrap_document(data: Any) -> WorkloadBootstrapDocument:
    if not isinstance(data, dict):
        raise OpenBoxConfigError("Workload bootstrap response must be a JSON object.")
    if data.get("bootstrap_version") != 3 or data.get("contract_version") != 3:
        raise OpenBoxConfigError("Unsupported workload bootstrap or contract version.")
    issuer = _require_string(data, "issuer").rstrip("/")
    endpoint = _require_string(data, "token_endpoint")
    for name, value in (("issuer", issuer), ("token_endpoint", endpoint)):
        try:
            url = urlparse(value)
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            valid = (
                url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and (url.scheme == "https" or (url.scheme == "http" and local))
            )
            # Validate malformed ports before a request reaches the HTTP client.
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise OpenBoxConfigError(f"Workload bootstrap: {name!r} must be a valid HTTPS URL.")
    if endpoint != issuer + "/protocol/openid-connect/token":
        raise OpenBoxConfigError("Workload token endpoint must belong to the advertised issuer.")
    source = _require_string(data, "identity_source").lower()
    if source not in {"openbox", "okta", "entra"}:
        raise OpenBoxConfigError("Unsupported workload identity_source.")
    return WorkloadBootstrapDocument(
        token_endpoint=endpoint,
        issuer=issuer,
        audience=_require_string(data, "audience"),
        client_id=_require_string(data, "client_id"),
        service_account_id=_require_uuid(data, "service_account_id"),
        activation_version=_require_uuid(data, "activation_version"),
        identity_source=source,
        kid=_require_string(data, "kid"),
    )


def extract_reason_code(body: bytes, *, include_reason: bool = True) -> str | None:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("reason_code") or data.get("code")
    if value is None and include_reason:
        value = data.get("reason")
    return value if isinstance(value, str) else None


def parse_bootstrap_response(status: int, body: bytes) -> WorkloadBootstrapDocument | None:
    if status == 404 or (
        status == 409
        and extract_reason_code(body, include_reason=False) == "workload_identity_unavailable"
    ):
        return None
    if status in (401, 403):
        raise OpenBoxAuthError(f"Workload identity bootstrap rejected (HTTP {status}).")
    if status != 200:
        raise OpenBoxNetworkError(f"Workload identity bootstrap failed (HTTP {status}).")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise OpenBoxConfigError("Workload bootstrap response must be valid JSON.") from None
    return parse_bootstrap_document(data)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def build_private_key_jwt(key: rsa.RSAPrivateKey, document: WorkloadBootstrapDocument) -> str:
    issued = int(datetime.now(timezone.utc).timestamp())
    header = {"alg": "RS256", "kid": document.kid, "typ": "JWT"}
    claims = {
        "aud": document.token_endpoint,
        "exp": issued + 60,
        "iat": issued,
        "iss": document.client_id,
        "jti": secrets.token_urlsafe(24),
        "sub": document.client_id,
    }
    encoded = [
        _b64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        for value in (header, claims)
    ]
    signing_input = ".".join(encoded).encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode("ascii") + "." + _b64url(signature)


def parse_token_response(status: int, body: bytes) -> WorkloadAccessToken:
    if status in (400, 401, 403):
        raise OpenBoxAuthError(f"Workload token exchange rejected (HTTP {status}).")
    if status != 200:
        raise OpenBoxNetworkError(f"Workload token exchange failed (HTTP {status}).")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise OpenBoxConfigError("Workload token response must be valid JSON.") from None
    if not isinstance(data, dict):
        raise OpenBoxConfigError("Workload token response must be a JSON object.")
    token, kind, lifetime = data.get("access_token"), data.get("token_type"), data.get("expires_in")
    if (
        not isinstance(token, str)
        or not token
        or not token.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in token)
    ):
        raise OpenBoxConfigError("Workload token response requires a valid access_token.")
    if not isinstance(kind, str) or kind.lower() != "bearer":
        raise OpenBoxConfigError("Workload token_type must be Bearer.")
    if (
        not isinstance(lifetime, (int, float))
        or isinstance(lifetime, bool)
        or (isinstance(lifetime, float) and not math.isfinite(lifetime))
        or lifetime <= REFRESH_SKEW_SECONDS
    ):
        raise OpenBoxConfigError("Workload expires_in must be finite and greater than 30 seconds.")
    return WorkloadAccessToken(
        value=token,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=min(lifetime, MAX_CACHE_SECONDS)),
    )
