#!/usr/bin/env python3
"""Example: Merchant accepting Cashu tokens.

Shows how to accept tokens from any mint and swap them to your trusted mint.
"""

import asyncio
import sys
from sixty_nuts.wallet import Wallet


async def accept_payment(token: str, trusted_mint: str):
    """Accept a Cashu token and swap it to trusted mint."""
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
        mint_urls=[trusted_mint],
    ) as wallet:
        # Parse token to show original amount
        source_mint, unit, proofs = wallet._parse_cashu_token(token)
        original_amount = sum(p["amount"] for p in proofs)

        print(f"Token from: {source_mint}")
        print(f"Amount: {original_amount} {unit}")

        # Redeem automatically swaps to trusted mint
        amount, unit = await wallet.redeem(token)

        fees = original_amount - amount
        print(f"\nReceived: {amount} {unit}")
        if fees > 0:
            print(f"Lightning fees: {fees} {unit}")

        return amount, unit


async def main():
    """Main example."""
    TRUSTED_MINT = "https://mint.minibits.cash/Bitcoin"

    if len(sys.argv) < 2:
        print("Usage: python merchant_accept_token.py <cashu_token>")
        return

    token = sys.argv[1]

    try:
        amount, unit = await accept_payment(token, TRUSTED_MINT)
        print("\n✅ Payment successful!")
    except Exception as e:
        print(f"\n❌ Payment failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
