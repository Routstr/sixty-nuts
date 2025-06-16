#!/usr/bin/env python3
"""Example: Send Cashu tokens to a Lightning Address.

Shows how to send tokens to any Lightning Address (user@domain.com format).
This automatically handles the LNURL flow and Lightning payment.
"""

import asyncio
import sys
from sixty_nuts.wallet import Wallet


async def send_to_address(wallet: Wallet, address: str, amount: int):
    """Send tokens to a Lightning Address."""
    print(f"Sending {amount} sats to {address}...")

    try:
        # Send to Lightning Address (handles LNURL automatically)
        actual_paid = await wallet.send_to_lnurl(address, amount)

        print(f"✅ Successfully sent {actual_paid} sats!")
        print(f"   (After estimated Lightning fees)")

        # Show remaining balance
        balance = await wallet.get_balance()
        print(f"\nRemaining balance: {balance} sats")

    except Exception as e:
        print(f"❌ Failed to send: {e}")
        raise


async def main():
    """Main example."""
    if len(sys.argv) < 3:
        print("Usage: python send_to_lightning_address.py <lightning_address> <amount>")
        print("Example: python send_to_lightning_address.py user@getalby.com 100")
        return

    address = sys.argv[1]
    amount = int(sys.argv[2])

    # Initialize wallet
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
    ) as wallet:
        # Check balance first
        balance = await wallet.get_balance()
        print(f"Current balance: {balance} sats")

        if balance < amount:
            print(f"❌ Insufficient balance! Need {amount}, have {balance}")
            return

        await send_to_address(wallet, address, amount)


if __name__ == "__main__":
    asyncio.run(main())
