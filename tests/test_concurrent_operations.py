#!/usr/bin/env python3
"""Test concurrent wallet operations to ensure WebSocket concurrency is handled properly."""

import asyncio

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

from sixty_nuts import TempWallet
from sixty_nuts.relay import NostrRelay


async def test_concurrent_wallet_operations():
    """Test that concurrent wallet operations don't cause WebSocket recv errors."""
    # Create a temporary wallet
    wallet = await TempWallet.create(
        mint_urls=["https://testnut.cashu.space"],
        auto_init=False
    )
    
    try:
        # Define multiple concurrent operations
        async def op1():
            return await wallet.fetch_wallet_state(check_proofs=False)
        
        async def op2():
            return await wallet.fetch_wallet_state(check_proofs=False)
        
        async def op3():
            return await wallet.create_wallet_event()
        
        # Run operations concurrently - this should not raise WebSocket recv errors
        results = await asyncio.gather(op1(), op2(), op3(), return_exceptions=True)
        
        # Check that no WebSocket recv errors occurred
        for result in results:
            if isinstance(result, Exception):
                assert "cannot call recv while another coroutine" not in str(result)
        
    finally:
        await wallet.aclose()


async def test_relay_concurrent_recv():
    """Test that NostrRelay handles concurrent recv operations with locks."""
    relay = NostrRelay("wss://relay.damus.io")
    
    # Mock the websocket to test lock behavior
    class MockWebSocket:
        def __init__(self):
            self.close_code = None
            self.recv_count = 0
            self.concurrent_calls = 0
            self.max_concurrent = 0
            
        async def recv(self):
            self.recv_count += 1
            self.concurrent_calls += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent_calls)
            await asyncio.sleep(0.1)  # Simulate network delay
            self.concurrent_calls -= 1
            return '["OK", "test_id", true]'
        
        async def send(self, data):
            pass
    
    mock_ws = MockWebSocket()
    relay.ws = mock_ws
    
    # Try to call _recv concurrently
    async def recv_task():
        return await relay._recv()
    
    # Run multiple recv operations concurrently
    results = await asyncio.gather(
        recv_task(),
        recv_task(),
        recv_task(),
        return_exceptions=True
    )
    
    # Check that operations completed without errors
    for result in results:
        assert not isinstance(result, Exception)
        assert result == ["OK", "test_id", True]
    
    # Verify that the lock prevented actual concurrent recv calls
    assert mock_ws.recv_count == 3  # All 3 calls completed
    assert mock_ws.max_concurrent == 1  # But only 1 at a time due to lock


async def test_wallet_relay_operation_lock():
    """Test that wallet-level relay operations are properly serialized."""
    wallet = await TempWallet.create(
        mint_urls=["https://testnut.cashu.space"],
        auto_init=False
    )
    
    try:
        # Track concurrent relay operations
        operation_count = 0
        max_concurrent = 0
        
        original_publish = wallet._publish_to_relays
        
        async def tracked_publish(event):
            nonlocal operation_count, max_concurrent
            operation_count += 1
            current = operation_count
            max_concurrent = max(max_concurrent, current)
            try:
                # Simulate the operation taking time
                await asyncio.sleep(0.05)
                return event.get("id", "test_id")
            finally:
                operation_count -= 1
        
        # Monkey patch to track concurrent calls
        wallet._publish_to_relays = tracked_publish
        
        # Run multiple publish operations concurrently
        tasks = []
        for i in range(5):
            event = {
                "id": f"event_{i}",
                "kind": 1,
                "content": f"Test {i}",
                "tags": [],
                "created_at": 1234567890,
                "pubkey": "test_pubkey",
                "sig": "test_sig"
            }
            tasks.append(wallet.publish_token_event([]))
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify that operations were serialized (max 1 concurrent)
        assert max_concurrent == 1, f"Expected max 1 concurrent operation, got {max_concurrent}"
        
    finally:
        await wallet.aclose()


async def test_high_concurrency_stress_test():
    """Stress test with many concurrent operations to ensure robustness."""
    wallet = await TempWallet.create(
        mint_urls=["https://testnut.cashu.space"],
        auto_init=False
    )
    
    try:
        # Create many concurrent operations
        operations = []
        
        for i in range(20):
            if i % 3 == 0:
                # Mix different types of operations
                operations.append(wallet.create_wallet_event())
            else:
                operations.append(wallet.fetch_wallet_state(check_proofs=False))
        
        # Run all operations concurrently
        results = await asyncio.gather(*operations, return_exceptions=True)
        
        # Check for WebSocket concurrency errors
        websocket_errors = 0
        other_errors = 0
        
        for result in results:
            if isinstance(result, Exception):
                error_msg = str(result)
                if "cannot call recv while another coroutine" in error_msg:
                    websocket_errors += 1
                else:
                    other_errors += 1
        
        # Assert no WebSocket concurrency errors occurred
        assert websocket_errors == 0, f"Found {websocket_errors} WebSocket concurrency errors"
        
        # Log other errors for debugging (network issues, etc.)
        if other_errors > 0:
            print(f"Note: {other_errors} other errors occurred (likely network-related)")
            
    finally:
        await wallet.aclose()


# Add pytest decorators only if pytest is available
if HAS_PYTEST:
    test_concurrent_wallet_operations = pytest.mark.asyncio(test_concurrent_wallet_operations)
    test_relay_concurrent_recv = pytest.mark.asyncio(test_relay_concurrent_recv)
    test_wallet_relay_operation_lock = pytest.mark.asyncio(test_wallet_relay_operation_lock)
    test_high_concurrency_stress_test = pytest.mark.asyncio(test_high_concurrency_stress_test)


if __name__ == "__main__":
    # Run tests
    asyncio.run(test_concurrent_wallet_operations())
    asyncio.run(test_relay_concurrent_recv())
    asyncio.run(test_wallet_relay_operation_lock())
    asyncio.run(test_high_concurrency_stress_test())
    print("All concurrency tests passed!")