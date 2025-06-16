#!/usr/bin/env python3
"""Example: Validate Cashu tokens before accepting.

Shows how to check if a token is valid, unspent, and from a trusted mint.
Essential for merchants or services accepting Cashu payments.
"""

import asyncio
import sys
from sixty_nuts.wallet import Wallet, TempWallet


async def validate_token(token: str, trusted_mints: list[str] | None = None):
    """Validate a Cashu token without redeeming it."""
    print(f"Validating token: {token[:50]}...")

    # Use temporary wallet for validation
    async with TempWallet() as wallet:
        try:
            # Parse the token
            mint_url, unit, proofs = wallet._parse_cashu_token(token)
            total_amount = sum(p["amount"] for p in proofs)

            print("\n📊 Token details:")
            print(f"  Mint: {mint_url}")
            print(f"  Amount: {total_amount} {unit}")
            print(f"  Proofs: {len(proofs)}")

            # Check if mint is trusted
            is_trusted = True
            if trusted_mints:
                is_trusted = mint_url in trusted_mints
                print(f"  Trusted mint: {'✅ Yes' if is_trusted else '❌ No'}")

            # Validate proof states with the mint
            print("\n🔍 Checking proof states...")
            mint = wallet._get_mint(mint_url)

            # Compute Y values for validation
            y_values = wallet._compute_proof_y_values(proofs)

            # Check state with mint
            state_response = await mint.check_state(Ys=y_values)

            # Analyze results
            spent_count = 0
            unspent_count = 0
            unknown_count = 0
            unspent_amount = 0

            for i, proof in enumerate(proofs):
                if i < len(state_response["states"]):
                    state_info = state_response["states"][i]
                    state = state_info.get("state", "UNKNOWN")

                    if state == "SPENT":
                        spent_count += 1
                    elif state == "UNSPENT":
                        unspent_count += 1
                        unspent_amount += proof["amount"]
                    else:
                        unknown_count += 1

            print("\n📋 Validation results:")
            print(f"  Unspent proofs: {unspent_count}/{len(proofs)}")
            print(f"  Spent proofs: {spent_count}/{len(proofs)}")
            if unknown_count > 0:
                print(f"  Unknown state: {unknown_count}/{len(proofs)}")

            print(f"\n💰 Valid amount: {unspent_amount} {unit}")

            # Overall validation result
            is_valid = (
                unspent_count == len(proofs)  # All proofs unspent
                and spent_count == 0  # No spent proofs
                and unspent_amount == total_amount  # Amount matches
                and is_trusted  # From trusted mint
            )

            if is_valid:
                print("\n✅ Token is VALID and can be accepted")
            else:
                print("\n❌ Token is INVALID:")
                if spent_count > 0:
                    print("   - Contains spent proofs")
                if unspent_amount != total_amount:
                    print(
                        f"   - Amount mismatch (expected {total_amount}, got {unspent_amount})"
                    )
                if not is_trusted and trusted_mints:
                    print("   - From untrusted mint")

            return {
                "valid": is_valid,
                "mint": mint_url,
                "amount": unspent_amount,
                "unit": unit,
                "trusted": is_trusted,
                "unspent_proofs": unspent_count,
                "spent_proofs": spent_count,
            }

        except Exception as e:
            print(f"\n❌ Token validation failed: {e}")
            return {"valid": False, "error": str(e)}


async def merchant_accept_flow(token: str, expected_amount: int | None = None):
    """Complete merchant acceptance flow with validation."""
    TRUSTED_MINTS = [
        "https://mint.minibits.cash/Bitcoin",
        "https://legend.lnbits.com/cashu/api/v1/AptDNABNBXv8gpuywhx6NV",
    ]

    print("🏪 Merchant payment acceptance")
    print("=" * 40)

    # Step 1: Validate token
    validation = await validate_token(token, TRUSTED_MINTS)

    if not validation["valid"]:
        print("\n❌ Payment rejected - invalid token")
        return False

    # Step 2: Check amount if specified
    if expected_amount is not None:
        if validation["amount"] < expected_amount:
            print("\n❌ Payment rejected - insufficient amount")
            print(f"   Expected: {expected_amount} {validation['unit']}")
            print(f"   Received: {validation['amount']} {validation['unit']}")
            return False

    # Step 3: Accept payment
    print(f"\n💳 Accepting payment of {validation['amount']} {validation['unit']}...")

    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
        mint_urls=TRUSTED_MINTS,
    ) as wallet:
        try:
            amount, unit = await wallet.redeem(token)
            print(f"✅ Payment accepted: {amount} {unit}")

            # Show new balance
            balance = await wallet.get_balance()
            print(f"💰 New balance: {balance} {unit}")

            return True

        except Exception as e:
            print(f"❌ Failed to redeem: {e}")
            return False


async def batch_validate(tokens: list[str]):
    """Validate multiple tokens at once."""
    print(f"Validating {len(tokens)} tokens...\n")

    results = []
    total_valid_amount = 0

    for i, token in enumerate(tokens, 1):
        print(f"Token {i}/{len(tokens)}:")
        result = await validate_token(token)
        results.append(result)

        if result["valid"]:
            total_valid_amount += result["amount"]

        print("-" * 40)

    # Summary
    valid_count = sum(1 for r in results if r["valid"])
    print("\n📊 Summary:")
    print(f"  Valid tokens: {valid_count}/{len(tokens)}")
    print(f"  Total valid amount: {total_valid_amount} sats")

    return results


async def main():
    """Main example."""
    if len(sys.argv) < 2:
        print("Usage: python validate_token.py <cashu_token> [expected_amount]")
        print("Example: python validate_token.py cashuAey...")
        print("\nFor merchant flow:")
        print(
            "Usage: python validate_token.py merchant <cashu_token> [expected_amount]"
        )
        print("\nFor batch validation:")
        print("Usage: python validate_token.py batch <token1> <token2> ...")
        return

    if sys.argv[1] == "merchant":
        # Merchant acceptance flow
        if len(sys.argv) < 3:
            print("Need token for merchant flow")
            return

        token = sys.argv[2]
        expected = int(sys.argv[3]) if len(sys.argv) > 3 else None
        await merchant_accept_flow(token, expected)

    elif sys.argv[1] == "batch":
        # Batch validation
        tokens = sys.argv[2:]
        if not tokens:
            print("Need at least one token for batch validation")
            return
        await batch_validate(tokens)

    else:
        # Simple validation
        token = sys.argv[1]
        await validate_token(token)


if __name__ == "__main__":
    asyncio.run(main())
