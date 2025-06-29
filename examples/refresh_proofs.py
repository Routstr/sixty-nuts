#!/usr/bin/env python3
"""Example: Refresh all proofs by swapping them at the mint.

This example shows how to swap old proofs for new ones at the mint.
This is useful for privacy and can help consolidate small proofs.
"""

import asyncio
from sixty_nuts.wallet import Wallet


async def refresh_proofs(wallet: Wallet):
    """Swap all proofs for fresh ones at the mint."""
    print("Getting current wallet state...")
    state = await wallet.fetch_wallet_state(check_proofs=True)

    if not state.proofs:
        print("❌ No proofs found in wallet!")
        return

    print(f"Found {len(state.proofs)} proofs worth {state.balance} sats")

    print(f"Refreshing proofs at {len(state.proofs_by_mints)} mint(s)...")

    for mint_url, mint_proofs in state.proofs_by_mints.items():
        mint_balance = sum(p["amount"] for p in mint_proofs)
        print(f"\n📍 Processing {len(mint_proofs)} proofs at {mint_url}")
        print(f"   Balance: {mint_balance} sats")

        try:
            # Calculate optimal denominations for consolidation
            optimal_denoms = wallet._calculate_optimal_denominations(mint_balance)

            # Swap proofs for optimal denominations
            new_proofs = await wallet._swap_proof_denominations(
                mint_proofs, optimal_denoms, mint_url
            )

            print(f"   ✅ Refreshed to {len(new_proofs)} optimized proofs")

            # Store the new proofs
            await wallet.store_proofs(new_proofs)

        except Exception as e:
            print(f"   ❌ Failed to refresh: {e}")

    # Verify final state
    print("\nVerifying final state...")
    final_state = await wallet.fetch_wallet_state(check_proofs=True)
    print(f"Final balance: {final_state.balance} sats")
    print(f"Final proof count: {len(final_state.proofs)}")


async def main():
    """Main function."""
    print("🔄 Cashu Proof Refresh Example")
    print("=" * 40)

    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        await refresh_proofs(wallet)


if __name__ == "__main__":
    asyncio.run(main())
