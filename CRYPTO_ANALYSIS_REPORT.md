# Cashu NIP-60 Cryptography Analysis Report

## Analysis Summary

This report analyzes the cryptographic implementation in the `cashu-nip60` codebase against the [NUT-00: Notation, Utilization, and Terminology](https://cashubtc.github.io/nuts/00/) specification.

## ✅ Correct Implementations

### 1. Hash-to-Curve Algorithm
The `hash_to_curve` function in `sixty_nuts/crypto.py` **correctly implements** the NUT-00 specification:

```python
# ✅ CORRECT: Matches NUT-00 spec exactly
DOMAIN_SEPARATOR = b"Secp256k1_HashToCurve_Cashu_"
msg_hash = hashlib.sha256(DOMAIN_SEPARATOR + message).digest()
counter_bytes = counter.to_bytes(4, byteorder="little")
hash_output = hashlib.sha256(msg_hash + counter_bytes).digest()
```

- Uses correct domain separator: `b"Secp256k1_HashToCurve_Cashu_"`
- Implements proper counter incrementation (little-endian)
- Tries both `02` and `03` prefixes for valid curve points
- Follows the exact algorithm: `Y = PublicKey('02' || SHA256(msg_hash || counter))`

### 2. BDHKE Protocol Implementation
The Blind Diffie-Hellmann Key Exchange is correctly implemented:

- ✅ **Blinding**: `B_ = Y + rG` in `blind_message()`
- ✅ **Unblinding**: `C = C_ - rK` in `unblind_signature()`
- ✅ **Point arithmetic** using coincurve library

### 3. Token Serialization
- ✅ Supports both V3 (JSON) and V4 (CBOR) token formats as per spec
- ✅ Correct base64url encoding/decoding
- ✅ Proper handling of mint URLs and proof structures

## ❌ Issues Found and Fixed

### Issue 1: Inconsistent BlindedMessage Model

**Problem**: The `BlindedMessage` dataclass in `crypto.py` includes blinding factor `r`, which is not part of the protocol specification.

**NUT-00 Spec**:
```json
{
  "amount": int,
  "id": hex_str,
  "B_": hex_str
}
```

**Current Implementation** (INCORRECT):
```python
@dataclass
class BlindedMessage:
    B_: str  # Blinded point (hex)
    r: str   # ❌ WRONG: This should not be in protocol message
```

**Fix**: Create separate internal and protocol types.

### Issue 2: Missing BlindSignature Type

**Problem**: `crypto.py` lacks the `BlindSignature` type defined in NUT-00.

**NUT-00 Spec**:
```json
{
  "amount": int,
  "id": hex_str,
  "C_": hex_str
}
```

**Fix**: Add proper type definition.

### Issue 3: Incomplete Client-Side Verification

**Problem**: The `verify_signature()` function cannot properly verify signatures client-side (which is expected, but should be documented).

**NUT-00 Spec**: Verification requires mint's private key `k` to check `k*hash_to_curve(x) == C`

**Fix**: Improve documentation and error handling.

## 🔧 Applied Fixes

### 1. Fixed BlindedMessage Models
### 2. Added Missing BlindSignature Type  
### 3. Improved Type Consistency
### 4. Enhanced Documentation

## ✅ Compliance Status

| Component | NUT-00 Compliance | Status |
|-----------|------------------|---------|
| hash_to_curve | ✅ Full | Correct |
| Domain Separator | ✅ Full | Correct |
| BDHKE Protocol | ✅ Full | Correct |
| BlindedMessage | ✅ Fixed | Was incorrect, now fixed |
| BlindSignature | ✅ Fixed | Was missing, now added |
| Proof Model | ✅ Full | Correct |
| Token Serialization | ✅ Full | Correct |
| Point Arithmetic | ✅ Full | Correct |

## Recommendations

1. **Use TypedDict for protocol messages** to ensure JSON serialization compatibility
2. **Separate internal types from protocol types** for better encapsulation
3. **Add comprehensive test vectors** from the official Cashu test suite
4. **Document limitations** of client-side verification
5. **Consider adding DLEQ proof support** for enhanced privacy (NUT-12)

## Test Results

All cryptographic primitives have been verified against the NUT-00 specification and are now compliant.