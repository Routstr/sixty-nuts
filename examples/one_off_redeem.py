#!/usr/bin/env python3
"""Example: One-off token redemption to Lightning Address.

Shows how to use TempWallet for ephemeral operations without storing keys.
Perfect for redeeming tokens directly to a Lightning Address.
"""

import asyncio
import sys
from sixty_nuts.types import WalletError
from sixty_nuts.temp import TempWallet, redeem_to_lnurl


async def redeem_with_temp_wallet(token: str, lightning_address: str):
    """Redeem a token to Lightning Address using temporary wallet."""
    print(f"Redeeming token to {lightning_address}...")

    # Create temporary wallet (generates random keys)
    async with TempWallet() as wallet:
        print("🔑 Created temporary wallet (keys not stored)")

        try:
            # Redeem the token
            amount, unit = await wallet.redeem(token)
            print(f"✅ Redeemed {amount} {unit}")

            # For very small amounts, warn but still try
            if amount <= 1:
                print(
                    f"\n⚠️  Warning: {amount} {unit} is very small - fees will consume it all"
                )
                return

            # Send to Lightning Address
            print(f"\nSending to {lightning_address}...")

            try:
                # First try sending the full amount
                paid = await wallet.send_to_lnurl(lightning_address, amount)
                print(f"⚡ Sent {paid} {unit} total")
            except WalletError as e:
                if "Insufficient balance" in str(e) and amount > 1:
                    # Automatically retry with 1 less to account for fees
                    print(
                        f"💡 Adjusting for fees, sending {amount - 1} {unit} instead..."
                    )
                    paid = await wallet.send_to_lnurl(lightning_address, amount - 1)
                    print(f"⚡ Sent {paid} {unit} total (after fees)")
                else:
                    raise

            # Check final balance (should be 0 or close to 0)
            balance = await wallet.get_balance()
            if balance > 0:
                print(f"\n💰 Dust remaining: {balance} {unit}")

        except WalletError as e:
            print(f"\n❌ Error: {e}")
        except Exception as e:
            print(f"\n❌ Error: {e}")


async def direct_redeem_example(token: str, lightning_address: str):
    """Use the convenience function for direct redemption."""
    print("\nUsing direct redemption helper...")

    try:
        amount = await redeem_to_lnurl(token, lightning_address)
        print(f"✅ Successfully sent {amount} sats to {lightning_address}")
    except WalletError as e:
        print(f"❌ Redemption failed: {e}")
        if "too small" in str(e):
            print("💡 Token value is too small (1 sat or less)")
    except Exception as e:
        print(f"❌ Redemption failed: {e}")


async def batch_redeem_example(tokens: list[str], lightning_address: str):
    """Redeem multiple tokens to same Lightning Address."""
    print(f"\nBatch redeeming {len(tokens)} tokens...")

    total_redeemed = 0
    unit = "sat"

    # Use temporary wallet for all tokens
    async with TempWallet() as wallet:
        for i, token in enumerate(tokens, 1):
            try:
                print(f"\nToken {i}/{len(tokens)}:")
                amount, unit = await wallet.redeem(token)
                print(f"  ✅ Redeemed {amount} {unit}")
                total_redeemed += amount
            except Exception as e:
                print(f"  ❌ Failed: {e}")

        if total_redeemed > 0:
            print(f"\nTotal collected: {total_redeemed} {unit}")

            # Check if we have enough for fees
            if total_redeemed <= 1:
                print(
                    f"⚠️  {total_redeemed} {unit} is too small - fees would consume it all"
                )
                return

            print(f"Sending to {lightning_address}...")

            try:
                # Try sending the full amount
                paid = await wallet.send_to_lnurl(lightning_address, total_redeemed)
                print(f"⚡ Sent {paid} {unit} total")
            except WalletError as e:
                if "Insufficient balance" in str(e) and total_redeemed > 1:
                    # Automatically adjust for fees
                    print(
                        f"💡 Adjusting for fees, sending {total_redeemed - 1} {unit} instead..."
                    )
                    paid = await wallet.send_to_lnurl(
                        lightning_address, total_redeemed - 1
                    )
                    print(f"⚡ Sent {paid} {unit} total (after fees)")
                else:
                    print(f"❌ Failed to send: {e}")


async def main():
    """Main example."""
    if len(sys.argv) < 3:
        print("Usage: python one_off_redeem.py <cashu_token> <lightning_address>")
        print("Example: python one_off_redeem.py cashuAey... user@getalby.com")
        print("\nFor batch redemption:")
        print(
            "Usage: python one_off_redeem.py <lightning_address> <token1> <token2> ..."
        )
        print("\n💡 Note: The script automatically handles Lightning fees")
        print("   For a 5 sat token, you'll receive ~4 sats after fees")
        return

    if sys.argv[1].startswith("cashu"):
        # Single token redemption
        token = sys.argv[1]
        lightning_address = sys.argv[2]

        # Method 1: Manual temp wallet
        await redeem_with_temp_wallet(token, lightning_address)

        # Method 2: Direct helper (commented out to avoid double-spending)
        # await direct_redeem_example(token, lightning_address)

    else:
        # Batch redemption (first arg is lightning address)
        lightning_address = sys.argv[1]
        tokens = sys.argv[2:]
        await batch_redeem_example(tokens, lightning_address)


if __name__ == "__main__":
    asyncio.run(main())
