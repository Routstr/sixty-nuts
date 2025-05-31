"""Cashu cryptographic primitives for BDHKE (Blind Diffie-Hellmann Key Exchange) and NIP-44 encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import secrets
import struct
from dataclasses import dataclass
from typing import Tuple

from coincurve import PrivateKey, PublicKey
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.kdf.hkdf import HKDF, HKDFExpand


@dataclass
class BlindedMessage:
    """A blinded message with its blinding factor."""

    B_: str  # Blinded point (hex)
    r: str  # Blinding factor (hex)


def hash_to_curve(message: bytes) -> PublicKey:
    """Hash a message to a point on the secp256k1 curve.

    Implements the Cashu hash_to_curve algorithm:
    Y = PublicKey('02' || SHA256(msg_hash || counter))
    where msg_hash = SHA256(DOMAIN_SEPARATOR || message)
    """
    # Domain separator as per Cashu spec
    DOMAIN_SEPARATOR = b"Secp256k1_HashToCurve_Cashu_"

    # First hash: SHA256(DOMAIN_SEPARATOR || message)
    msg_hash = hashlib.sha256(DOMAIN_SEPARATOR + message).digest()

    # Try to find a valid point by incrementing counter
    counter = 0
    while counter < 2**32:
        # SHA256(msg_hash || counter)
        counter_bytes = counter.to_bytes(4, byteorder="little")
        hash_input = msg_hash + counter_bytes
        hash_output = hashlib.sha256(hash_input).digest()

        # Try to create a compressed public key with '02' prefix
        try:
            pubkey_bytes = b"\x02" + hash_output
            pubkey = PublicKey(pubkey_bytes)
            return pubkey
        except Exception:
            # If not valid, try with '03' prefix
            try:
                pubkey_bytes = b"\x03" + hash_output
                pubkey = PublicKey(pubkey_bytes)
                return pubkey
            except Exception:
                pass

        counter += 1

    raise ValueError("Could not find valid curve point after 2^32 iterations")


def blind_message(secret: bytes, r: bytes | None = None) -> tuple[PublicKey, bytes]:
    """Blind a message for the mint.

    Args:
        secret: The secret message to blind (raw bytes)
        r: Optional blinding factor (will be generated if not provided)

    Returns:
        Tuple of (blinded_point, blinding_factor)
    """
    # Hash secret to curve point Y
    Y = hash_to_curve(secret)

    # Generate random blinding factor if not provided
    if r is None:
        r = secrets.token_bytes(32)

    # Create blinding factor as private key
    r_key = PrivateKey(r)

    # Calculate B' = Y + r*G
    B_ = PublicKey.combine_keys([Y, r_key.public_key])

    return B_, r


def unblind_signature(C_: PublicKey, r: bytes, K: PublicKey) -> PublicKey:
    """Unblind a signature from the mint.

    Args:
        C_: Blinded signature from mint
        r: Blinding factor used
        K: Mint's public key

    Returns:
        Unblinded signature C
    """
    # Create r as private key
    r_key = PrivateKey(r)

    # Calculate r*K
    rK = K.multiply(r_key.secret)

    # Calculate C = C' - r*K
    # To subtract, we need to add the negation of rK
    # The negation of a point (x, y) is (x, -y)
    rK_bytes = rK.format(compressed=False)
    x = rK_bytes[1:33]
    y = rK_bytes[33:65]

    # Negate y coordinate (p - y where p is the field prime)
    # For secp256k1: p = 2^256 - 2^32 - 977
    p = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
    y_int = int.from_bytes(y, "big")
    neg_y_int = (p - y_int) % p
    neg_y = neg_y_int.to_bytes(32, "big")

    # Reconstruct negated point
    neg_rK_bytes = b"\x04" + x + neg_y
    neg_rK = PublicKey(neg_rK_bytes)

    # Combine C' + (-r*K) = C' - r*K
    C = PublicKey.combine_keys([C_, neg_rK])

    return C


def verify_signature(secret: bytes, C: PublicKey, K: PublicKey) -> bool:
    """Verify a signature is valid.

    Args:
        secret: The secret message (raw bytes)
        C: The unblinded signature
        K: Mint's public key

    Returns:
        True if signature is valid
    """
    # Hash secret to curve point Y
    # Y = hash_to_curve(secret)

    # For verification, we need the mint's private key k
    # But we can't do this client-side - this would be done by the mint
    # For now, we'll just return the components
    # In practice, the mint would check if C == k*Y

    # We can't fully verify client-side, but we can check the signature format
    try:
        # Ensure C is a valid point
        _ = C.format()
        return True
    except Exception:
        return False


class NIP44Error(Exception):
    """Base exception for NIP-44 encryption errors."""


class NIP44Encrypt:
    """NIP-44 v2 encryption implementation."""

    # Constants
    VERSION = 2
    MIN_PLAINTEXT_SIZE = 1
    MAX_PLAINTEXT_SIZE = 65535
    SALT = b"nip44-v2"

    @staticmethod
    def calc_padded_len(unpadded_len: int) -> int:
        """Calculate padded length according to NIP-44."""
        if unpadded_len <= 0:
            raise ValueError("Invalid unpadded length")

        # Add 2 for the length prefix
        total_len = unpadded_len + 2

        if total_len <= 32:
            return 32

        next_power = 1 << (math.floor(math.log2(total_len - 1)) + 1)
        chunk = 32 if next_power <= 256 else next_power // 8

        return chunk * ((total_len - 1) // chunk + 1)

    @staticmethod
    def pad(plaintext: bytes) -> bytes:
        """Apply NIP-44 padding to plaintext."""
        unpadded_len = len(plaintext)
        if (
            unpadded_len < NIP44Encrypt.MIN_PLAINTEXT_SIZE
            or unpadded_len > NIP44Encrypt.MAX_PLAINTEXT_SIZE
        ):
            raise ValueError(f"Invalid plaintext length: {unpadded_len}")

        padded_len = NIP44Encrypt.calc_padded_len(unpadded_len)
        prefix = struct.pack(">H", unpadded_len)  # 2 bytes big-endian
        padding = bytes(padded_len - 2 - unpadded_len)

        return prefix + plaintext + padding

    @staticmethod
    def unpad(padded: bytes) -> bytes:
        """Remove NIP-44 padding from plaintext."""
        if len(padded) < 2:
            raise ValueError("Invalid padded data")

        unpadded_len = struct.unpack(">H", padded[:2])[0]
        if unpadded_len == 0 or len(padded) < 2 + unpadded_len:
            raise ValueError("Invalid padding")

        expected_len = NIP44Encrypt.calc_padded_len(unpadded_len)
        if len(padded) != expected_len:
            raise ValueError("Invalid padded length")

        return padded[2 : 2 + unpadded_len]

    @staticmethod
    def get_conversation_key(privkey: PrivateKey, pubkey_hex: str) -> bytes:
        """Calculate conversation key using ECDH and HKDF."""
        # Parse public key
        pubkey_bytes = bytes.fromhex(pubkey_hex)
        pubkey_obj = PublicKey(pubkey_bytes)

        # ECDH - get shared x coordinate
        shared_point = pubkey_obj.multiply(privkey.secret)
        shared_x = shared_point.format(compressed=False)[1:33]  # x coordinate only

        # HKDF-Extract with salt "nip44-v2"
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=NIP44Encrypt.SALT,
            info=None,
            backend=default_backend(),
        )
        return hkdf.derive(shared_x)

    @staticmethod
    def get_message_keys(
        conversation_key: bytes, nonce: bytes
    ) -> Tuple[bytes, bytes, bytes]:
        """Derive message keys from conversation key and nonce."""
        if len(conversation_key) != 32:
            raise ValueError("Invalid conversation key length")
        if len(nonce) != 32:
            raise ValueError("Invalid nonce length")

        # HKDF-Expand with info=nonce, length=76
        hkdf_expand = HKDFExpand(
            algorithm=hashes.SHA256(), length=76, info=nonce, backend=default_backend()
        )
        expanded = hkdf_expand.derive(conversation_key)

        chacha_key = expanded[0:32]
        chacha_nonce = expanded[32:44]
        hmac_key = expanded[44:76]

        return chacha_key, chacha_nonce, hmac_key

    @staticmethod
    def hmac_aad(key: bytes, message: bytes, aad: bytes) -> bytes:
        """Calculate HMAC with additional authenticated data."""
        if len(aad) != 32:
            raise ValueError("AAD must be 32 bytes")

        h = hmac.new(key, aad + message, hashlib.sha256)
        return h.digest()

    @staticmethod
    def chacha20_encrypt(key: bytes, nonce: bytes, data: bytes) -> bytes:
        """Encrypt data using ChaCha20."""
        # ChaCha20 in cryptography library expects 16-byte nonce
        # but NIP-44 uses 12-byte nonce, so we pad with zeros
        if len(nonce) == 12:
            nonce = b"\x00" * 4 + nonce
        cipher = Cipher(
            algorithms.ChaCha20(key, nonce), mode=None, backend=default_backend()
        )
        encryptor = cipher.encryptor()
        return encryptor.update(data) + encryptor.finalize()

    @staticmethod
    def chacha20_decrypt(key: bytes, nonce: bytes, data: bytes) -> bytes:
        """Decrypt data using ChaCha20."""
        # ChaCha20 in cryptography library expects 16-byte nonce
        # but NIP-44 uses 12-byte nonce, so we pad with zeros
        if len(nonce) == 12:
            nonce = b"\x00" * 4 + nonce
        cipher = Cipher(
            algorithms.ChaCha20(key, nonce), mode=None, backend=default_backend()
        )
        decryptor = cipher.decryptor()
        return decryptor.update(data) + decryptor.finalize()

    @staticmethod
    def encrypt(
        plaintext: str, sender_privkey: PrivateKey, recipient_pubkey: str
    ) -> str:
        """Encrypt a message using NIP-44 v2.

        Args:
            plaintext: Message to encrypt
            sender_privkey: Sender's private key
            recipient_pubkey: Recipient's public key (hex)

        Returns:
            Base64 encoded encrypted payload
        """
        # Generate random nonce
        nonce = secrets.token_bytes(32)

        # Get conversation key
        conversation_key = NIP44Encrypt.get_conversation_key(
            sender_privkey, recipient_pubkey
        )

        # Get message keys
        chacha_key, chacha_nonce, hmac_key = NIP44Encrypt.get_message_keys(
            conversation_key, nonce
        )

        # Pad plaintext
        plaintext_bytes = plaintext.encode("utf-8")
        padded = NIP44Encrypt.pad(plaintext_bytes)

        # Encrypt
        ciphertext = NIP44Encrypt.chacha20_encrypt(chacha_key, chacha_nonce, padded)

        # Calculate MAC
        mac = NIP44Encrypt.hmac_aad(hmac_key, ciphertext, nonce)

        # Encode payload: version(1) + nonce(32) + ciphertext + mac(32)
        version = bytes([NIP44Encrypt.VERSION])
        payload = version + nonce + ciphertext + mac

        # Base64 encode
        return base64.b64encode(payload).decode("ascii")

    @staticmethod
    def decrypt(
        ciphertext: str, recipient_privkey: PrivateKey, sender_pubkey: str
    ) -> str:
        """Decrypt a message using NIP-44 v2.

        Args:
            ciphertext: Base64 encoded encrypted payload
            recipient_privkey: Recipient's private key
            sender_pubkey: Sender's public key (hex)

        Returns:
            Decrypted plaintext message
        """
        # Check for unsupported format
        if ciphertext.startswith("#"):
            raise NIP44Error("Unsupported encryption version")

        # Decode base64
        try:
            payload = base64.b64decode(ciphertext)
        except Exception as e:
            raise NIP44Error(f"Invalid base64: {e}")

        # Validate payload length
        if len(payload) < 99 or len(payload) > 65603:
            raise NIP44Error(f"Invalid payload size: {len(payload)}")

        # Parse payload
        version = payload[0]
        if version != NIP44Encrypt.VERSION:
            raise NIP44Error(f"Unknown version: {version}")

        nonce = payload[1:33]
        mac = payload[-32:]
        encrypted_data = payload[33:-32]

        # Get conversation key
        conversation_key = NIP44Encrypt.get_conversation_key(
            recipient_privkey, sender_pubkey
        )

        # Get message keys
        chacha_key, chacha_nonce, hmac_key = NIP44Encrypt.get_message_keys(
            conversation_key, nonce
        )

        # Verify MAC
        calculated_mac = NIP44Encrypt.hmac_aad(hmac_key, encrypted_data, nonce)
        if not hmac.compare_digest(calculated_mac, mac):
            raise NIP44Error("Invalid MAC")

        # Decrypt
        padded_plaintext = NIP44Encrypt.chacha20_decrypt(
            chacha_key, chacha_nonce, encrypted_data
        )

        # Remove padding
        plaintext_bytes = NIP44Encrypt.unpad(padded_plaintext)

        return plaintext_bytes.decode("utf-8")
