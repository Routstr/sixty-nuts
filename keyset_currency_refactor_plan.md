# Multi-Keyset and Multi-Currency Refactor Plan

## Overview

This document outlines a comprehensive plan to implement proper multi-keyset and multi-currency support in the sixty_nuts Cashu wallet implementation. Currently, the wallet has partial support for multiple keysets and currencies, but it's not fully implemented. This refactor will enable:

- Support for multiple keysets per mint (different currencies)
- Dynamic denomination fetching from mints
- Easy switching between keysets/currencies
- Universal keyset swap function
- Granular keyset specification for operations
- Proper balance display per keyset/currency

## Current State Analysis

### What's Currently Implemented

1. **Partial Multi-Mint Support**: The wallet can handle multiple mints but treats them mostly independently
2. **Single Currency Per Wallet**: Currency is set at wallet initialization and applies globally
3. **Hardcoded Denominations**: Powers of 2 (1, 2, 4, 8, 16, ...) are hardcoded in `_calculate_optimal_denominations`
4. **Basic Keyset Handling**: Keysets are fetched but only filtered by currency and active status
5. **Token Serialization**: Supports multiple token formats (V3/V4) with currency information

### What's Missing

1. **Keyset Management**: No proper tracking of keysets across mints and currencies
2. **Dynamic Denominations**: Denominations should be fetched from mint keysets
3. **Currency Switching**: No way to work with multiple currencies in the same wallet
4. **Keyset Selection**: Operations don't allow specifying which keyset to use
5. **Multi-Currency Balance**: Balance display doesn't break down by currency

## Architecture Changes

### 1. Data Model Updates

#### Enhanced Keyset Storage

```python
@dataclass
class KeysetInfo:
    """Complete keyset information."""
    id: str
    mint_url: str
    unit: CurrencyUnit
    active: bool
    input_fee_ppk: int = 0
    keys: dict[str, str] = field(default_factory=dict)  # amount -> pubkey
    denominations: list[int] = field(default_factory=list)  # available denominations
    
@dataclass
class WalletState:
    """Enhanced wallet state with keyset tracking."""
    balance_by_keyset: dict[str, int]  # keyset_id -> balance
    proofs: list[ProofDict]
    keysets: dict[str, KeysetInfo]  # keyset_id -> KeysetInfo
    proof_to_event_id: dict[str, str] | None = None
```

#### Enhanced ProofDict

```python
class ProofDict(TypedDict):
    """Enhanced proof with keyset information."""
    id: str  # keyset_id
    amount: int
    secret: str
    C: str
    mint: str
    unit: NotRequired[CurrencyUnit]  # currency unit for this proof
```

### 2. Core Component Updates

#### Wallet Class Enhancements

```python
class Wallet:
    def __init__(self, ...):
        # Remove single currency restriction
        self.default_unit: CurrencyUnit = "sat"  # default unit for operations
        self.keysets: dict[str, KeysetInfo] = {}  # keyset_id -> KeysetInfo
        self.mint_keysets: dict[str, list[str]] = {}  # mint_url -> [keyset_ids]
```

## Implementation Tasks

### Phase 1: Foundation (Core Infrastructure)

#### Task 1.1: Update Data Models

- [ ] Create `KeysetInfo` dataclass with full keyset information
- [ ] Update `WalletState` to include keyset tracking and multi-currency balances
- [ ] Add `unit` field to `ProofDict` for currency tracking
- [ ] Update type hints throughout the codebase

#### Task 1.2: Keyset Management System

- [ ] Create `KeysetManager` class for centralized keyset operations
- [ ] Implement keyset caching with expiration
- [ ] Add methods for keyset validation and comparison
- [ ] Implement keyset rotation detection

#### Task 1.3: Dynamic Denomination System

- [ ] Remove hardcoded denomination lists
- [ ] Implement denomination fetching from keyset keys
- [ ] Create denomination optimization algorithm that respects available denominations
- [ ] Add denomination validation against keyset

### Phase 2: Multi-Currency Support

#### Task 2.1: Currency-Aware Operations

- [ ] Update `_calculate_optimal_denominations` to use keyset-specific denominations
- [ ] Modify proof selection to be currency-aware
- [ ] Implement currency conversion detection and prevention
- [ ] Add currency validation to all operations

#### Task 2.2: Balance Management

- [ ] Implement `get_balance_by_currency()` method
- [ ] Update `fetch_wallet_state()` to calculate per-currency balances
- [ ] Add `get_balance_by_keyset()` for fine-grained balance tracking
- [ ] Update CLI balance display to show breakdown by currency

#### Task 2.3: Keyset Selection Logic

- [ ] Implement intelligent keyset selection based on:
  - Available balance
  - Fees
  - Currency requirements
  - Active status
- [ ] Add fallback logic for inactive keysets
- [ ] Implement keyset preference system

### Phase 3: Keyset Operations

#### Task 3.1: Universal Keyset Swap Function

```python
async def swap_keyset(
    self,
    proofs: list[ProofDict],
    target_keyset_id: str,
    *,
    amount: int | None = None,
) -> list[ProofDict]:
    """Universal function to swap proofs to a different keyset."""
```

- [ ] Implement cross-currency swaps (via Lightning)
- [ ] Implement same-currency keyset swaps (direct swap)
- [ ] Handle fees for both swap types
- [ ] Add atomic swap guarantees

#### Task 3.2: Granular Operation Control

- [ ] Add `keyset_id` parameter to:
  - `mint()` - mint to specific keyset
  - `melt()` - melt from specific keyset
  - `send()` - send from specific keyset
  - `swap()` - swap within specific keyset
- [ ] Implement parameter validation
- [ ] Add keyset availability checks

#### Task 3.3: Cross-Mint Currency Exchange

- [ ] Implement `exchange_currency()` method
- [ ] Support for cross-mint transfers with currency conversion
- [ ] Calculate exchange rates based on Lightning quotes
- [ ] Implement fee optimization for exchanges

### Phase 4: API Updates

#### Task 4.1: Mint API Integration

- [ ] Update `get_keysets()` to fetch all keysets (not just active)
- [ ] Implement keyset filtering by currency and active status
- [ ] Add keyset metadata caching
- [ ] Implement keyset update notifications

#### Task 4.2: Token Format Updates

- [ ] Ensure V4 tokens properly encode currency information
- [ ] Update token parsing to extract currency data
- [ ] Implement backward compatibility for older tokens
- [ ] Add currency validation on token redemption

### Phase 5: CLI Enhancements

#### Task 5.1: Balance Display

```
$ cashu balance
Total Balance: 1500 sat, 10.50 usd, 9.20 eur

By Mint:
├── https://mint1.example.com
│   ├── sat: 1000 (keyset: 00a1b2...)
│   └── usd: 10.50 (keyset: 00c3d4...)
└── https://mint2.example.com
    ├── sat: 500 (keyset: 00e5f6...)
    └── eur: 9.20 (keyset: 00g7h8...)
```

#### Task 5.2: Currency Operations

- [ ] Add `--unit` flag to all relevant commands
- [ ] Implement `cashu exchange` command for currency swaps
- [ ] Add `cashu keysets` command to list available keysets
- [ ] Update help text with currency examples

#### Task 5.3: Keyset Management Commands

- [ ] `cashu keysets list` - show all keysets
- [ ] `cashu keysets info <id>` - detailed keyset information
- [ ] `cashu keysets rotate` - handle keyset rotation
- [ ] `cashu keysets set-default <id>` - set default keyset

### Phase 6: Testing & Migration

#### Task 6.1: Test Suite Updates

- [ ] Add multi-currency unit tests
- [ ] Add keyset swap integration tests
- [ ] Test cross-mint currency exchanges
- [ ] Add denomination edge case tests

#### Task 6.2: Migration Strategy

- [ ] Create migration script for existing wallets
- [ ] Handle legacy single-currency proofs
- [ ] Implement gradual migration path
- [ ] Add rollback capability

#### Task 6.3: Documentation

- [ ] Update API documentation
- [ ] Create currency exchange examples
- [ ] Document keyset management
- [ ] Add troubleshooting guide

## Implementation Order

1. **Week 1-2**: Phase 1 - Foundation
   - Update data models
   - Implement keyset management
   - Dynamic denomination system

2. **Week 3-4**: Phase 2 - Multi-Currency Support
   - Currency-aware operations
   - Balance management
   - Keyset selection logic

3. **Week 5-6**: Phase 3 - Keyset Operations
   - Universal swap function
   - Granular operation control
   - Cross-mint exchanges

4. **Week 7**: Phase 4 - API Updates
   - Mint API integration
   - Token format updates

5. **Week 8**: Phase 5 - CLI Enhancements
   - Balance display
   - Currency operations
   - Keyset management commands

6. **Week 9-10**: Phase 6 - Testing & Migration
   - Test suite updates
   - Migration strategy
   - Documentation

## Technical Considerations

### Performance

- Keyset information should be cached to avoid frequent API calls
- Balance calculations should be optimized for wallets with many proofs
- Denomination selection algorithm must be efficient

### Security

- Keyset validation must be thorough to prevent attacks
- Currency swap operations must be atomic
- Private keys must never be exposed during swaps

### Compatibility

- Maintain backward compatibility with existing tokens
- Support gradual adoption of new features
- Ensure compatibility with other Cashu implementations

### Error Handling

- Clear error messages for currency mismatches
- Graceful handling of unavailable keysets
- Rollback mechanisms for failed swaps

## Future Enhancements

1. **Automatic Currency Arbitrage**: Detect and execute profitable currency swaps
2. **Keyset Reputation System**: Track keyset reliability and performance
3. **Multi-Signature Keysets**: Support for keysets requiring multiple signatures
4. **Currency Hedging**: Automatic balancing across currencies
5. **Keyset Discovery**: Automatic discovery of new keysets from mints

## Conclusion

This refactor will transform the sixty_nuts wallet from a single-currency system to a fully-featured multi-currency wallet with proper keyset management. The phased approach ensures that each component is properly tested before moving to the next phase, minimizing risk and ensuring a smooth transition for users.
