#!/usr/bin/env python3
"""Example: Merchant accepting Cashu tokens.

Shows how a merchant can safely accept tokens from any mint
and optionally swap them to their preferred mint.
"""

import asyncio
import sys
from sixty_nuts.wallet import Wallet


async def accept_token(wallet: Wallet, token: str, auto_swap: bool = True):
    """Accept and validate a token from a customer."""
    print("🛒 Merchant Token Acceptance")
    print("=" * 40)

    try:
        # Parse token to check details
        mint_url, unit, proofs = wallet._parse_cashu_token(token)
        total_value = sum(p["amount"] for p in proofs)

        print("📋 Token Details:")
        print(f"   Value: {total_value} {unit}")
        print(f"   From Mint: {mint_url}")
        print(f"   Proofs: {len(proofs)}")

        # Check if mint is trusted
        is_trusted_mint = mint_url in wallet.mint_urls
        print(f"   Trusted Mint: {'✅ Yes' if is_trusted_mint else '❌ No'}")

        if not is_trusted_mint and auto_swap:
            print(f"\n🔄 Will auto-swap to trusted mint: {wallet._primary_mint_url()}")

        # Get balance before
        balance_before = await wallet.get_balance()

        # Redeem the token (auto_swap handles untrusted mints)
        print("\n💰 Redeeming token...")
        amount, received_unit = await wallet.redeem(token, auto_swap=auto_swap)

        # Get balance after
        await asyncio.sleep(0.5)  # Give relays time to update
        balance_after = await wallet.get_balance()

        print(f"✅ Successfully accepted {amount} {received_unit}!")
        print(f"💳 Wallet Balance: {balance_before} → {balance_after} sats")

        return True

    except Exception as e:
        print(f"❌ Failed to accept token: {e}")
        return False


async def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python merchant_accept_token.py <cashu_token>")
        print("\nExample:")
        print("  python merchant_accept_token.py cashuAey...")
        return

    token = sys.argv[1].strip()

    # Initialize merchant wallet with trusted mints
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
        mint_urls=["https://mint.minibits.cash/Bitcoin"],  # Merchant's trusted mint
    ) as wallet:
        success = await accept_token(wallet, token, auto_swap=True)

        if success:
            print("\n🎉 Payment accepted!")
        else:
            print("\n💀 Payment rejected!")


if __name__ == "__main__":
    asyncio.run(main())
