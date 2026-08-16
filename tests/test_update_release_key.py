from __future__ import annotations

import base64
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.verify_update_release_key import verify_release_keypair


def test_verify_release_keypair_valid() -> None:
    private_key = Ed25519PrivateKey.generate()
    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    priv_b64 = base64.b64encode(priv_bytes).decode("ascii")
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")

    # 예외 없이 통과해야 함
    verify_release_keypair(priv_b64, pub_b64)


def test_verify_release_keypair_mismatch() -> None:
    priv1 = Ed25519PrivateKey.generate()
    priv2 = Ed25519PrivateKey.generate()

    priv1_bytes = priv1.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub2_bytes = priv2.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    with pytest.raises(ValueError, match="일치하지 않습니다"):
        verify_release_keypair(
            base64.b64encode(priv1_bytes).decode("ascii"),
            base64.b64encode(pub2_bytes).decode("ascii"),
        )
