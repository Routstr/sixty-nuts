#!/usr/bin/env python3
"""Example: Check wallet balance and proof details.

Shows current balance and detailed proof breakdown by mint and denomination.
"""

import asyncio
from sixty_nuts.wallet import Wallet


async def main() -> None:
    """Check wallet balance with detailed breakdown."""
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        print("Checking wallet balance...")

        # Get validated balance (checks proofs with mint)
        balance = await wallet.get_balance(check_proofs=True)
        print(f"💰 Total Balance: {balance} sats")

        # Get detailed wallet state
        state = await wallet.fetch_wallet_state(check_proofs=False)
        print(f"📄 Total Proofs: {len(state.proofs)}")

        if not state.proofs:
            print("No proofs found in wallet")
            return

        # Group proofs by mint
        proofs_by_mint: dict[str, list] = {}
        for proof in state.proofs:
            mint_url = proof.get("mint") or "unknown"
            if mint_url not in proofs_by_mint:
                proofs_by_mint[mint_url] = []
            proofs_by_mint[mint_url].append(proof)

        print(f"\n🏦 Breakdown by mint ({len(proofs_by_mint)} mints):")

        for mint_url, proofs in proofs_by_mint.items():
            mint_balance = sum(p["amount"] for p in proofs)
            print(f"\n  📍 {mint_url}")
            print(f"     Balance: {mint_balance} sats")
            print(f"     Proofs: {len(proofs)}")

            # Show denomination breakdown
            denominations: dict[int, int] = {}
            for proof in proofs:
                amount = proof["amount"]
                denominations[amount] = denominations.get(amount, 0) + 1

            print("     Denominations:")
            for denom in sorted(denominations.keys(), reverse=True):
                count = denominations[denom]
                total_value = denom * count
                print(f"       {denom:4d} sats × {count:2d} = {total_value:4d} sats")


if __name__ == "__main__":
    asyncio.run(main())
