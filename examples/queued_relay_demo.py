#!/usr/bin/env python3
"""Demonstration of queued relay functionality in sixty_nuts wallet.

This example shows how the wallet can queue events for publishing while
immediately including pending proofs in balance calculations.
"""

import asyncio
import time
from sixty_nuts import Wallet


async def main():
    # Create wallet with queued relay support (enabled by default)
    wallet = Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",  # Replace with actual nsec
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        relays=["wss://relay.damus.io", "wss://relay.nostr.band"],
    )

    async with wallet:
        print("=== Queued Relay Demo ===\n")

        # Check initial balance
        initial_balance = await wallet.get_balance()
        print(f"Initial balance: {initial_balance} sats")

        # For demonstration, we need some funds to create a token
        if initial_balance < 10:
            print("\n⚠️  Need at least 10 sats for demo. Please fund the wallet first.")
            print("You can use 'mint_and_send.py' to add funds.")
            return

        # Create a token from our balance for demonstration
        demo_amount = min(
            10, initial_balance // 2
        )  # Use half balance or 10 sats, whichever is smaller
        print(f"\nCreating {demo_amount} sat token for demo...")
        token = await wallet.send(demo_amount)
        print("Token created!")

        # Wait a moment for the send operation to complete
        await asyncio.sleep(1)

        # Check balance after creating token
        balance_after_send = await wallet.get_balance()
        print(f"Balance after creating token: {balance_after_send} sats")

        print(f"\nRedeeming {demo_amount} sat token...")
        start_time = time.time()

        # This will queue events instead of blocking
        amount, unit = await wallet.redeem(token)

        redeem_time = time.time() - start_time
        print(f"Redeem completed in {redeem_time:.2f}s (non-blocking)")

        # Check balance immediately - includes pending proofs!
        immediate_balance = await wallet.get_balance()
        print(f"Balance (with pending): {immediate_balance} sats")

        # The events are being published in the background
        print("\nEvents are being published in background...")

        # Wait a bit for publishing to complete
        await asyncio.sleep(2)

        # Check balance again after publishing
        final_balance = await wallet.get_balance()
        print(f"Balance (after publish): {final_balance} sats")

        # Demonstrate queue status
        if wallet._use_queued_relays and wallet.relay_pool:
            queue_size = wallet.relay_pool.shared_queue.size
            print(f"\nQueue status: {queue_size} events pending")

        print("\nDemo complete!")
        print(
            f"Final balance should be close to initial: {final_balance} sats (started with {initial_balance})"
        )


if __name__ == "__main__":
    # Run the demo
    asyncio.run(main())
