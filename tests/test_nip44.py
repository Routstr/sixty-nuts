#!/usr/bin/env python3
"""Test NIP-44 encryption implementation."""

from coincurve import PrivateKey

from sixty_nuts.crypto import NIP44Encrypt


def test_nip44_encryption():
    """Test basic encryption and decryption."""
    # Generate two key pairs
    alice_privkey = PrivateKey()
    alice_pubkey = alice_privkey.public_key.format(compressed=True).hex()

    bob_privkey = PrivateKey()
    bob_pubkey = bob_privkey.public_key.format(compressed=True).hex()

    # Test message
    plaintext = "Hello, this is a secret message!"

    # Alice encrypts to Bob
    ciphertext = NIP44Encrypt.encrypt(plaintext, alice_privkey, bob_pubkey)
    print(f"Encrypted: {ciphertext[:50]}...")

    # Bob decrypts from Alice
    decrypted = NIP44Encrypt.decrypt(ciphertext, bob_privkey, alice_pubkey)
    print(f"Decrypted: {decrypted}")

    assert decrypted == plaintext, "Decryption failed!"
    print("✓ Encryption/Decryption successful")

    # Test conversation key symmetry
    conv_key_1 = NIP44Encrypt.get_conversation_key(alice_privkey, bob_pubkey)
    conv_key_2 = NIP44Encrypt.get_conversation_key(bob_privkey, alice_pubkey)

    assert conv_key_1 == conv_key_2, "Conversation keys don't match!"
    print("✓ Conversation key symmetry verified")

    # Test padding
    short_msg = "A"
    padded = NIP44Encrypt.pad(short_msg.encode())
    # 32 bytes total padded length: 2 bytes length prefix + 1 byte message + 29 bytes padding
    assert len(padded) == 32, f"Expected padded length 32, got {len(padded)}"

    unpadded = NIP44Encrypt.unpad(padded)
    assert unpadded.decode() == short_msg, "Padding/unpadding failed!"
    print("✓ Padding/unpadding successful")


if __name__ == "__main__":
    test_nip44_encryption()
    print("\nAll tests passed! 🎉")
