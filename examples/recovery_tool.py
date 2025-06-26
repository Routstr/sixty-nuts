#!/usr/bin/env python3
"""Example: Wallet recovery demonstration.

Shows how to recover wallet state from Nostr relays using just your private key.
This demonstrates the power of NIP-60 for wallet backup and recovery.
"""

import asyncio
from sixty_nuts.wallet import Wallet


async def demonstrate_recovery(nsec: str):
    """Demonstrate wallet recovery from Nostr relays."""
    print("🔄 Wallet Recovery Demonstration")
    print("=" * 40)

    print("Creating wallet from private key...")
    print("This will automatically recover state from Nostr relays...")

    async with Wallet(nsec=nsec) as wallet:
        print("✅ Wallet created and connected to relays")

        # Check if wallet events exist
        exists, wallet_event = await wallet.check_wallet_event_exists()

        if exists and wallet_event:
            from datetime import datetime

            created_time = datetime.fromtimestamp(wallet_event["created_at"])
            print(f"📅 Found wallet event created: {created_time}")
            print(f"🆔 Event ID: {wallet_event['id'][:16]}...")
        else:
            print("⚠️  No wallet configuration found on relays")

        # Show recovered configuration
        print("\n🏦 Recovered Configuration:")
        print(f"   Mints: {len(wallet.mint_urls)}")
        for i, mint_url in enumerate(wallet.mint_urls, 1):
            print(f"   {i}. {mint_url}")

        print(f"   Relays: {len(wallet.relays)}")
        for i, relay_url in enumerate(wallet.relays, 1):
            print(f"   {i}. {relay_url}")

        # Show recovered balance and proofs
        print("\n💰 Recovered Wallet State:")
        balance = await wallet.get_balance(check_proofs=False)
        print(f"   Balance: {balance} sats")

        state = await wallet.fetch_wallet_state(check_proofs=False)
        print(f"   Proofs: {len(state.proofs)}")

        if state.proofs:
            # Group by mint
            proofs_by_mint: dict[str, int] = {}
            for proof in state.proofs:
                mint_url = proof.get("mint") or "unknown"
                proofs_by_mint[mint_url] = (
                    proofs_by_mint.get(mint_url, 0) + proof["amount"]
                )

            print("   Balance by mint:")
            for mint_url, amount in proofs_by_mint.items():
                print(f"     {mint_url}: {amount} sats")

        # Validate recovered proofs
        if balance > 0:
            print("\n🔍 Validating recovered proofs with mints...")
            try:
                validated_balance = await wallet.get_balance(check_proofs=True)
                if validated_balance == balance:
                    print("✅ All recovered proofs are valid!")
                elif validated_balance < balance:
                    spent_amount = balance - validated_balance
                    print(f"⚠️  {spent_amount} sats worth of proofs are spent")
                    print(f"   Valid balance: {validated_balance} sats")
                else:
                    print("🤔 Unexpected validation result")
            except Exception as e:
                print(f"❌ Validation failed: {e}")

        print("\n🎉 Recovery demonstration complete!")
        print(f"   Total recovered: {balance} sats across {len(state.proofs)} proofs")


async def show_recovery_info():
    """Show information about wallet recovery."""
    print("💡 Wallet Recovery Information:")
    print("=" * 40)
    print()
    print("✅ What gets recovered automatically:")
    print("   • Wallet configuration (mints, settings)")
    print("   • All token proofs stored on Nostr")
    print("   • Transaction history")
    print("   • Relay configuration")
    print()
    print("🔑 What you need for recovery:")
    print("   • Your Nostr private key (nsec)")
    print("   • Access to the same Nostr relays")
    print()
    print("⚡ Recovery is automatic:")
    print("   • Just create a wallet with your nsec")
    print("   • The wallet fetches all data from relays")
    print("   • No manual backup files needed")
    print()


async def main():
    """Main function."""
    # Demo NSEC - replace with your own for real recovery
    demo_nsec = "nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"

    await show_recovery_info()
    await demonstrate_recovery(demo_nsec)


if __name__ == "__main__":
    asyncio.run(main())
