"""Prints a new release signing key pair once; writes nothing.

    python tools/release_keys.py
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print("Private key (secret UPDATE_SIGNING_KEY or offline storage):")
    print(base64.b64encode(private).decode())
    print("Public key (RELEASE_PUBLIC_KEYS in update_signature.py):")
    print(base64.b64encode(public).decode())


if __name__ == "__main__":
    main()
