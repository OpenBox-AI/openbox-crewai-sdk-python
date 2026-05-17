"""Shared helpers for DID e2e tests."""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_wrong_seed_b64() -> str:
    """Return a base64-encoded Ed25519 seed that will not match any provisioned DID."""
    key = ed25519.Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(seed).decode("ascii")
