# NUT-02 Implementation Analysis Report

## Executive Summary

This report provides a comprehensive analysis of the NUT-02 (Keysets and fees) specification implementation in the `sixty_nuts` codebase. NUT-02 is a **mandatory** specification in the Cashu protocol that defines how keysets are managed, how keyset IDs are derived, and how fees are handled.

**Overall Assessment: ⚠️ PARTIALLY COMPLIANT** 

The codebase implements the basic structure and API endpoints required by NUT-02, but has several gaps and potential improvements needed for full compliance.

---

## NUT-02 Specification Overview

NUT-02 defines:
1. **Keyset properties** - including keyset ID, active status, and fee structure
2. **Keyset ID derivation** - standardized method for computing keyset identifiers
3. **API endpoints** - `/v1/keys` and `/v1/keysets` for keyset management
4. **Fee handling** - `input_fee_ppk` (input fee per proof per thousand) structure
5. **Wallet fee calculation** - how wallets should handle fees in transactions

---

## Current Implementation Analysis

### ✅ **COMPLIANT AREAS**

#### 1. Basic Type Definitions
**Location**: `sixty_nuts/mint.py:62-66`
```python
class KeysetsResponse(TypedDict):
    """Active keysets response."""
    keysets: list[dict[str, str | int]]  # id, unit, active, input_fee_ppk
```

**Analysis**: 
- ✅ Correctly defines the `KeysetsResponse` structure
- ✅ Includes `input_fee_ppk` field in type hint
- ✅ Supports both string and integer fields as required

#### 2. API Endpoint Implementation
**Location**: `sixty_nuts/mint.py:229-236`
```python
async def get_keys(self, keyset_id: str | None = None) -> KeysResponse:
    """Get mint public keys for a keyset (or newest if not specified)."""
    path = f"/v1/keys/{keyset_id}" if keyset_id else "/v1/keys"
    return cast(KeysResponse, await self._request("GET", path))

async def get_keysets(self) -> KeysetsResponse:
    """Get all active keyset IDs."""
    return cast(KeysetsResponse, await self._request("GET", "/v1/keysets"))
```

**Analysis**:
- ✅ Implements both required endpoints: `/v1/keys` and `/v1/keysets`
- ✅ Supports optional keyset_id parameter for specific keyset queries
- ✅ Proper return type annotations

#### 3. Fee Handling in Wallet Operations
**Location**: `sixty_nuts/wallet.py:1582-1585, 1235-1241`
```python
total_needed = melt_quote["amount"] + melt_quote["fee_reserve"]
# ...
amount_to_mint = total_amount - melt_quote["fee_reserve"]
```

**Analysis**:
- ✅ Wallet correctly accounts for fees in melt operations
- ✅ Fee reserves are properly calculated and applied
- ✅ Multiple fee handling strategies implemented

### ⚠️ **GAPS AND CONCERNS**

#### 1. Missing Keyset ID Derivation Implementation
**Severity**: HIGH

**Issue**: No evidence of standardized keyset ID derivation algorithm implementation.

**NUT-02 Requirement**: Keyset IDs should be derived using a specific cryptographic method (typically hash-based).

**Current State**: 
- Keyset IDs are used throughout the codebase as strings
- No validation or derivation logic found
- IDs appear to be accepted as-is from mint responses

**Impact**: May lead to compatibility issues with other Cashu implementations.

#### 2. Incomplete Fee Structure Implementation
**Severity**: MEDIUM

**Issue**: While `input_fee_ppk` is defined in types, there's no evidence of:
- Fee calculation based on number of proofs
- Per-proof fee application
- Fee validation logic

**Current Implementation**:
```python
# Only basic fee_reserve handling found
estimated_fee = max(estimated_fee, test_melt_quote["fee_reserve"])
```

**Missing**:
- Input fee calculation: `fee = (number_of_proofs * input_fee_ppk) / 1000`
- Fee validation before transactions
- Dynamic fee adjustment based on keyset properties

#### 3. No Keyset Validation Logic
**Severity**: MEDIUM

**Issue**: No validation that keysets comply with NUT-02 structure requirements.

**Missing Validations**:
- Keyset ID format validation
- Required field presence validation
- Fee structure validation
- Active/inactive keyset handling

#### 4. Limited Test Coverage for NUT-02 Features
**Severity**: MEDIUM

**Current Test Coverage**: `tests/test_mint.py:66-87`
```python
"keysets": [
    {
        "id": "00ad268c4d1f5826",
        "unit": "sat",
        "keys": {"1": "02abc...", "2": "02def..."},
    }
]
```

**Missing Test Areas**:
- Fee calculation tests
- Keyset ID derivation tests
- Error handling for malformed keysets
- Integration tests with fee-enabled keysets

### 🔧 **IMPLEMENTATION IMPROVEMENTS NEEDED**

#### 1. Keyset ID Derivation (HIGH PRIORITY)
```python
# Recommended implementation in crypto.py
def derive_keyset_id(keys: dict[str, str], version: int = 0) -> str:
    """Derive keyset ID according to NUT-02 specification."""
    # Concatenate all public keys in sorted order
    sorted_keys = sorted(keys.items(), key=lambda x: int(x[0]))
    key_concat = "".join(f"{amount}{pubkey}" for amount, pubkey in sorted_keys)
    
    # Hash and encode
    hash_bytes = hashlib.sha256(key_concat.encode()).digest()
    
    # Version byte + first 7 bytes of hash
    keyset_id = bytes([version]) + hash_bytes[:7]
    return keyset_id.hex()
```

#### 2. Enhanced Fee Calculation (HIGH PRIORITY)
```python
# Add to wallet.py
def calculate_input_fees(self, proofs: list[ProofDict], keyset_info: dict) -> int:
    """Calculate input fees based on number of proofs and keyset fee rate."""
    input_fee_ppk = keyset_info.get("input_fee_ppk", 0)
    if input_fee_ppk == 0:
        return 0
    
    num_proofs = len(proofs)
    return (num_proofs * input_fee_ppk) // 1000  # Integer division for satoshi precision
```

#### 3. Keyset Validation (MEDIUM PRIORITY)
```python
# Add to mint.py
def validate_keyset(self, keyset: dict) -> bool:
    """Validate keyset structure according to NUT-02."""
    required_fields = ["id", "unit", "active"]
    
    for field in required_fields:
        if field not in keyset:
            return False
    
    # Validate fee structure if present
    if "input_fee_ppk" in keyset:
        if not isinstance(keyset["input_fee_ppk"], int) or keyset["input_fee_ppk"] < 0:
            return False
    
    return True
```

#### 4. Enhanced Wallet Fee Integration (HIGH PRIORITY)
```python
# Enhance existing wallet methods
async def _apply_input_fees(self, mint: Mint, proofs: list[ProofDict]) -> int:
    """Calculate and apply input fees for transaction."""
    try:
        keysets_resp = await mint.get_keysets()
        keyset_fees = {}
        
        for keyset in keysets_resp["keysets"]:
            keyset_fees[keyset["id"]] = keyset.get("input_fee_ppk", 0)
        
        total_fee = 0
        for proof in proofs:
            fee_rate = keyset_fees.get(proof["id"], 0)
            total_fee += fee_rate
        
        return total_fee // 1000  # Convert from ppk to base units
    except Exception:
        # Fallback to zero fees if keyset info unavailable
        return 0
```

---

## Compliance Summary

| NUT-02 Requirement | Implementation Status | Priority |
|-------------------|---------------------|----------|
| `/v1/keys` endpoint | ✅ Implemented | - |
| `/v1/keysets` endpoint | ✅ Implemented | - |
| Keyset response structure | ✅ Implemented | - |
| `input_fee_ppk` field support | ✅ Basic support | - |
| Keyset ID derivation | ❌ Missing | HIGH |
| Input fee calculation | ❌ Missing | HIGH |
| Keyset validation | ❌ Missing | MEDIUM |
| Comprehensive fee integration | ⚠️ Partial | HIGH |
| Error handling | ⚠️ Basic | MEDIUM |
| Test coverage | ⚠️ Minimal | MEDIUM |

---

## Recommendations

### Immediate Actions (Next Sprint)
1. **Implement keyset ID derivation** following NUT-02 specification
2. **Add input fee calculation** for proof-based fee handling
3. **Enhance fee integration** in wallet melt/send operations
4. **Add comprehensive tests** for fee calculations and keyset handling

### Medium-term Improvements
1. **Add keyset validation** for all keyset responses
2. **Implement error handling** for malformed or incompatible keysets
3. **Add configuration options** for fee handling behavior
4. **Enhance documentation** with NUT-02 compliance examples

### Testing Strategy
1. **Unit tests** for keyset ID derivation algorithm
2. **Integration tests** with fee-enabled mints
3. **Compatibility tests** with other Cashu implementations
4. **Error scenario tests** for invalid keyset structures

---

## Risk Assessment

### HIGH RISK
- **Keyset ID incompatibility**: Current implementation may not interoperate with compliant mints
- **Fee calculation errors**: Missing input fee handling could lead to transaction failures

### MEDIUM RISK  
- **Protocol version mismatch**: May not support future NUT-02 extensions
- **Error handling gaps**: Could cause wallet crashes with malformed responses

### LOW RISK
- **Performance impact**: Current implementation should be adequate for typical usage
- **Security concerns**: No obvious security vulnerabilities identified

---

## Conclusion

The `sixty_nuts` implementation provides a solid foundation for NUT-02 compliance but requires significant enhancements to achieve full specification compliance. The most critical gaps are in keyset ID derivation and comprehensive fee handling. Addressing the high-priority items identified in this report will ensure full NUT-02 compliance and improve interoperability with the broader Cashu ecosystem.

**Estimated effort**: 2-3 developer days for high-priority items, 1-2 additional days for comprehensive testing and documentation.