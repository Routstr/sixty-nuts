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

    # Test padding (spec: padded plaintext → 32 B, plus 2-byte length prefix)
    short_msg = "A"
    padded = NIP44Encrypt.pad(short_msg.encode())
    # 34 bytes total: 2-byte length prefix + 32-byte padded plaintext
    assert len(padded) == 34, f"Expected padded length 34, got {len(padded)}"

    unpadded = NIP44Encrypt.unpad(padded)
    assert unpadded.decode() == short_msg, "Padding/unpadding failed!"
    print("✓ Padding/unpadding successful")


if __name__ == "__main__":
    test_nip44_encryption()
    print("\nAll tests passed! 🎉")

# ───────────────────────── Additional hard-coded vector test ──────────────────


_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_decode(bech: str) -> tuple[str, bytes]:
    """Return (hrp, data) from a bech32 string – minimal decoder for test."""
    if not bech:
        raise ValueError("empty bech32 string")
    bech = bech.lower()
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        raise ValueError("invalid bech32 format")
    hrp, data_part = bech[:pos], bech[pos + 1 :]

    decoded5 = []
    for ch in data_part:
        idx = _CHARSET.find(ch)
        if idx == -1:
            raise ValueError(f"invalid bech32 char: {ch}")
        decoded5.append(idx)
    if len(decoded5) < 6:
        raise ValueError("bech32 data too short")
    decoded5 = decoded5[:-6]  # strip checksum (trusted input)

    acc = 0
    bits = 0
    out = bytearray()
    for val in decoded5:
        acc = (acc << 5) | val
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    if bits >= 5:
        raise ValueError("invalid padding in bech32 data")
    return hrp, bytes(out)


def test_nip44_hardcoded_vector() -> None:
    """Decrypt a publicly supplied ciphertext and round-trip it again."""

    sender_nsec = "nsec1g076as5d528uenjkx7xcwwjv86569ax5hkd3vl5a8e00hk6zhcjsn8pwqe"
    ciphertext = (
        "Ai917eoTypQZRbFLzNSGVppQ4InuSNlcj4CQd/bkzgR2yM1M28IC5tHKMzw7WpHW5S/oKIGP7xC39IJ2"
        "8WlM1b7FcV33jEPtFBS5k0AvxHhbdle9HjxBz9S923wXBVGeD53LklUQGONL/NPL2UaduyOBfTDaFJRUR"
        "J31DVwny20H0mxPJOqnom/wSTgOUtqtZKoyyjDguiKdNM/ycz5CuT4YWmKejjsfPanAYSX3+CmWSRSNQN"
        "xBZmGQYVduOVOc4OLjHaJnxc94deIX9vMCULwrkfFN6tb0799rkc7XnHqBP5+FBUUNBehvHS35E6YDF1+"
        "tT7gCFVc7Z1CbYs/TVm7pGpvHDgVv89ITgC3est0TvL7nB63l0mgOoLV4VbJCa4HxSgr42dUhxD4amdYp"
        "LHil9cT2fynzLYwR4TbMltrAmnqBCr6+ZC3P4prrgislwdF1jfJSWeXn+xDAg+ueq+P4qj65g7VOugybb"
        "BnVZ1pL1GLObDfLvqcs5H12LWpH0emfFnPirDWSxJwQzt/SsnvPp0Ayk6O/BFUUbAqv/UYe6sDs62eq4t"
        "7U6kGRQfWOjCJ10ypAyIKOuz7p788IlPK3dJcTGd7HlJdgzdv2b4LRRLTtTw49KQ86vPcyabOqiKZ9rWS"
        "hZdSPsOSiZHZxyGGsKbhAO16ug6Ybi6GFn279L2yGJbbL3OcIM/US/U7fs/lKaSikIB48FKB0xh11XXY7"
        "8gPm2EQwjZAzLFCS/qe6Z2DfjijslvhoD5+L32GuU0FnSDv6QaGYdhQ1vXs3sjRgsTdhxSgby1Zm2ausN"
        "w5co85dldGonRiSnqfagR2hszQSCYHAQBJZz4m1xpK429pg0V4th//pi+zOKv47j6YlTgufanvzXPU/oZ"
        "6cAwpWcmz6J/F7ywnXJrZeM7Zri5sDcVetUXQqLEvZewywh7pQsgd+G2k5QH8ij9I1K8f8bq+vJrGutxB"
        "eMeHEQrRqN6J8Lth8TLVCYJ2VJ1aGe7r/l4E98B72mujpSJ/mT6WE3NzdZYu0DbT5nhoNgY5AY70u8OIP"
        "/qAaihq05E42YcuKAvZ1N3RfDddY6fUklBbmb1JF83UnvkGbv9oatSUpcli64EACF3tYOnV+iqL0i+Fcc"
        "xoSn5UqZOFmtOu3J5KpggZpbP6phvahMT8VfEnqtvSZYyrg3nz4YfVHrcNwnlV4uPs5FL7hdiRQtN447I"
        "ce8dXVctZOB6ZrOyRwO8dk4MOVyb7mUkcc8+oVRYDHl1ckLKTduu4mHoqyyZooWDLVFpvO"
    )

    plaintext_expected = '{"mint":"https://mint.minibits.cash/Bitcoin","proofs":[{"secret":"9c52c2a28703f707ae148184a505f044ffec17872f3343dab34504fceebdbd3b","C":"02fa3460e7dda04f0848b6e66e87ef369b77a06f2304b9e7f0bd0cd78f98aaa7f2","amount":8,"id":"00500550f0494146","dleq":{"r":"0e13dc04b571059b9ad775d898d4c145d87c4aecff326efed8b73fce43b0badd","s":"a1a64c7f82bbc111c7ff862be9375fecbe89d8066ecb3c0acfb911b42cf65e11","e":"64a10585e9de5b2044cee5518332c5267074f7adc3c4d51ed45164a326739be3"}},{"secret":"4e329ba084c0b63e0319698fff1451cf6c836e9c283a89e0c90f6c19ecb0ab4d","C":"033c4701cae515b8399537bad0cd63ac60c9bfbec7698c892f6d246b7ca1a4523d","amount":4,"id":"00500550f0494146","dleq":{"r":"6f9558c0346565628b43cf7836333e91018820a1d0a1edd328d02e6d385c5ec5","s":"468ab7af450b9f0cbb3f72e1aa2eb51aeb6723c8e30096d64ce47afcda151f1c","e":"75e25b28dbd408fa19abb1a9babe35fb969052f7e73214a7549cfce35957b8e9"}}]}'

    # derive priv/pub
    hrp, sk_bytes = _bech32_decode(sender_nsec)
    assert hrp == "nsec"
    priv = PrivateKey(sk_bytes)
    pub_hex = priv.public_key.format(compressed=True).hex()

    decrypted = NIP44Encrypt.decrypt(ciphertext, priv, pub_hex)
    assert decrypted == plaintext_expected

    # round-trip with new nonce
    ct2 = NIP44Encrypt.encrypt(decrypted, priv, pub_hex)
    assert NIP44Encrypt.decrypt(ct2, priv, pub_hex) == decrypted
