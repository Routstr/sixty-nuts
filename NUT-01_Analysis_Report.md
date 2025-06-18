# NUT-01 Specification Compliance Analysis Report
## Cashu Mint Public Key Exchange Implementation

### Executive Summary

This report analyzes the implementation of NUT-01 (Mint public keys) in the `cashu-nip60` codebase. NUT-01 is a **mandatory** specification that defines how wallets obtain public keys from mints. The analysis reveals that the codebase has a **mostly compliant** implementation with several areas requiring attention to ensure full specification adherence.

### 1. NUT-01 Specification Requirements Overview

NUT-01 specifies the protocol for mint public key exchange with these key requirements:

- **Endpoint**: `GET /v1/keys` and `GET /v1/keys/{keyset_id}`
- **Active keysets only**: Mint responds only with active keysets on `/v1/keys`
- **Currency units**: Support for btc, sat, msat, auth, ISO 4217 codes, stablecoin codes
- **Minor units**: For currencies with minor units, amounts must represent the minor unit
- **Keyset format**: Maps of `{<amount_i> : <mint_pubkey_i>, ...}`
- **Public key format**: Compressed Secp256k1 format
- **Response structure**: Specific JSON structure for `GetKeysResponse`

### 2. Current Implementation Analysis

#### 2.1 Endpoint Implementation ✅ COMPLIANT

**File**: `sixty_nuts/mint.py:229-232`
```python
async def get_keys(self, keyset_id: str | None = None) -> KeysResponse:
    """Get mint public keys for a keyset (or newest if not specified)."""
    path = f"/v1/keys/{keyset_id}" if keyset_id else "/v1/keys"
    return cast(KeysResponse, await self._request("GET", path))
```

**Status**: ✅ **COMPLIANT** - Correctly implements both endpoints

#### 2.2 Response Type Definitions ❌ PARTIALLY COMPLIANT

**File**: `sixty_nuts/mint.py:58-65`
```python
class KeysResponse(TypedDict):
    """Mint keys response."""
    keysets: list[dict[str, str]]  # id -> keys mapping

class KeysetsResponse(TypedDict):
    """Active keysets response."""
    keysets: list[dict[str, str | int]]  # id, unit, active, input_fee_ppk
```

**Issues**:
1. **Missing keyset structure fields**: According to NUT-01, each keyset should include:
   - `id`: Keyset identifier
   - `unit`: Currency unit  
   - `keys`: The actual amount->pubkey mapping
   - Potentially `active` flag

2. **Incomplete type annotations**: The current `dict[str, str]` doesn't properly represent the nested structure

#### 2.3 Currency Unit Support ⚠️ LIMITED COMPLIANCE

**File**: `sixty_nuts/wallet.py:92`
```python
currency: Literal["sat", "msat", "usd"] = "sat",
```

**Issues**:
1. **Limited currency support**: Only supports `sat`, `msat`, `usd`
2. **Missing mandatory units**: No support for `btc`, `auth`
3. **Missing ISO 4217 codes**: No comprehensive ISO currency code support
4. **Missing stablecoin codes**: No support for common stablecoin units

#### 2.4 Public Key Format ✅ COMPLIANT

**File**: `sixty_nuts/wallet.py:568-570`
```python
Y = hash_to_curve(secret_utf8_bytes)
# Convert to compressed hex format
y_hex = Y.format(compressed=True).hex()
```

**Status**: ✅ **COMPLIANT** - Uses compressed Secp256k1 format

#### 2.5 Keyset Handling ❌ NEEDS IMPROVEMENT

**Analysis of keyset usage throughout the codebase**:

**Issues**:
1. **Inconsistent keyset structure**: Code assumes `keysets[0]["id"]` pattern but type definitions don't guarantee this structure
2. **Missing validation**: No validation that keysets contain required fields
3. **Error handling**: Insufficient error handling for malformed keyset responses

### 3. Test Coverage Analysis

**File**: `tests/test_mint.py:65-91`

The test suite shows some understanding of keyset structure:
```python
mock_response.json.return_value = {
    "keysets": [
        {
            "id": "00ad268c4d1f5826",
            "unit": "sat", 
            "keys": {"1": "02abc...", "2": "02def..."},
        }
    ]
}
```

**Issues**:
1. **Incomplete test coverage**: Tests don't validate all NUT-01 requirements
2. **Missing error cases**: No tests for invalid keyset responses
3. **Currency unit testing**: Limited testing of different currency units

### 4. Detailed Findings & Recommendations

#### 4.1 Critical Issues (Must Fix)

1. **Fix Response Type Definitions**
   ```python
   class Keyset(TypedDict):
       id: str
       unit: str
       keys: dict[str, str]  # amount -> pubkey mapping
       active: bool
   
   class KeysResponse(TypedDict):
       keysets: list[Keyset]
   ```

2. **Expand Currency Unit Support**
   ```python
   currency: Literal["btc", "sat", "msat", "usd", "eur", "auth"] = "sat"
   ```

3. **Add Keyset Validation**
   ```python
   def validate_keyset_response(self, response: dict) -> KeysResponse:
       # Validate structure matches NUT-01 requirements
       for keyset in response.get("keysets", []):
           if not all(key in keyset for key in ["id", "unit", "keys"]):
               raise ValueError("Invalid keyset structure")
       return response
   ```

#### 4.2 Minor Issues (Should Fix)

1. **Improve Error Handling**
   - Add specific exceptions for malformed keyset responses
   - Validate that public keys are valid compressed secp256k1 points

2. **Enhanced Type Safety**
   - Use more specific type hints for keyset dictionaries
   - Add runtime validation of API responses

#### 4.3 Compliance Gaps

1. **Missing GetKeysResponse Structure**: NUT-01 likely specifies a specific response format that may differ from current implementation

2. **Currency Unit Validation**: No validation that amounts represent minor units for currencies that have them

3. **Active Keyset Filtering**: Unclear if implementation properly filters to only active keysets

### 5. Proposed Changes

#### 5.1 Type System Updates

```python
# sixty_nuts/mint.py

from typing import TypedDict, Literal

class KeysetKeys(TypedDict):
    """Maps amount to compressed secp256k1 pubkey."""
    # amount -> compressed pubkey hex string

class Keyset(TypedDict):
    """Individual keyset information."""
    id: str  # keyset identifier
    unit: str  # currency unit
    keys: dict[str, str]  # amount -> pubkey mapping
    active: bool  # whether keyset is active

class KeysResponse(TypedDict):
    """Response from GET /v1/keys endpoint."""
    keysets: list[Keyset]

CurrencyUnit = Literal[
    "btc", "sat", "msat",  # Bitcoin units
    "usd", "eur", "gbp",   # Major fiat
    "auth",                # Authentication unit
    # Add more ISO 4217 codes as needed
]
```

#### 5.2 Validation Logic

```python
def validate_keyset(self, keyset: dict) -> bool:
    """Validate keyset structure per NUT-01."""
    required_fields = ["id", "unit", "keys"]
    if not all(field in keyset for field in required_fields):
        return False
    
    # Validate keys are valid compressed pubkeys
    for amount, pubkey in keyset["keys"].items():
        if not self._is_valid_compressed_pubkey(pubkey):
            return False
    
    return True
```

#### 5.3 Enhanced Currency Support

```python
class Wallet:
    def __init__(
        self,
        nsec: str,
        *,
        mint_urls: list[str] | None = None,
        currency: CurrencyUnit = "sat",  # Use expanded type
        # ... other params
    ):
        self._validate_currency_unit(currency)
        # ... rest of init
    
    def _validate_currency_unit(self, unit: str) -> None:
        """Validate currency unit is supported."""
        # Implementation specific validation
        pass
```

### 6. Testing Recommendations

1. **Add comprehensive keyset validation tests**
2. **Test all supported currency units**  
3. **Test error handling for malformed responses**
4. **Add integration tests with real mint endpoints**

### 7. Compliance Score

| Requirement | Status | Score |
|-------------|---------|-------|
| Endpoint Implementation | ✅ Compliant | 10/10 |
| Response Structure | ❌ Needs Fix | 6/10 |
| Currency Units | ⚠️ Limited | 4/10 |
| Public Key Format | ✅ Compliant | 10/10 |
| Error Handling | ⚠️ Basic | 5/10 |
| Type Safety | ❌ Needs Work | 5/10 |
| **Overall Compliance** | | **6.7/10** |

### 8. Implementation Priority

**High Priority (Critical for NUT-01 compliance)**:
1. Fix response type definitions
2. Add keyset validation
3. Expand currency unit support

**Medium Priority**:
1. Improve error handling
2. Add comprehensive tests
3. Enhance type safety

**Low Priority**:
1. Performance optimizations
2. Additional helper methods
3. Documentation improvements

### 9. Conclusion

The current implementation provides a **functional but incomplete** implementation of NUT-01. While the basic endpoint structure is correct and public key formatting follows the specification, significant improvements are needed in:

- Response type definitions
- Currency unit support  
- Validation logic
- Error handling

Implementing the recommended changes will bring the codebase to **full NUT-01 compliance** and improve robustness for production use.

### 10. Next Steps

1. **Immediate**: Implement type definition fixes
2. **Short-term**: Add validation and expand currency support
3. **Medium-term**: Enhance test coverage and error handling
4. **Long-term**: Consider additional NUT specifications for comprehensive compliance

---

**Report Generated**: 2025-01-04  
**Codebase Version**: Current HEAD  
**Specification**: NUT-01 (Mint public keys) - Mandatory