#!/usr/bin/env python3
"""Example: Validate a Cashu token.

Shows how to parse and validate a Cashu token before accepting it.
Useful for merchants who want to check token validity.
"""

import asyncio
import sys
from sixty_nuts.wallet import Wallet


async def validate_token(token: str):
    """Validate a Cashu token and check if proofs are unspent."""
    print("🔍 Validating Cashu token...\n")

    # Create a temporary wallet just for parsing and validation
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        try:
            # Parse the token
            mint_url, unit, proofs = wallet._parse_cashu_token(token)

            print("📊 Token Details:")
            print(f"   Mint: {mint_url}")
            print(f"   Unit: {unit}")
            print(f"   Proofs: {len(proofs)}")
            print(f"   Total Value: {sum(p['amount'] for p in proofs)} {unit}")

            # Check proof states with the mint
            print("\n🔄 Checking proof states with mint...")
            mint = wallet._get_mint(mint_url)

            # Compute Y values for all proofs
            y_values = wallet._compute_proof_y_values(proofs)
            state_response = await mint.check_state(Ys=y_values)

            # Count states
            spent_count = 0
            unspent_count = 0

            for i, proof in enumerate(proofs):
                if i < len(state_response.get("states", [])):
                    state_info = state_response["states"][i]
                    state = state_info.get("state", "UNKNOWN")

                    if state == "SPENT":
                        spent_count += 1
                    elif state == "UNSPENT":
                        unspent_count += 1

                    # Show first few proofs as examples
                    if i < 3:
                        print(f"   Proof {i + 1}: {proof['amount']} {unit} - {state}")

            if len(proofs) > 3:
                print(f"   ... and {len(proofs) - 3} more proofs")

            # Summary
            print("\n📈 Validation Results:")
            print(f"   ✅ Unspent: {unspent_count} proofs")
            print(f"   ❌ Spent: {spent_count} proofs")

            if spent_count == 0:
                print("\n✅ All proofs are unspent - token is valid!")
                return True
            elif spent_count == len(proofs):
                print("\n❌ All proofs are spent - token is worthless!")
                return False
            else:
                print(
                    f"\n⚠️  Warning: {spent_count} out of {len(proofs)} proofs are spent!"
                )
                print("   Token will fail to redeem due to spent proofs.")
                return False

        except Exception as e:
            print(f"❌ Error validating token: {e}")
            return False


async def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python validate_token.py <cashu_token>")
        print("\nExample:")
        print("  python validate_token.py cashuAey...")
        print("  python validate_token.py $(cat token.txt)")
        return

    token = sys.argv[1].strip()

    # Basic validation
    if not token.startswith("cashu"):
        print("❌ Invalid token! Cashu tokens start with 'cashu'")
        return

    is_valid = await validate_token(token)

    if is_valid:
        print("\n🎉 Token validation passed!")
    else:
        print("\n💀 Token validation failed!")


if __name__ == "__main__":
    asyncio.run(main())
