#!/usr/bin/env python3
"""Example: Redeem a Cashu token into your wallet.

Simple example showing how to redeem a Cashu token and add it to your wallet balance.
"""

import asyncio
import sys
from sixty_nuts.types import WalletError
from sixty_nuts.wallet import Wallet


async def redeem_token(wallet: Wallet, token: str):
    """Redeem a Cashu token into the wallet."""
    # Check balance before
    print("Checking current balance...")
    try:
        balance_before = await wallet.get_balance()
        print(f"Current balance: {balance_before} sats\n")
    except Exception:
        balance_before = 0
        print("Current balance: Unknown\n")

    # Redeem the token
    print("Redeeming token...")
    try:
        amount, unit = await wallet.redeem(token)
        print(f"✅ Successfully redeemed {amount} {unit}!")

        # Check balance after
        await asyncio.sleep(0.5)  # Give relays time to update
        balance_after = await wallet.get_balance()
        print(f"\nNew balance: {balance_after} sats")
        print(f"Added: {balance_after - balance_before} sats")

    except WalletError as e:
        if "already spent" in str(e).lower():
            print("❌ Token has already been spent!")
        elif "invalid token" in str(e).lower():
            print("❌ Invalid token format!")
        else:
            print(f"❌ Failed to redeem: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")


async def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python redeem_token.py <cashu_token>")
        print("\nExample:")
        print("python redeem_token.py cashuAey...")
        print("\nYou can paste the full token string or read from a file:")
        print("python redeem_token.py $(cat token.txt)")
        return

    token = sys.argv[1].strip()

    # Basic validation
    if not token.startswith("cashu"):
        print("❌ Invalid token! Cashu tokens start with 'cashu'")
        return

    print("🎫 Cashu Token Redemption")
    print("=" * 50)

    # Initialize wallet
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
    ) as wallet:
        await redeem_token(wallet, token)


if __name__ == "__main__":
    asyncio.run(main())
