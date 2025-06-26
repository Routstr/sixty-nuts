#!/usr/bin/env python3
"""Example: One-off token redemption.

Shows how to redeem tokens without storing them in a persistent wallet.
Useful for forwarding tokens directly to Lightning addresses or temporary redemption.
"""

import asyncio
import sys
from sixty_nuts.wallet import Wallet
from sixty_nuts.temp import redeem_to_lnurl


async def redeem_and_forward(tokens: list[str], lightning_address: str):
    """Redeem multiple tokens and forward total to Lightning address."""
    print(f"🔄 Redeeming {len(tokens)} token(s) and forwarding to {lightning_address}")

    total_sent = 0

    for i, token in enumerate(tokens, 1):
        print(f"\n📦 Processing token {i}/{len(tokens)}...")

        try:
            # Redeem token directly to Lightning address (no wallet storage)
            amount_sent = await redeem_to_lnurl(token, lightning_address)
            total_sent += amount_sent

            print(f"✅ Sent {amount_sent} sats from token {i}")

        except Exception as e:
            print(f"❌ Failed to redeem token {i}: {e}")

    if total_sent > 0:
        print(f"\n🎉 Total forwarded: {total_sent} sats to {lightning_address}")
    else:
        print("\n💀 No tokens were successfully redeemed")


async def redeem_to_wallet(token: str):
    """Redeem a single token to a temporary wallet."""
    print("💰 Redeeming token to temporary wallet...")

    # Create temporary wallet for this redemption
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as temp_wallet:
        try:
            # Parse token to show details
            mint_url, unit, proofs = temp_wallet._parse_cashu_token(token)
            total_value = sum(p["amount"] for p in proofs)

            print(f"📋 Token Details:")
            print(f"   Value: {total_value} {unit}")
            print(f"   Mint: {mint_url}")
            print(f"   Proofs: {len(proofs)}")

            # Redeem the token
            amount, received_unit = await temp_wallet.redeem(token)

            print(f"✅ Successfully redeemed {amount} {received_unit}!")

            # Note: Wallet will be discarded when context exits
            print("💡 Note: This was a temporary wallet - tokens are now in the ether")

            return amount

        except Exception as e:
            print(f"❌ Failed to redeem token: {e}")
            return 0


async def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  # Redeem single token to temporary wallet")
        print("  python one_off_redeem.py <cashu_token>")
        print("")
        print("  # Redeem and forward to Lightning address")
        print("  python one_off_redeem.py <lightning_address> <token1> [token2] ...")
        print("")
        print("Examples:")
        print("  python one_off_redeem.py cashuAey...")
        print("  python one_off_redeem.py user@getalby.com cashuAey... cashuBdef...")
        return

    args = sys.argv[1:]

    # Check if first argument is a Lightning address or token
    if args[0].startswith("cashu"):
        # Single token redemption to temporary wallet
        token = args[0]
        await redeem_to_wallet(token)

    elif "@" in args[0] or args[0].startswith("lightning:"):
        # Forward to Lightning address
        if len(args) < 2:
            print("❌ Need at least one token to redeem and forward")
            return

        lightning_address = args[0]
        tokens = args[1:]

        # Validate tokens
        invalid_tokens = [t for t in tokens if not t.startswith("cashu")]
        if invalid_tokens:
            print(f"❌ Invalid tokens found: {invalid_tokens[:3]}...")
            return

        await redeem_and_forward(tokens, lightning_address)

    else:
        print("❌ First argument must be either a Cashu token or Lightning address")
        print("   Lightning addresses should contain '@' or start with 'lightning:'")


if __name__ == "__main__":
    asyncio.run(main())
