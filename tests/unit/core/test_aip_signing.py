"""Unit tests for AIP request signing."""

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from openbox.core.aip_signing import (
    OPENBOX_AGENT_DID_HEADER,
    OPENBOX_AGENT_NONCE_HEADER,
    OPENBOX_AGENT_SIGNATURE_HEADER,
    OPENBOX_AGENT_TIMESTAMP_HEADER,
    OPENBOX_BODY_SHA256_HEADER,
    AgentIdentity,
    build_canonical_identity_request,
    build_signed_identity_headers,
    validate_agent_did,
    validate_ed25519_private_key,
)


def _generate_identity() -> tuple[AgentIdentity, ed25519.Ed25519PublicKey]:
    private = ed25519.Ed25519PrivateKey.generate()
    seed = private.private_bytes_raw()
    identity = AgentIdentity(
        did="did:aip:ece81073-60e1-5091-b284-cfd247823a77",
        private_key=base64.b64encode(seed).decode("ascii"),
    )
    return identity, private.public_key()


def test_canonical_request_format() -> None:
    canonical = build_canonical_identity_request(
        method="post",
        pathname="/api/v1/governance/evaluate",
        timestamp="2026-04-20T10:00:00Z",
        nonce="abc-123",
        body_sha256="deadbeef",
    )
    assert canonical == "POST\n/api/v1/governance/evaluate\n2026-04-20T10:00:00Z\nabc-123\ndeadbeef"


def test_signed_headers_roundtrip_verifies() -> None:
    identity, public_key = _generate_identity()
    body = '{"hello":"world"}'

    headers = build_signed_identity_headers(
        method="POST",
        pathname="/api/v1/governance/evaluate",
        body=body,
        identity=identity,
    )

    assert headers[OPENBOX_AGENT_DID_HEADER] == identity.did
    assert headers[OPENBOX_BODY_SHA256_HEADER] == hashlib.sha256(body.encode()).hexdigest()

    canonical = build_canonical_identity_request(
        method="POST",
        pathname="/api/v1/governance/evaluate",
        timestamp=headers[OPENBOX_AGENT_TIMESTAMP_HEADER],
        nonce=headers[OPENBOX_AGENT_NONCE_HEADER],
        body_sha256=headers[OPENBOX_BODY_SHA256_HEADER],
    )
    signature = base64.b64decode(headers[OPENBOX_AGENT_SIGNATURE_HEADER])
    public_key.verify(signature, canonical.encode("utf-8"))


def test_nonce_is_unique_per_call() -> None:
    identity, _ = _generate_identity()
    h1 = build_signed_identity_headers(method="POST", pathname="/x", body="", identity=identity)
    h2 = build_signed_identity_headers(method="POST", pathname="/x", body="", identity=identity)
    assert h1[OPENBOX_AGENT_NONCE_HEADER] != h2[OPENBOX_AGENT_NONCE_HEADER]


@pytest.mark.parametrize(
    "did,expected",
    [
        ("did:aip:ece81073-60e1-5091-b284-cfd247823a77", True),
        ("did:aip:ECE81073-60E1-5091-B284-CFD247823A77", False),
        ("did:aip:Ece81073-60e1-5091-b284-cfd247823a77", False),
        ("did:aip:ece81073-60e1-4091-b284-cfd247823a77", False),
        ("did:aip:ece81073-60e1-3091-b284-cfd247823a77", False),
        ("did:aip:00000000-0000-0000-0000-000000000000", False),
        ("did:aip:not-a-uuid", False),
        ("did:other:ece81073-60e1-5091-b284-cfd247823a77", False),
        ("ece81073-60e1-5091-b284-cfd247823a77", False),
        ("did:aip: ece81073-60e1-5091-b284-cfd247823a77", False),
        ("did:aip:urn:uuid:ece81073-60e1-5091-b284-cfd247823a77", False),
        ("did:aip:{ece81073-60e1-5091-b284-cfd247823a77}", False),
        ("", False),
    ],
)
def test_validate_agent_did(did: str, expected: bool) -> None:
    assert validate_agent_did(did) is expected


def test_agent_identity_repr_redacts_private_key() -> None:
    secret = base64.b64encode(b"\xab" * 32).decode("ascii")
    identity = AgentIdentity(
        did="did:aip:ece81073-60e1-5091-b284-cfd247823a77",
        private_key=secret,
    )
    rendered_repr = repr(identity)
    rendered_str = str(identity)
    assert secret not in rendered_repr
    assert secret not in rendered_str
    assert "did:aip:ece81073-60e1-5091-b284-cfd247823a77" in rendered_repr
    assert identity.private_key == secret


def test_agent_identity_remains_frozen() -> None:
    identity = AgentIdentity(
        did="did:aip:ece81073-60e1-5091-b284-cfd247823a77",
        private_key="secret",
    )
    with pytest.raises((AttributeError, Exception)):
        identity.private_key = "tampered"  # type: ignore[misc]


def test_validate_ed25519_private_key() -> None:
    good = base64.b64encode(b"\x00" * 32).decode("ascii")
    short = base64.b64encode(b"\x00" * 16).decode("ascii")
    long_ = base64.b64encode(b"\x00" * 64).decode("ascii")
    assert validate_ed25519_private_key(good) is True
    assert validate_ed25519_private_key(short) is False
    assert validate_ed25519_private_key(long_) is False
    assert validate_ed25519_private_key("not base64!!!") is False
    assert validate_ed25519_private_key("") is False
