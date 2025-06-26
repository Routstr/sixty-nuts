#!/usr/bin/env python3
"""Example: Clear wallet by melting all tokens to Lightning.

Shows how to empty your wallet by converting all tokens back to Lightning invoices.
Useful for cashing out or cleaning up your wallet.
"""

import asyncio
from sixty_nuts.wallet import Wallet


async def clear_wallet_to_lightning(wallet: Wallet, destination_invoice: str):
    """Clear wallet by paying a Lightning invoice with all available funds."""
    print("💸 Clearing wallet to Lightning invoice...")

    # Check current balance
    balance = await wallet.get_balance(check_proofs=True)
    print(f"Current balance: {balance} sats")

    if balance == 0:
        print("✅ Wallet is already empty!")
        return

    try:
        # Pay the invoice (this will use all available proofs)
        print(f"⚡ Paying Lightning invoice...")
        await wallet.melt(destination_invoice)

        print("✅ Successfully paid Lightning invoice!")

        # Check remaining balance
        final_balance = await wallet.get_balance()
        print(f"Remaining balance: {final_balance} sats")

        if final_balance == 0:
            print("🎉 Wallet successfully cleared!")
        else:
            print(f"💰 {final_balance} sats remain (may be dust or fees)")

    except Exception as e:
        print(f"❌ Failed to clear wallet: {e}")


async def clear_wallet_to_address(wallet: Wallet, lightning_address: str):
    """Clear wallet by sending all funds to a Lightning address."""
    print(f"💸 Clearing wallet to {lightning_address}...")

    # Check current balance
    balance = await wallet.get_balance(check_proofs=True)
    print(f"Current balance: {balance} sats")

    if balance == 0:
        print("✅ Wallet is already empty!")
        return

    # Leave some room for fees (typically 1-2 sats)
    if balance <= 2:
        print("❌ Balance too small to clear (need > 2 sats for fees)")
        return

    send_amount = balance - 2  # Reserve 2 sats for fees

    try:
        print(f"⚡ Sending {send_amount} sats to {lightning_address}...")
        actual_paid = await wallet.send_to_lnurl(lightning_address, send_amount)

        print(f"✅ Successfully sent {actual_paid} sats!")

        # Check remaining balance
        final_balance = await wallet.get_balance()
        print(f"Remaining balance: {final_balance} sats")

        if final_balance <= 2:
            print("🎉 Wallet successfully cleared!")
        else:
            print(f"💰 {final_balance} sats remain as dust")

    except Exception as e:
        print(f"❌ Failed to clear wallet: {e}")


async def show_wallet_state(wallet: Wallet):
    """Show current wallet state."""
    print("\n📊 Current Wallet State:")

    balance = await wallet.get_balance(check_proofs=False)
    print(f"   Balance: {balance} sats")

    state = await wallet.fetch_wallet_state(check_proofs=False)
    print(f"   Proofs: {len(state.proofs)}")

    if state.proofs:
        # Group by mint
        proofs_by_mint: dict[str, int] = {}
        for proof in state.proofs:
            mint_url = proof.get("mint") or "unknown"
            proofs_by_mint[mint_url] = proofs_by_mint.get(mint_url, 0) + proof["amount"]

        print("   By mint:")
        for mint_url, amount in proofs_by_mint.items():
            print(f"     {mint_url}: {amount} sats")


async def main():
    """Main function."""
    print("🧹 Wallet Clearing Example")
    print("=" * 40)

    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        # Show initial state
        await show_wallet_state(wallet)

        balance = await wallet.get_balance()
        if balance == 0:
            print("\n✅ Wallet is already empty - nothing to clear!")
            return

        print(f"\n💡 You can clear this wallet by:")
        print(f"   1. Paying a Lightning invoice for {balance} sats")
        print(f"   2. Sending to a Lightning address (leaving ~2 sats for fees)")
        print(
            f"\n🔒 This example is read-only - uncomment code below to actually clear"
        )

        # Example clearance methods (commented out for safety)
        #
        # To clear to Lightning address:
        # await clear_wallet_to_address(wallet, "user@getalby.com")
        #
        # To clear to specific invoice:
        # invoice = "lnbc1000n1..."  # Your invoice here
        # await clear_wallet_to_lightning(wallet, invoice)


if __name__ == "__main__":
    asyncio.run(main())
