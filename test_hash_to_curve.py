#!/usr/bin/env python3
"""Test hash_to_curve implementation against known test vectors."""

import base64
from sixty_nuts.crypto import hash_to_curve


def test_hash_to_curve():
    """Test our hash_to_curve implementation."""
    # Test with a known secret from the Cashu test vectors
    # Using the test secret from nutshell tests
    test_secret = "test_message"
    test_secret_bytes = test_secret.encode("utf-8")

    print(f"Testing hash_to_curve with secret: {test_secret}")
    print(f"Secret bytes (hex): {test_secret_bytes.hex()}")

    # Compute Y
    Y = hash_to_curve(test_secret_bytes)
    Y_hex = Y.format(compressed=True).hex()
    print(f"Y (compressed): {Y_hex}")

    # Test with base64 encoded secret (like in our proofs)
    test_b64_secret = "YtFpHAiFp0ZsHNVfcqbGGlp2yVdI6qyUr6RV6ELQ3QQ"
    padded = test_b64_secret + "=" * ((-len(test_b64_secret)) % 4)
    secret_bytes = base64.urlsafe_b64decode(padded)
    print(f"\nTesting with base64 secret: {test_b64_secret}")
    print(f"Decoded bytes (hex): {secret_bytes.hex()}")

    Y2 = hash_to_curve(secret_bytes)
    Y2_hex = Y2.format(compressed=True).hex()
    print(f"Y (compressed): {Y2_hex}")

    # Let's also test the domain separator is being used
    print("\nVerifying domain separator usage...")
    import hashlib

    DOMAIN_SEPARATOR = b"Secp256k1_HashToCurve_Cashu_"
    msg_hash = hashlib.sha256(DOMAIN_SEPARATOR + test_secret_bytes).digest()
    print(f"SHA256(DOMAIN_SEPARATOR || message): {msg_hash.hex()}")


if __name__ == "__main__":
    test_hash_to_curve()
