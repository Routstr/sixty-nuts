#!/usr/bin/env python3
"""Example: Monitor Lightning payments.

Shows how to create Lightning invoices and monitor for payments in real-time.
Useful for merchants or services accepting Lightning payments.
"""

import asyncio
from sixty_nuts.wallet import Wallet


async def create_and_monitor_invoice(wallet: Wallet, amount: int, timeout: int = 300):
    """Create a Lightning invoice and monitor for payment."""
    print(f"💡 Creating Lightning invoice for {amount} sats...")

    # Create invoice and get monitoring task
    invoice, payment_task = await wallet.mint_async(amount, timeout=timeout)

    print(f"\n⚡ Lightning Invoice:")
    print(invoice)
    print(f"\n⏰ Monitoring for payment (timeout: {timeout}s)...")
    print("💡 Pay the invoice above to complete the demonstration")

    # Monitor payment with progress updates
    start_time = asyncio.get_event_loop().time()

    while not payment_task.done():
        await asyncio.sleep(5)  # Check every 5 seconds
        elapsed = int(asyncio.get_event_loop().time() - start_time)
        remaining = timeout - elapsed

        if remaining > 0:
            print(f"⏳ Still waiting... ({remaining}s remaining)")
        else:
            break

    # Check result
    try:
        paid = await payment_task

        if paid:
            print("\n✅ Payment received!")

            # Show updated balance
            balance = await wallet.get_balance()
            print(f"💰 New wallet balance: {balance} sats")

            return True
        else:
            print(f"\n❌ Payment timeout after {timeout} seconds")
            return False

    except Exception as e:
        print(f"\n❌ Error monitoring payment: {e}")
        return False


async def main():
    """Main function."""
    print("📱 Lightning Payment Monitor")
    print("=" * 40)

    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        # Show current balance
        balance = await wallet.get_balance()
        print(f"Current balance: {balance} sats")

        # Create and monitor a small invoice
        amount = 100  # 100 sats
        timeout = 120  # 2 minutes

        success = await create_and_monitor_invoice(wallet, amount, timeout)

        if success:
            print("\n🎉 Payment monitoring completed successfully!")
        else:
            print("\n💀 Payment monitoring failed or timed out")


if __name__ == "__main__":
    asyncio.run(main())
