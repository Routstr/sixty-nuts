#!/usr/bin/env python3
"""Example: Multi-mint wallet operations.

Shows how to work with multiple mints and check balances per mint.
"""

import asyncio
from sixty_nuts.wallet import Wallet


# Popular Cashu mints for demonstration
MINTS = [
    "https://mint.minibits.cash/Bitcoin",
    "https://stablenut.umint.cash",
    "https://mint.macadamia.cash",
]


async def check_multi_mint_balance(wallet: Wallet):
    """Check balance across all configured mints."""
    print("Checking balances across all mints...")

    state = await wallet.fetch_wallet_state(check_proofs=True)

    # Group by mint
    mint_balances: dict[str, int] = {}
    for proof in state.proofs:
        mint_url = proof.get("mint") or "unknown"
        mint_balances[mint_url] = mint_balances.get(mint_url, 0) + proof["amount"]

    print(f"\n💰 Balance by mint:")
    total = 0
    for mint_url, balance in mint_balances.items():
        print(f"   📍 {mint_url}: {balance} sats")
        total += balance

    if not mint_balances:
        print("   No balance in any mint")

    print(f"\n📊 Total across all mints: {total} sats")
    print(f"🏦 Configured mints: {len(wallet.mint_urls)}")

    return mint_balances


async def add_new_mint(wallet: Wallet, mint_url: str):
    """Add a new mint to the wallet configuration."""
    print(f"\n➕ Adding new mint: {mint_url}")

    if mint_url not in wallet.mint_urls:
        wallet.mint_urls.append(mint_url)

        # Update wallet event with new mint
        try:
            await wallet.initialize_wallet(force=True)
            print("✅ Mint added and wallet updated")
        except Exception as e:
            print(f"⚠️  Mint added locally but failed to update wallet event: {e}")
    else:
        print("ℹ️  Mint already in wallet")


async def main():
    """Main example."""
    # Initialize wallet with multiple mints
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
        mint_urls=MINTS[:2],  # Start with first 2 mints
    ) as wallet:
        print(f"🏦 Wallet initialized with {len(wallet.mint_urls)} mints")

        # Check balances across mints
        balances = await check_multi_mint_balance(wallet)

        # Example: Add a new mint
        if len(MINTS) > 2:
            await add_new_mint(wallet, MINTS[2])

            # Check balances again after adding mint
            print("\nBalance after adding new mint:")
            await check_multi_mint_balance(wallet)


if __name__ == "__main__":
    asyncio.run(main())
