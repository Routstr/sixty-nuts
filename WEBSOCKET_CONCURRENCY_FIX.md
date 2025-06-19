# WebSocket Concurrency Fix

## Summary

Fixed the WebSocket concurrency error that occurred when multiple coroutines attempted to use the Wallet instance concurrently. The error `"cannot call recv while another coroutine is already running recv"` is now prevented through internal locking mechanisms.

## Problem

Previously, when multiple async operations were executed simultaneously on a Wallet instance (e.g., calling `send_to_lnurl()`, `redeem()`, or `fetch_wallet_state()` concurrently), the library would throw a WebSocket error because the underlying websocket connection cannot handle concurrent `recv()` operations.

## Solution

The fix implements two levels of protection:

### 1. NostrRelay Level (relay.py)
- Added `asyncio.Lock` instances for send, recv, and connect operations
- Each WebSocket operation is now protected by its respective lock:
  - `_send_lock`: Protects all send operations
  - `_recv_lock`: Protects all recv operations  
  - `_connect_lock`: Protects connection establishment

### 2. Wallet Level (wallet.py)
- Added `_relay_operation_lock` to serialize all relay operations at the wallet level
- This ensures that operations like `_publish_to_relays()`, `fetch_wallet_state()`, and `_discover_relays()` don't overlap

## Code Changes

### sixty_nuts/relay.py
```python
class NostrRelay:
    def __init__(self, url: str) -> None:
        # ... existing code ...
        # Add locks for concurrent access protection
        self._send_lock = asyncio.Lock()
        self._recv_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
```

### sixty_nuts/wallet.py
```python
class Wallet:
    def __init__(self, ...):
        # ... existing code ...
        # Add lock for serializing relay operations
        self._relay_operation_lock = asyncio.Lock()
```

## Usage

Users can now safely use concurrent operations without implementing their own locking:

```python
# This now works without errors!
await asyncio.gather(
    wallet.send_to_lnurl("address1", 100),
    wallet.send_to_lnurl("address2", 200),
    wallet.fetch_wallet_state()
)
```

## Benefits

1. **Transparent to users**: No API changes required
2. **Safe concurrency**: Multiple async operations can be called simultaneously
3. **Better performance**: Operations are serialized only when necessary
4. **No breaking changes**: Existing code continues to work

## Testing

Run the example script to verify the fix:

```bash
python examples/test_concurrent_wallet.py
```

This demonstrates that concurrent operations now work correctly without WebSocket recv errors.