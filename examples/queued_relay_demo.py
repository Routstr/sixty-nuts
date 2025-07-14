#!/usr/bin/env python3
"""Example: Queued relay operations.

Shows how the wallet uses queued relays for better performance and reliability.
Queued relays batch operations and handle failures gracefully.
"""

import asyncio
from sixty_nuts.wallet import Wallet


async def demonstrate_queued_operations(wallet: Wallet):
    """Show how queued relays handle multiple operations efficiently."""
    print("📡 Demonstrating queued relay operations...")

    # Check if queued relays are enabled
    if wallet.relay_manager.use_queued_relays:
        print("✅ Queued relays are enabled")

        # Show relay pool status
        if wallet.relay_manager.relay_pool:
            pool_size = len(wallet.relay_manager.relay_pool.relays)
            print(f"📊 Relay pool size: {pool_size} relays")

    else:
        print("❌ Queued relays are disabled")
        return

    # Get current balance (this uses queued operations)
    print("\n💰 Getting balance (uses queued relay operations)...")
    balance = await wallet.get_balance()
    print(f"Balance: {balance} sats")

    # Create a small token (this will queue operations for publishing)
    if balance >= 10:
        print("\n📦 Creating token (operations will be queued)...")
        try:
            token = await wallet.send(5)
            print(f"✅ Token created: {token[:50]}...")

            # Show pending operations if any
            if wallet.relay_manager.relay_pool:
                pending = wallet.relay_manager.relay_pool.get_pending_proofs()
                if pending:
                    print(f"📤 Pending proofs in queue: {len(pending)}")

        except Exception as e:
            print(f"❌ Failed to create token: {e}")

    else:
        print("\n💡 Not enough balance to demonstrate token creation")

    # Show final status
    print("\n📈 Queued relay operations completed")


async def show_relay_status(wallet: Wallet):
    """Show current relay configuration and status."""
    print("\n🌐 Relay Configuration:")
    print(f"   Configured relays: {len(wallet.relay_urls)}")
    for i, relay in enumerate(wallet.relay_urls, 1):
        print(f"   {i}. {relay}")

    print(
        f"   Queued relays: {'✅ Enabled' if wallet.relay_manager.use_queued_relays else '❌ Disabled'}"
    )

    try:
        connections = await wallet.relay_manager.get_relay_connections()
        print(f"   Active connections: {len(connections)}")
    except Exception as e:
        print(f"   Connection status: ❌ Error ({e})")


async def main():
    """Main function."""
    print("📡 Queued Relay Demo")
    print("=" * 30)

    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        # Show relay configuration
        await show_relay_status(wallet)

        # Demonstrate queued operations
        await demonstrate_queued_operations(wallet)

        print("\n💡 Key benefits of queued relays:")
        print("   • Batched operations for better performance")
        print("   • Automatic retries on failures")
        print("   • Reduced relay load")
        print("   • Better handling of slow/unreliable relays")


if __name__ == "__main__":
    asyncio.run(main())
