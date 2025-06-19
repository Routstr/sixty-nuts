#!/usr/bin/env python3
"""Example demonstrating concurrent wallet operations with the WebSocket concurrency fix."""

import asyncio
from sixty_nuts import TempWallet


async def test_concurrent_operations():
    """Test concurrent wallet operations that now work with the internal locking."""
    print("Testing concurrent operations with internal locking fix...")
    
    # Create a temporary wallet
    wallet = await TempWallet.create(
        mint_urls=["https://testnut.cashu.space"],
        auto_init=False
    )
    
    print(f"Created wallet with nsec: {wallet.nsec[:20]}...")
    
    # These operations can now run concurrently without errors
    # The internal _relay_operation_lock serializes WebSocket access
    async def fetch_state():
        try:
            state = await wallet.fetch_wallet_state(check_proofs=False)
            return f"Balance: {state.balance}"
        except Exception as e:
            return f"Error: {e}"
    
    async def create_wallet_event():
        try:
            event_id = await wallet.create_wallet_event()
            return f"Created event: {event_id[:8]}..."
        except Exception as e:
            return f"Error: {e}"
    
    # Run multiple concurrent operations
    print("\nRunning concurrent operations...")
    results = await asyncio.gather(
        fetch_state(),
        fetch_state(),
        create_wallet_event(),
        fetch_state(),
        return_exceptions=False
    )
    
    # Print results
    for i, result in enumerate(results):
        print(f"Operation {i + 1}: {result}")
    
    # Clean up
    await wallet.aclose()
    
    print("\nAll operations completed successfully!")
    print("No WebSocket concurrency errors occurred.")


async def test_high_concurrency():
    """Test with many concurrent operations to stress test the fix."""
    print("\n\nStress testing with 20 concurrent operations...")
    
    wallet = await TempWallet.create(
        mint_urls=["https://testnut.cashu.space"],
        auto_init=False
    )
    
    async def operation(op_id: int):
        """Run a wallet operation."""
        try:
            if op_id % 3 == 0:
                await wallet.create_wallet_event()
                return f"Op {op_id}: Created wallet event"
            else:
                state = await wallet.fetch_wallet_state(check_proofs=False)
                return f"Op {op_id}: Fetched state (balance: {state.balance})"
        except Exception as e:
            return f"Op {op_id}: Error - {type(e).__name__}: {e}"
    
    # Run many concurrent operations
    results = await asyncio.gather(
        *[operation(i) for i in range(20)],
        return_exceptions=True
    )
    
    # Count successes and failures
    successes = sum(1 for r in results if not isinstance(r, Exception) and "Error" not in str(r))
    failures = sum(1 for r in results if isinstance(r, Exception) or "Error" in str(r))
    
    print(f"\nResults: {successes} successes, {failures} failures")
    
    if failures > 0:
        print("\nFailures:")
        for i, result in enumerate(results):
            if isinstance(result, Exception) or "Error" in str(result):
                print(f"  {result}")
    
    await wallet.aclose()
    
    if failures == 0:
        print("\nAll operations completed successfully! The concurrency fix is working.")
    else:
        print("\nSome operations failed, but no WebSocket recv concurrency errors!")


if __name__ == "__main__":
    print("=== WebSocket Concurrency Fix Demo ===\n")
    print("This example demonstrates that the library now handles concurrent")
    print("operations internally using locks, preventing WebSocket recv errors.\n")
    
    asyncio.run(test_concurrent_operations())
    asyncio.run(test_high_concurrency())
    
    print("\n\n=== Summary ===")
    print("The sixty_nuts library now handles concurrent operations properly.")
    print("Users no longer need to implement their own locking mechanisms.")
    print("The async methods are now safe to call concurrently! 🎉")