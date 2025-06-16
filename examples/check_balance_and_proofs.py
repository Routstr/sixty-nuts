#!/usr/bin/env python3
"""Example: Check wallet balance and validate proofs.

Shows how to check wallet balance, list proofs by mint, and validate their state.
Useful for debugging and understanding wallet state.
"""

import asyncio
from sixty_nuts.wallet import Wallet


async def check_wallet_status(wallet: Wallet):
    """Check and display detailed wallet status."""
    print("Fetching wallet state...\n")

    # Get wallet state with proof validation
    state = await wallet.fetch_wallet_state(check_proofs=True)

    print(f"💰 Total Balance: {state.balance} sats")
    print(f"📄 Total Proofs: {len(state.proofs)}")

    # Group proofs by mint
    proofs_by_mint: dict[str, list] = {}
    for proof in state.proofs:
        mint_url = proof.get("mint") or "unknown"
        if mint_url not in proofs_by_mint:
            proofs_by_mint[mint_url] = []
        proofs_by_mint[mint_url].append(proof)

    print(f"\n🏦 Mints in use: {len(proofs_by_mint)}")

    # Display breakdown by mint
    for mint_url, proofs in proofs_by_mint.items():
        mint_balance = sum(p["amount"] for p in proofs)
        print(f"\n  Mint: {mint_url}")
        print(f"  Balance: {mint_balance} sats")
        print(f"  Proofs: {len(proofs)}")

        # Show denomination breakdown
        denominations: dict[int, int] = {}
        for proof in proofs:
            amount = proof["amount"]
            denominations[amount] = denominations.get(amount, 0) + 1

        print("  Denominations:")
        for denom in sorted(denominations.keys()):
            count = denominations[denom]
            print(f"    {denom} sat: {count} proof(s)")

    # Check if any proofs are spent (shouldn't be any after validation)
    print(f"\n✅ All {len(state.proofs)} proofs are validated and unspent")


async def check_without_validation(wallet: Wallet):
    """Check wallet state without validating proofs (faster but less accurate)."""
    print("\n" + "=" * 50)
    print("Checking without proof validation (fast mode)...")

    # Get balance without checking proofs
    balance = await wallet.get_balance(check_proofs=False)
    print(f"\n💰 Reported Balance (unvalidated): {balance} sats")
    print("⚠️  Note: This may include spent proofs!")


async def main():
    """Main example."""
    # Initialize wallet
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
    ) as wallet:
        # Check with full validation
        await check_wallet_status(wallet)

        # Also show fast check without validation
        await check_without_validation(wallet)


if __name__ == "__main__":
    asyncio.run(main())
