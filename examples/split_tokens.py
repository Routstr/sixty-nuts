#!/usr/bin/env python3
"""Example: Split tokens into specific amounts.

Shows how to split your tokens into specific denominations or prepare exact amounts.
Useful for making specific payments or optimizing token denominations.
"""

import asyncio
import sys
from sixty_nuts.wallet import Wallet


async def split_tokens(wallet: Wallet, target_amounts: list[int]):
    """Split tokens to create specific amounts."""
    total_needed = sum(target_amounts)

    print(f"🔪 Splitting tokens to create {len(target_amounts)} specific amounts")
    print(f"Target amounts: {target_amounts}")
    print(f"Total needed: {total_needed} sats")

    # Check if we have enough balance
    balance = await wallet.get_balance(check_proofs=True)
    if balance < total_needed:
        print(f"❌ Insufficient balance! Need {total_needed}, have {balance}")
        return

    print(f"✅ Sufficient balance: {balance} sats")

    # Get current wallet state
    state = await wallet.fetch_wallet_state(check_proofs=False)
    print(f"💳 Current balance: {state.balance} sats")

    # For each target amount, try to create exact tokens
    created_tokens = []

    for amount in target_amounts:
        print(f"\n📦 Creating token for {amount} sats...")

        try:
            # Create token for this exact amount
            token = await wallet.send(amount)
            created_tokens.append((amount, token))
            print(f"✅ Created token: {token[:50]}...")

        except Exception as e:
            print(f"❌ Failed to create {amount} sat token: {e}")

    # Show results
    if created_tokens:
        print(f"\n🎉 Successfully created {len(created_tokens)} tokens:")
        for amount, token in created_tokens:
            print(f"   💰 {amount} sats: {token[:30]}...")

        # Show remaining balance
        final_balance = await wallet.get_balance()
        print(f"\n💳 Remaining balance: {final_balance} sats")
    else:
        print("\n💀 No tokens were created")


async def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python split_tokens.py <amount1> [amount2] [amount3] ...")
        print("\nExamples:")
        print("  python split_tokens.py 100        # Create one 100 sat token")
        print("  python split_tokens.py 50 25 10   # Create three tokens")
        print("  python split_tokens.py 1000 500   # Create two larger tokens")
        return

    # Parse amounts from command line
    try:
        amounts = [int(arg) for arg in sys.argv[1:]]
    except ValueError:
        print("❌ All arguments must be valid numbers")
        return

    if any(amount <= 0 for amount in amounts):
        print("❌ All amounts must be positive")
        return

    # Initialize wallet
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        await split_tokens(wallet, amounts)


if __name__ == "__main__":
    asyncio.run(main())
