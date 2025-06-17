# Sixty Nuts - Refactor Proposal

## Executive Summary

The current codebase suffers from a **monolithic design** with the main `wallet.py` file containing 2,275 lines and mixing multiple responsibilities. This proposal outlines a comprehensive refactor to improve code organization, reduce merge conflicts, enhance testability, and maintain the existing functionality while making the codebase more maintainable.

## Current Problems

### 1. **Monolithic Wallet Module (2,275 lines)**
- Single file handling cryptography, networking, state management, and business logic
- Difficult to navigate and understand
- High risk of merge conflicts in team development
- Poor separation of concerns

### 2. **Mixed Responsibilities**
- Wallet class handles 6+ distinct concerns:
  - Cryptographic operations
  - Nostr event management  
  - Relay communications
  - Mint API interactions
  - State management
  - Token operations

### 3. **Code Duplication**
- Similar patterns repeated across different operations
- Token parsing/serialization logic scattered
- Error handling patterns not standardized

### 4. **Testing Challenges**
- Large monolithic class difficult to unit test
- Heavy coupling makes mocking complicated
- Integration tests hard to isolate

### 5. **Poor Extensibility**
- Adding new features requires modifying core wallet
- Hard to add new mint protocols or relay types
- Difficult to implement custom behaviors

## Proposed Refactor Strategy

### Phase 1: Modular Decomposition

#### A. Extract Service Layer Classes

**1. `services/cryptography_service.py`**
```python
class CryptographyService:
    """Handles all cryptographic operations."""
    
    # From wallet.py:
    - _create_blinded_message()
    - _get_mint_pubkey_for_amount()
    - _decode_nsec()
    - _generate_privkey()
    - _get_pubkey()
    - _get_pubkey_compressed()
    - _sign_event()
    - _compute_event_id()
    - _nip44_encrypt()
    - _nip44_decrypt()
```

**2. `services/event_service.py`**
```python
class EventService:
    """Manages Nostr event operations."""
    
    # From wallet.py:
    - _create_event()
    - _publish_to_relays()
    - _estimate_event_size()
    - create_wallet_event()
    - publish_token_event()
    - _split_large_token_events()
    - delete_token_event()
    - publish_spending_history()
```

**3. `services/relay_service.py`**
```python
class RelayService:
    """Handles relay connections and operations."""
    
    # From wallet.py:
    - _get_relay_connections()
    - _discover_relays()
    - _rate_limit_relay_operations()
    
    # Enhanced features:
    - Connection pooling
    - Health monitoring
    - Load balancing
```

**4. `services/state_service.py`**
```python
class StateService:
    """Manages wallet state and proof validation."""
    
    # From wallet.py:
    - fetch_wallet_state()
    - _compute_proof_y_values()
    - _is_proof_state_cached()
    - _cache_proof_state()
    - clear_spent_proof_cache()
    - _validate_proofs_with_cache()
```

**5. `services/token_service.py`**
```python
class TokenService:
    """Handles token operations and parsing."""
    
    # From wallet.py:
    - _serialize_proofs_for_token()
    - _parse_cashu_token()
    - _proofdict_to_mint_proof()
    - _filter_proofs_by_keyset()
```

#### B. Create Business Logic Layer

**1. `operations/mint_operations.py`**
```python
class MintOperations:
    """High-level minting operations."""
    
    def __init__(self, mint_service, crypto_service, event_service):
        self.mint_service = mint_service
        self.crypto_service = crypto_service
        self.event_service = event_service
    
    async def mint_tokens(self, amount: int) -> str:
        # Orchestrates minting flow
    
    async def create_quote(self, amount: int) -> tuple[str, str]:
        # Creates and manages quotes
```

**2. `operations/send_operations.py`**
```python
class SendOperations:
    """High-level sending operations."""
    
    async def send_tokens(self, amount: int) -> str:
        # Orchestrates token sending
    
    async def send_to_lnurl(self, lnurl: str, amount: int) -> int:
        # Handles LNURL payments
```

**3. `operations/redeem_operations.py`**
```python
class RedeemOperations:
    """High-level redemption operations."""
    
    async def redeem_token(self, token: str) -> tuple[int, str]:
        # Orchestrates token redemption
    
    async def swap_mints(self, token: str, target_mint: str) -> tuple[int, str]:
        # Handles cross-mint swapping
```

#### C. Refactored Core Wallet

**`wallet/core_wallet.py`**
```python
class Wallet:
    """Lightweight wallet orchestrator."""
    
    def __init__(self, nsec: str, **kwargs):
        # Initialize services
        self.crypto_service = CryptographyService(nsec)
        self.relay_service = RelayService(kwargs.get('relays'))
        self.event_service = EventService(self.crypto_service, self.relay_service)
        self.state_service = StateService()
        self.token_service = TokenService()
        
        # Initialize operations
        self.mint_ops = MintOperations(...)
        self.send_ops = SendOperations(...)
        self.redeem_ops = RedeemOperations(...)
    
    # Delegate to operations
    async def mint_async(self, amount: int) -> tuple[str, asyncio.Task[bool]]:
        return await self.mint_ops.mint_tokens(amount)
    
    async def send(self, amount: int) -> str:
        return await self.send_ops.send_tokens(amount)
    
    async def redeem(self, token: str) -> tuple[int, str]:
        return await self.redeem_ops.redeem_token(token)
```

### Phase 2: Enhanced Architecture

#### A. Plugin System

**`protocols/base_protocol.py`**
```python
class BaseProtocol(ABC):
    """Base class for different protocols."""
    
    @abstractmethod
    async def create_quote(self, amount: int) -> dict[str, object]:
        pass
    
    @abstractmethod
    async def pay_invoice(self, invoice: str) -> bool:
        pass
```

**`protocols/cashu_protocol.py`**
```python
class CashuProtocol(BaseProtocol):
    """Cashu-specific protocol implementation."""
    
    # Current mint.py logic
```

**`protocols/lightning_protocol.py`**
```python
class LightningProtocol(BaseProtocol):
    """Direct Lightning protocol implementation."""
    
    # Future: direct LN operations
```

#### B. Event Store Pattern

**`storage/event_store.py`**
```python
class EventStore:
    """Handles event persistence and retrieval."""
    
    async def store_wallet_event(self, event: WalletEvent) -> str:
        # Store with optimistic locking
    
    async def get_wallet_state(self, pubkey: str) -> WalletState:
        # Rebuild state from events
    
    async def create_snapshot(self, state: WalletState) -> str:
        # Create state snapshots for performance
```

#### C. Configuration Management

**`config/wallet_config.py`**
```python
@dataclass
class WalletConfig:
    """Centralized configuration."""
    
    mint_urls: list[str]
    relay_urls: list[str]
    currency: str = "sat"
    cache_ttl: int = 300
    max_event_size: int = 60000
    rate_limit_interval: float = 1.0
    
    @classmethod
    def from_file(cls, path: str) -> "WalletConfig":
        # Load from config file
    
    @classmethod
    def from_env(cls) -> "WalletConfig":
        # Load from environment variables
```

### Phase 3: New Package Structure

```
sixty_nuts/
├── __init__.py
├── config/
│   ├── __init__.py
│   └── wallet_config.py
├── wallet/
│   ├── __init__.py
│   ├── core_wallet.py
│   └── temp_wallet.py
├── services/
│   ├── __init__.py
│   ├── cryptography_service.py
│   ├── event_service.py
│   ├── relay_service.py
│   ├── state_service.py
│   └── token_service.py
├── operations/
│   ├── __init__.py
│   ├── mint_operations.py
│   ├── send_operations.py
│   └── redeem_operations.py
├── protocols/
│   ├── __init__.py
│   ├── base_protocol.py
│   ├── cashu_protocol.py
│   └── lightning_protocol.py
├── storage/
│   ├── __init__.py
│   └── event_store.py
├── types/
│   ├── __init__.py
│   ├── events.py
│   ├── proofs.py
│   └── wallet_state.py
├── utils/
│   ├── __init__.py
│   ├── token_parser.py
│   └── validators.py
├── mint.py          # Keep existing for now
├── relay.py         # Keep existing for now
├── crypto.py        # Keep existing for now
└── lnurl.py         # Keep existing for now
```

## Implementation Benefits

### 1. **Reduced Lines of Code**
- **Current**: 2,275 lines in wallet.py
- **Proposed**: ~200-300 lines in core_wallet.py + distributed services
- **Reduction**: ~75% smaller core files

### 2. **Improved Testability**
```python
# Before: Hard to test due to coupling
def test_wallet_mint():
    wallet = Wallet(...)  # Needs full setup
    # Test requires real networking

# After: Easy to test with mocks
def test_mint_operations():
    mock_mint_service = Mock()
    mock_crypto_service = Mock()
    ops = MintOperations(mock_mint_service, mock_crypto_service)
    # Pure unit test
```

### 3. **Reduced Merge Conflicts**
- Multiple developers can work on different services simultaneously
- Clear boundaries reduce overlapping changes
- Smaller files easier to merge

### 4. **Enhanced Maintainability**
- Single responsibility principle enforced
- Clear separation of concerns
- Easier to locate and fix bugs

### 5. **Better Extensibility**
```python
# Adding new protocol is simple
class FediProtocol(BaseProtocol):
    # Implement Fedimint support
    pass

# Register with wallet
wallet.add_protocol("fedimint", FediProtocol())
```

## Migration Strategy

### Phase 1: Extract Services (Week 1-2)
1. Create service interfaces
2. Move methods from wallet.py to services
3. Update wallet.py to delegate to services
4. Run full test suite to ensure compatibility

### Phase 2: Create Operations Layer (Week 3)
1. Extract business logic into operations classes
2. Update wallet to use operations
3. Add comprehensive unit tests for operations

### Phase 3: Implement Storage Layer (Week 4)
1. Create event store abstraction
2. Implement caching and optimization
3. Add state snapshots for performance

### Phase 4: Plugin System (Week 5)
1. Create protocol abstractions
2. Implement Cashu protocol as plugin
3. Design extension points for future protocols

## Backward Compatibility

### Public API Preservation
```python
# Current API remains unchanged
wallet = Wallet(nsec="...")
balance = await wallet.get_balance()
token = await wallet.send(100)
```

### Migration Path
1. Keep existing wallet.py as legacy wrapper
2. Gradually migrate internals to new architecture
3. Deprecate old patterns with warnings
4. Remove legacy code in major version bump

## Performance Improvements

### 1. **Concurrent Operations**
```python
# Enable parallel operations
async def process_multiple_tokens(tokens: list[str]):
    tasks = [redeem_ops.redeem_token(token) for token in tokens]
    return await asyncio.gather(*tasks)
```

### 2. **Intelligent Caching**
```python
class StateService:
    async def get_cached_state(self, pubkey: str) -> WalletState | None:
        # Multi-level caching: memory -> disk -> relay
        return await self.cache.get_or_fetch(pubkey, self.fetch_from_relay)
```

### 3. **Connection Pooling**
```python
class RelayService:
    def __init__(self):
        self.connection_pool = ConnectionPool(max_size=10)
        # Reuse connections across operations
```

## Error Handling Improvements

### 1. **Structured Error Hierarchy**
```python
class WalletError(Exception):
    """Base wallet error."""

class NetworkError(WalletError):
    """Network-related errors."""

class CryptographyError(WalletError):
    """Cryptography-related errors."""

class ProtocolError(WalletError):
    """Protocol-specific errors."""
```

### 2. **Retry Logic**
```python
class RetryableOperation:
    @retry(max_attempts=3, backoff=ExponentialBackoff())
    async def execute(self) -> Any:
        # Automatic retry with exponential backoff
```

### 3. **Circuit Breaker Pattern**
```python
class MintService:
    def __init__(self):
        self.circuit_breaker = CircuitBreaker(failure_threshold=5)
    
    async def call_mint(self, mint_url: str) -> Any:
        return await self.circuit_breaker.call(self._make_request, mint_url)
```

## Configuration Examples

### 1. **Environment-based Config**
```python
# .env file
WALLET_MINT_URLS=https://mint1.example.com,https://mint2.example.com
WALLET_RELAY_URLS=wss://relay1.example.com,wss://relay2.example.com
WALLET_CURRENCY=sat
WALLET_CACHE_TTL=600
```

### 2. **File-based Config**
```yaml
# wallet_config.yaml
mint_urls:
  - "https://mint.minibits.cash/Bitcoin"
  - "https://testnut.cashu.space"
relay_urls:
  - "wss://relay.damus.io"
  - "wss://nos.lol"
currency: "sat"
cache_ttl: 300
rate_limiting:
  interval: 1.0
  burst: 5
```

## Quality Metrics

### Before Refactor:
- **Lines per file**: 2,275 (wallet.py)
- **Cyclomatic complexity**: High (many nested conditions)
- **Test coverage**: ~70% (hard to test monolithic code)
- **Merge conflicts**: High (single large file)

### After Refactor:
- **Lines per file**: <300 per service
- **Cyclomatic complexity**: Low (single responsibility)
- **Test coverage**: >90% (easy to unit test)
- **Merge conflicts**: Low (distributed across files)

## Conclusion

This refactor proposal transforms the sixty-nuts codebase from a monolithic structure into a modern, maintainable, and extensible architecture. The benefits include:

- **75% reduction** in core file size
- **Improved testability** with clear service boundaries
- **Enhanced maintainability** through separation of concerns
- **Better extensibility** via plugin architecture
- **Reduced merge conflicts** through distributed codebase

The migration can be implemented incrementally with full backward compatibility, ensuring a smooth transition while laying the foundation for future growth and improvements.