"""Agent Identity Protocol (AIP) request signing for outbound governance calls."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric import ed25519

OPENBOX_AGENT_DID_HEADER = "X-OpenBox-Agent-DID"
OPENBOX_AGENT_NONCE_HEADER = "X-OpenBox-Agent-Nonce"
OPENBOX_AGENT_SIGNATURE_HEADER = "X-OpenBox-Agent-Signature"
OPENBOX_AGENT_TIMESTAMP_HEADER = "X-OpenBox-Agent-Timestamp"
OPENBOX_BODY_SHA256_HEADER = "X-OpenBox-Body-SHA256"

_DID_AIP_PREFIX = "did:aip:"


@dataclass(frozen=True)
class AgentIdentity:
    did: str
    private_key: str

    def __repr__(self) -> str:
        return f"AgentIdentity(did={self.did!r}, private_key='***REDACTED***')"


def validate_agent_did(did: str) -> bool:
    if not did.startswith(_DID_AIP_PREFIX):
        return False
    suffix = did[len(_DID_AIP_PREFIX):]
    if suffix != suffix.lower():
        return False
    try:
        parsed = uuid.UUID(suffix)
    except ValueError:
        return False
    return parsed.version == 5 and str(parsed) == suffix


def validate_ed25519_private_key(private_key: str) -> bool:
    try:
        return len(base64.b64decode(private_key, validate=True)) == 32
    except (ValueError, binascii.Error):
        return False


def build_canonical_identity_request(
    *,
    method: str,
    pathname: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
) -> str:
    return "\n".join([method.upper(), pathname, timestamp, nonce, body_sha256])


def build_signed_identity_headers(
    *,
    method: str,
    pathname: str,
    body: str,
    identity: AgentIdentity,
) -> dict[str, str]:
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    nonce = str(uuid.uuid4())
    body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical = build_canonical_identity_request(
        method=method,
        pathname=pathname,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=body_sha256,
    )
    signature = _sign_canonical(canonical, identity.private_key)
    return {
        OPENBOX_AGENT_DID_HEADER: identity.did,
        OPENBOX_AGENT_NONCE_HEADER: nonce,
        OPENBOX_AGENT_SIGNATURE_HEADER: signature,
        OPENBOX_AGENT_TIMESTAMP_HEADER: timestamp,
        OPENBOX_BODY_SHA256_HEADER: body_sha256,
    }


def _sign_canonical(canonical: str, private_key_b64: str) -> str:
    seed = base64.b64decode(private_key_b64, validate=True)
    key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    signature = key.sign(canonical.encode("utf-8"))
    return base64.b64encode(signature).decode("ascii")
