#!/usr/bin/env python3
"""Auto Multi-Mint Melt Example

This example demonstrates the enhanced melt functionality that automatically
consolidates proofs from multiple mints when no single mint has enough balance
to pay a Lightning invoice.

The wallet will:
1. Check the invoice amount and fees
2. Verify total balance across all mints
3. If no single mint has enough, automatically swap proofs to consolidate
4. Execute payment from the mint with sufficient balance
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for local development
sys.path.insert(0, str(Path(__file__).parent.parent))

from sixty_nuts import TempWallet

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Example configuration
MINT_URLS = [
    "https://testnut.cashu.space",
    "https://mintter.shocknet.us",
    "https://mint.lnvoltz.com"
]

async def distribute_balance_across_mints(wallet: TempWallet, total_amount: int) -> None:
    """Helper to distribute balance across multiple mints for testing."""
    amount_per_mint = total_amount // len(wallet.mint_urls)
    remainder = total_amount % len(wallet.mint_urls)
    
    print(f"\n📊 Distributing {total_amount} sats across {len(wallet.mint_urls)} mints...")
    
    for i, mint_url in enumerate(wallet.mint_urls):
        # Add remainder to first mint
        mint_amount = amount_per_mint + (remainder if i == 0 else 0)
        
        print(f"\n💰 Minting {mint_amount} sats at {mint_url}...")
        
        # Create invoice at this mint
        quote_id, payment_request = await wallet.create_quote(mint_amount)
        print(f"   Invoice: {payment_request[:50]}...")
        
        # In a real scenario, you would pay this invoice
        print(f"   ⚡ Pay this invoice to mint {mint_amount} sats")
        print(f"   Waiting for payment confirmation...")
        
        # For demo purposes, we'll just show the process
        # In real usage, the invoice would be paid and tokens minted


async def check_mint_balances(wallet: TempWallet) -> dict[str, int]:
    """Check and display balance at each mint."""
    state = await wallet.fetch_wallet_state(check_proofs=True)
    
    # Group proofs by mint
    proofs_by_mint: dict[str, list] = {}
    for proof in state.proofs:
        mint_url = proof.get("mint") or wallet.mint_urls[0]
        if mint_url not in proofs_by_mint:
            proofs_by_mint[mint_url] = []
        proofs_by_mint[mint_url].append(proof)
    
    # Calculate balance per mint
    mint_balances = {}
    print("\n💼 Mint Balances:")
    for mint_url, proofs in proofs_by_mint.items():
        balance = sum(p["amount"] for p in proofs)
        mint_balances[mint_url] = balance
        print(f"   {mint_url}: {balance} sats ({len(proofs)} proofs)")
    
    total = sum(mint_balances.values())
    print(f"   Total: {total} sats")
    
    return mint_balances


async def demonstrate_auto_multi_mint_melt(wallet: TempWallet, invoice: str) -> None:
    """Demonstrate automatic proof consolidation for payment."""
    print(f"\n🎯 Attempting to pay invoice: {invoice[:50]}...")
    
    # Check balances before payment
    print("\n📊 Balance distribution before payment:")
    mint_balances_before = await check_mint_balances(wallet)
    
    try:
        # The melt function will automatically:
        # 1. Check which mints can handle the invoice
        # 2. Calculate required amount including fees
        # 3. Check if any single mint has enough balance
        # 4. If not, consolidate proofs from other mints
        # 5. Execute the payment
        
        print("\n⚡ Executing melt with auto multi-mint support...")
        await wallet.melt(invoice)
        
        print("\n✅ Payment successful!")
        
        # Check balances after payment
        print("\n📊 Balance distribution after payment:")
        mint_balances_after = await check_mint_balances(wallet)
        
        # Show what changed
        print("\n📈 Balance changes:")
        all_mints = set(mint_balances_before.keys()) | set(mint_balances_after.keys())
        for mint in all_mints:
            before = mint_balances_before.get(mint, 0)
            after = mint_balances_after.get(mint, 0)
            change = after - before
            if change != 0:
                print(f"   {mint}: {before} → {after} ({change:+d})")
                
    except Exception as e:
        print(f"\n❌ Payment failed: {e}")
        raise


async def main() -> None:
    """Run the auto multi-mint melt example."""
    print("🚀 Auto Multi-Mint Melt Example")
    print("=" * 50)
    
    # Create temporary wallet with multiple mints
    wallet = await TempWallet.create(
        mint_urls=MINT_URLS,
        currency="sat",
        relays=["wss://relay.damus.io", "wss://relay.primal.net"],
    )
    
    # Get wallet info
    print(f"\n📱 Temporary wallet created")
    print(f"   Nsec: {wallet.nsec[:20]}...")  # Show partial nsec for privacy
    print(f"   Mints: {len(wallet.mint_urls)}")
    
    # Example scenario 1: Distribute small amounts across mints
    print("\n\n📌 Scenario 1: Small amounts distributed across mints")
    print("-" * 50)
    await distribute_balance_across_mints(wallet, 300)  # 100 sats per mint
    
    # Simulate having the distributed balance
    print("\n💡 Simulating distributed balance:")
    print("   Mint 1: 120 sats")
    print("   Mint 2: 90 sats")  
    print("   Mint 3: 90 sats")
    print("   Total: 300 sats")
    
    # Try to pay a 200 sat invoice (no single mint has enough)
    invoice_200 = "lnbc2u1..." # Example invoice for 200 sats
    print(f"\n⚡ Attempting to pay 200 sat invoice...")
    print("   No single mint has enough balance!")
    print("   Wallet will automatically consolidate proofs...")
    
    # The melt function would automatically:
    # 1. Detect that Mint 1 has the highest balance (120 sats)
    # 2. Calculate it needs ~85 more sats (200 + fees - 120)
    # 3. Swap 90 sats from Mint 2 to Mint 1
    # 4. Pay the invoice from Mint 1
    
    print("\n🔄 Auto consolidation process:")
    print("   1. Target mint selected: Mint 1 (highest balance)")
    print("   2. Need to transfer: ~85 sats to cover invoice + fees")
    print("   3. Swapping 90 sats from Mint 2 → Mint 1")
    print("   4. Mint 1 balance after swap: 210 sats")
    print("   5. Paying invoice from Mint 1")
    
    # Example scenario 2: Larger invoice requiring multiple swaps
    print("\n\n📌 Scenario 2: Large invoice requiring multiple mint consolidation")
    print("-" * 50)
    
    print("\n💡 Simulating balance:")
    print("   Mint 1: 150 sats")
    print("   Mint 2: 200 sats")
    print("   Mint 3: 180 sats")
    print("   Total: 530 sats")
    
    invoice_450 = "lnbc4500n1..." # Example invoice for 450 sats
    print(f"\n⚡ Attempting to pay 450 sat invoice...")
    print("   No single mint has enough!")
    
    print("\n🔄 Auto consolidation process:")
    print("   1. Target mint selected: Mint 2 (highest balance)")
    print("   2. Need to transfer: ~260 sats (450 + fees - 200)")
    print("   3. Collecting proofs from other mints:")
    print("      - 150 sats from Mint 1")
    print("      - 180 sats from Mint 3")
    print("   4. Swapping 330 sats → Mint 2")
    print("   5. Mint 2 balance after swaps: 530 sats")
    print("   6. Paying invoice from Mint 2")
    
    # Show the benefits
    print("\n\n✨ Benefits of Auto Multi-Mint Melt:")
    print("   ✓ No manual proof management needed")
    print("   ✓ Automatic optimal mint selection")
    print("   ✓ Seamless cross-mint consolidation")
    print("   ✓ Handles fees automatically")
    print("   ✓ Works with any number of mints")
    
    # Clean up
    await wallet.aclose()
    print("\n\n✅ Example completed!")


if __name__ == "__main__":
    asyncio.run(main())