"""Cashu cryptographic primitives for BDHKE (Blind Diffie-Hellmann Key Exchange) and NIP-44 encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Tuple

from coincurve import PrivateKey, PublicKey
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

from .types import BlindedMessage

try:
    from bech32 import bech32_decode, convertbits  # type: ignore
except ModuleNotFoundError:  # pragma: no cover – allow runtime miss
    bech32_decode = None  # type: ignore
    convertbits = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# Internal Types (for implementation use)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class BlindingData:
    """Internal data structure for blinding operations.

    Contains both the blinded message and the blinding factor.
    The blinding factor should never be sent over the network.
    """

    B_: str  # Blinded point (hex)
    r: str  # Blinding factor (hex) - KEEP SECRET!


def hash_to_curve(message: bytes) -> PublicKey:
    """Hash a message to a point on the secp256k1 curve.

    Implements the Cashu hash_to_curve algorithm per NUT-00:
    Y = PublicKey('02' || SHA256(msg_hash || counter))
    where msg_hash = SHA256(DOMAIN_SEPARATOR || message)

    Args:
        message: The message to hash (raw bytes)

    Returns:
        PublicKey point on the secp256k1 curve
    """
    # Domain separator as per Cashu NUT-00 specification
    DOMAIN_SEPARATOR = b"Secp256k1_HashToCurve_Cashu_"

    # First hash: SHA256(DOMAIN_SEPARATOR || message)
    msg_hash = hashlib.sha256(DOMAIN_SEPARATOR + message).digest()

    # Try to find a valid point by incrementing counter
    counter = 0
    while counter < 2**32:
        # SHA256(msg_hash || counter) - counter is little-endian
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
    """Blind a message for the mint using BDHKE.

    Implements the blinding step: B_ = Y + rG
    where Y = hash_to_curve(secret) and r is the blinding factor.

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

    # Calculate B_ = Y + r*G
    B_ = PublicKey.combine_keys([Y, r_key.public_key])

    return B_, r


def unblind_signature(C_: PublicKey, r: bytes, K: PublicKey) -> PublicKey:
    """Unblind a signature from the mint using BDHKE.

    Implements the unblinding step: C = C_ - rK
    where C_ is the blinded signature, r is the blinding factor, and K is the mint's public key.

    Args:
        C_: Blinded signature from mint
        r: Blinding factor used in blinding
        K: Mint's public key for this denomination

    Returns:
        Unblinded signature C
    """
    # Create r as private key
    r_key = PrivateKey(r)

    # Calculate r*K
    rK = K.multiply(r_key.secret)

    # Calculate C = C_ - r*K
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

    # Combine C_ + (-r*K) = C_ - r*K
    C = PublicKey.combine_keys([C_, neg_rK])

    return C


def verify_signature(secret: bytes, C: PublicKey, K: PublicKey) -> bool:
    """Verify a signature is valid (limited client-side verification).

    Note: Full verification requires the mint's private key k and checks if C == k*Y.
    This client-side verification only checks if C is a valid curve point.
    The actual verification k*hash_to_curve(secret) == C must be done by the mint.

    Args:
        secret: The secret message (raw bytes)
        C: The unblinded signature
        K: Mint's public key (not used in this limited verification)

    Returns:
        True if C is a valid curve point, False otherwise
    """
    try:
        # Ensure C is a valid point
        _ = C.format()
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────────────────────────────────────


def create_blinded_message(
    amount: int, keyset_id: str, secret: bytes | None = None
) -> tuple[BlindedMessage, BlindingData]:
    """Create a blinded message for the mint with proper separation of concerns.

    Args:
        amount: The amount for this blinded message
        keyset_id: The keyset ID to use
        secret: Optional secret (will be generated if not provided)

    Returns:
        Tuple of (BlindedMessage for network, BlindingData for internal use)
    """
    if secret is None:
        secret = secrets.token_bytes(32)

    # Blind the message
    B_, r = blind_message(secret)

    # Create protocol message (without blinding factor)
    blinded_msg = BlindedMessage(
        amount=amount, id=keyset_id, B_=B_.format(compressed=True).hex()
    )

    # Create internal data (with blinding factor)
    blinding_data = BlindingData(B_=B_.format(compressed=True).hex(), r=r.hex())

    return blinded_msg, blinding_data


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
        """Return the padded *plaintext* length (without the 2-byte length prefix).

        The algorithm follows NIP-44 v2 reference implementation:
        1. Plaintexts of length ≤ 32 are always padded to exactly 32 bytes.
        2. For longer messages, the length is rounded **up** to the next multiple of
           `chunk`, where `chunk` is 32 bytes for messages ≤ 256 bytes and
           `next_power/8` otherwise, with `next_power` being the next power of two
           of `(unpadded_len - 1)`.
        """
        if unpadded_len <= 0:
            raise ValueError("Invalid unpadded length")

        if unpadded_len <= 32:
            return 32

        next_power = 1 << (math.floor(math.log2(unpadded_len - 1)) + 1)
        chunk = 32 if next_power <= 256 else next_power // 8

        return chunk * ((unpadded_len - 1) // chunk + 1)

    @staticmethod
    def pad(plaintext: bytes) -> bytes:
        """Apply NIP-44 padding to plaintext."""
        unpadded_len = len(plaintext)
        if (
            unpadded_len < NIP44Encrypt.MIN_PLAINTEXT_SIZE
            or unpadded_len > NIP44Encrypt.MAX_PLAINTEXT_SIZE
        ):
            raise ValueError(f"Invalid plaintext length: {unpadded_len}")

        data_len = NIP44Encrypt.calc_padded_len(unpadded_len)

        # 2-byte big-endian length prefix precedes the plaintext (see spec).
        prefix = struct.pack(">H", unpadded_len)

        padding = bytes(data_len - unpadded_len)

        # Total padded length = 2 (prefix) + data_len
        return prefix + plaintext + padding

    @staticmethod
    def unpad(padded: bytes) -> bytes:
        """Remove NIP-44 padding from plaintext."""
        if len(padded) < 2:
            raise ValueError("Invalid padded data")

        unpadded_len = struct.unpack(">H", padded[:2])[0]
        if unpadded_len == 0 or len(padded) < 2 + unpadded_len:
            raise ValueError("Invalid padding")

        expected_len = 2 + NIP44Encrypt.calc_padded_len(unpadded_len)
        if len(padded) != expected_len:
            raise ValueError("Invalid padded length")

        return padded[2 : 2 + unpadded_len]

    @staticmethod
    def get_conversation_key(privkey: PrivateKey, pubkey_hex: str) -> bytes:
        """Return the 32-byte conversation key (`PRK`) as defined by NIP-44.

        The key is the HKDF-Extract of the shared ECDH *x* coordinate using the
        ASCII salt ``"nip44-v2"`` and SHA-256.
        """
        pubkey_bytes = bytes.fromhex(pubkey_hex)
        pubkey_obj = PublicKey(pubkey_bytes)

        # ECDH – shared secret is the x-coordinate of k * P.
        shared_point = pubkey_obj.multiply(privkey.secret)
        shared_x = shared_point.format(compressed=False)[1:33]

        # HKDF-Extract == HMAC(salt, IKM)
        return hmac.new(NIP44Encrypt.SALT, shared_x, hashlib.sha256).digest()

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


def derive_keyset_id(keys: dict[str, str], version: int = 0) -> str:
    """Derive keyset ID according to NUT-02 specification.

    Args:
        keys: Dictionary mapping amount strings to public key hex strings
        version: Version byte for keyset ID format (default: 0)

    Returns:
        Hex-encoded keyset ID (16 characters: 1 version byte + 7 hash bytes)

    Example:
        keys = {"1": "02abc...", "2": "02def...", "4": "02ghi..."}
        keyset_id = derive_keyset_id(keys)  # Returns "00a1b2c3d4e5f6a7"
    """
    # Sort keys by amount (as integers) for deterministic ordering
    sorted_keys = sorted(keys.items(), key=lambda x: int(x[0]))

    # Concatenate amount and public key for each denomination
    key_concat = "".join(f"{amount}{pubkey}" for amount, pubkey in sorted_keys)

    # Hash the concatenated string
    hash_bytes = hashlib.sha256(key_concat.encode()).digest()

    # Version byte (1 byte) + first 7 bytes of hash = 8 bytes total
    keyset_id_bytes = bytes([version]) + hash_bytes[:7]

    return keyset_id_bytes.hex()


def validate_keyset_id(keyset_id: str, keys: dict[str, str], version: int = 0) -> bool:
    """Validate that a keyset ID matches the expected derivation from keys.

    Args:
        keyset_id: Hex-encoded keyset ID to validate
        keys: Dictionary of amount -> public key mappings
        version: Expected version byte

    Returns:
        True if keyset ID is valid for the given keys
    """
    try:
        # Derive the expected keyset ID
        expected_id = derive_keyset_id(keys, version)
        return keyset_id.lower() == expected_id.lower()
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Wallet Crypto Helpers (moved from wallet.py)
# ──────────────────────────────────────────────────────────────────────────────


def create_blinded_messages_for_amount(
    amount: int, keyset_id: str
) -> tuple[list[BlindedMessage], list[str], list[str]]:
    """Create blinded messages for a given amount using optimal denominations.

    Args:
        amount: Total amount to split into denominations
        keyset_id: The keyset ID to use for the blinded messages

    Returns:
        Tuple of (blinded_messages, secrets, blinding_factors)
    """
    outputs: list[BlindedMessage] = []
    secrets_list: list[str] = []
    blinding_factors: list[str] = []

    remaining = amount

    for denom in [
        16384,
        8192,
        4096,
        2048,
        1024,
        512,
        256,
        128,
        64,
        32,
        16,
        8,
        4,
        2,
        1,
    ]:
        while remaining >= denom:
            secret, r_hex, blinded_msg = create_blinded_message_with_secret(
                denom, keyset_id
            )
            outputs.append(blinded_msg)
            secrets_list.append(secret)
            blinding_factors.append(r_hex)
            remaining -= denom

    return outputs, secrets_list, blinding_factors


def create_blinded_message_with_secret(
    amount: int, keyset_id: str
) -> tuple[str, str, BlindedMessage]:
    """Create a properly blinded message for the mint using the updated crypto API.

    Returns:
        Tuple of (secret_hex, blinding_factor_hex, blinded_message)
    """
    # Generate random 32-byte secret
    secret_bytes = secrets.token_bytes(32)

    # Convert to hex string (this is what Cashu protocol expects)
    secret_hex = secret_bytes.hex()

    # For blinding, use UTF-8 bytes of the hex string (Cashu standard)
    secret_utf8_bytes = secret_hex.encode("utf-8")

    # Use the create_blinded_message function
    blinded_msg, blinding_data = create_blinded_message(
        amount=amount, keyset_id=keyset_id, secret=secret_utf8_bytes
    )

    # The secret that is stored and used in proofs is the hex representation
    # of the random bytes, as per NUT-00 recommendation.
    blinding_factor_hex = blinding_data.r

    return secret_hex, blinding_factor_hex, blinded_msg


def get_mint_pubkey_for_amount(
    keys_data: dict[str, str], amount: int
) -> PublicKey | None:
    """Get the mint's public key for a specific amount.

    Args:
        keys_data: Dictionary mapping amounts to public keys
        amount: The denomination amount

    Returns:
        PublicKey or None if not found
    """
    # Keys are indexed by string amount
    pubkey_hex = keys_data.get(str(amount))
    if pubkey_hex:
        return PublicKey(bytes.fromhex(pubkey_hex))
    return None


def decode_nsec(nsec: str) -> PrivateKey:
    """Decode `nsec` (bech32 as per Nostr) or raw hex private key."""
    if nsec.startswith("nsec1"):
        if bech32_decode is None or convertbits is None:
            raise NotImplementedError(
                "bech32 library missing – install `bech32` to use bech32-encoded nsec keys"
            )

        hrp, data = bech32_decode(nsec)
        if hrp != "nsec" or data is None:
            raise ValueError("Malformed nsec bech32 string")

        decoded = bytes(convertbits(data, 5, 8, False))  # type: ignore
        if len(decoded) != 32:
            raise ValueError("Invalid nsec length after decoding")
        return PrivateKey(decoded)

    # Fallback – treat as raw hex key
    return PrivateKey(bytes.fromhex(nsec))


def generate_privkey() -> str:
    """Generate a new secp256k1 private key for wallet P2PK operations."""
    return PrivateKey().to_hex()


def get_pubkey(privkey: PrivateKey) -> str:
    """Get hex public key from private key (Nostr x-only format)."""
    # Nostr uses x-only public keys (32 bytes, without the prefix byte)
    compressed_pubkey = privkey.public_key.format(compressed=True)
    x_only_pubkey = compressed_pubkey[1:]  # Remove the prefix byte
    return x_only_pubkey.hex()


def get_pubkey_compressed(privkey: PrivateKey) -> str:
    """Get full compressed hex public key for encryption (33 bytes)."""
    return privkey.public_key.format(compressed=True).hex()


def sign_event(event: dict, privkey: PrivateKey) -> dict:
    """Sign a Nostr event with the provided private key."""
    # Ensure event has required fields
    event["pubkey"] = get_pubkey(privkey)
    event["created_at"] = event.get("created_at", int(time.time()))
    event["id"] = compute_event_id(event)

    # Sign the event
    sig = privkey.sign_schnorr(bytes.fromhex(event["id"]))
    event["sig"] = sig.hex()

    return event


def compute_event_id(event: dict) -> str:
    """Compute Nostr event ID (hash of canonical JSON)."""
    # Canonical format: [0, pubkey, created_at, kind, tags, content]
    canonical = json.dumps(
        [
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def nip44_encrypt(
    plaintext: str, sender_privkey: PrivateKey, recipient_pubkey: str | None = None
) -> str:
    """Encrypt content using NIP-44 v2."""
    if recipient_pubkey is None:
        recipient_pubkey = get_pubkey_compressed(sender_privkey)

    return NIP44Encrypt.encrypt(plaintext, sender_privkey, recipient_pubkey)


def nip44_decrypt(
    ciphertext: str, recipient_privkey: PrivateKey, sender_pubkey: str | None = None
) -> str:
    """Decrypt content using NIP-44 v2."""
    if sender_pubkey is None:
        sender_pubkey = get_pubkey_compressed(recipient_privkey)

    return NIP44Encrypt.decrypt(ciphertext, recipient_privkey, sender_pubkey)
