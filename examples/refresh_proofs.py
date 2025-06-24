#!/usr/bin/env python3
"""Refresh all proofs by swapping them at the mint.

This script:
1. Backs up all current proofs to a timestamped JSON file
2. Swaps all proofs at the mint for fresh ones
3. Updates the wallet with the new proofs

This is useful for:
- Privacy: Breaking the link between old and new proofs
- Consolidation: Can help consolidate many small proofs
- Safety: Creates a backup before the operation
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from sixty_nuts.wallet import Wallet, ProofDict


async def backup_proofs(wallet: Wallet, backup_dir: Path = Path("proof_backups")):
    """Backup all current proofs to a JSON file."""
    # Create backup directory if it doesn't exist
    backup_dir.mkdir(exist_ok=True)

    # Get current wallet state
    print("Fetching current wallet state...")
    state = await wallet.fetch_wallet_state(check_proofs=True)

    if not state.proofs:
        print("❌ No proofs found in wallet!")
        return None, 0

    # Create backup data
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_data = {
        "timestamp": datetime.now().isoformat(),
        "total_balance": state.balance,
        "proof_count": len(state.proofs),
        "proofs": state.proofs,
        "mints": list(set(p.get("mint") or wallet.mint_urls[0] for p in state.proofs)),
        "wallet_pubkey": wallet._get_pubkey(),
    }

    # Save to file
    backup_file = backup_dir / f"proofs_backup_{timestamp}.json"
    with open(backup_file, "w") as f:
        json.dump(backup_data, f, indent=2)

    print(f"\n✅ Backed up {len(state.proofs)} proofs to: {backup_file}")
    print(f"   Total value: {state.balance} sats")

    return backup_file, state.balance


async def refresh_proofs(wallet: Wallet):
    """Swap all proofs for fresh ones at the mint."""
    print("\nRefreshing proofs at mint...")

    # Get current state
    state = await wallet.fetch_wallet_state(check_proofs=True)
    initial_balance = state.balance

    # Group proofs by mint
    proofs_by_mint: dict[str, list[ProofDict]] = {}
    for proof in state.proofs:
        mint_url = proof.get("mint") or wallet.mint_urls[0]
        if mint_url not in proofs_by_mint:
            proofs_by_mint[mint_url] = []
        proofs_by_mint[mint_url].append(proof)

    print(f"\nFound proofs at {len(proofs_by_mint)} mint(s)")

    # Process each mint separately
    total_refreshed = 0
    for mint_url, mint_proofs in proofs_by_mint.items():
        mint_balance = sum(p["amount"] for p in mint_proofs)
        print(f"\nProcessing {len(mint_proofs)} proofs at {mint_url}")
        print(f"  Balance at this mint: {mint_balance} sats")

        try:
            # Get mint instance
            mint = wallet._get_mint(mint_url)

            # Process in batches of 1000 to respect mint limits
            BATCH_SIZE = 1000
            all_new_proofs: list[ProofDict] = []

            for batch_start in range(0, len(mint_proofs), BATCH_SIZE):
                batch_end = min(batch_start + BATCH_SIZE, len(mint_proofs))
                batch_proofs = mint_proofs[batch_start:batch_end]
                batch_balance = sum(p["amount"] for p in batch_proofs)

                if len(mint_proofs) > BATCH_SIZE:
                    print(
                        f"  Processing batch {batch_start // BATCH_SIZE + 1}/{(len(mint_proofs) + BATCH_SIZE - 1) // BATCH_SIZE}: {len(batch_proofs)} proofs ({batch_balance} sats)"
                    )

                # Convert to mint proof format
                mint_proof_objs = []
                for p in batch_proofs:
                    mint_proof_objs.append(wallet._proofdict_to_mint_proof(p))

                # Get active keysets
                keysets_resp = await mint.get_keysets()
                keysets = keysets_resp.get("keysets", [])
                active_keysets = [ks for ks in keysets if ks.get("active", True)]

                if not active_keysets:
                    print(f"  ⚠️  No active keysets found at {mint_url}")
                    continue

                keyset_id = str(active_keysets[0]["id"])

                # Create blinded messages for the same amount (prefer consolidation)
                from sixty_nuts.crypto import create_blinded_message_with_secret

                outputs = []
                secrets = []
                blinding_factors = []

                # Calculate optimal denominations for better consolidation
                optimal_denoms = wallet._calculate_optimal_denominations(batch_balance)

                for denomination, count in sorted(optimal_denoms.items()):
                    for _ in range(count):
                        secret, r_hex, blinded_msg = create_blinded_message_with_secret(
                            denomination, keyset_id
                        )
                        outputs.append(blinded_msg)
                        secrets.append(secret)
                        blinding_factors.append(r_hex)

                print(f"  Swapping for {len(outputs)} new proofs...")

                # Perform swap
                swap_resp = await mint.swap(inputs=mint_proof_objs, outputs=outputs)

                # Get mint keys for unblinding
                keys_resp = await mint.get_keys(keyset_id)
                mint_keysets = keys_resp.get("keysets", [])
                mint_keys = None

                for ks in mint_keysets:
                    if str(ks.get("id")) == keyset_id:
                        keys_data: dict[str, str] | str = ks.get("keys", {})
                        if isinstance(keys_data, dict) and keys_data:
                            mint_keys = keys_data
                            break

                if not mint_keys:
                    print("  ❌ Could not find mint keys for unblinding")
                    continue

                # Unblind the new proofs
                batch_new_proofs: list[ProofDict] = []
                for i, sig in enumerate(swap_resp["signatures"]):
                    # Get the public key for this amount
                    amount = sig["amount"]
                    from sixty_nuts.crypto import get_mint_pubkey_for_amount

                    mint_pubkey = get_mint_pubkey_for_amount(mint_keys, amount)
                    if not mint_pubkey:
                        print(
                            f"  ⚠️  Could not find mint public key for amount {amount}"
                        )
                        continue

                    # Unblind the signature
                    from coincurve import PublicKey
                    from sixty_nuts.crypto import unblind_signature

                    C_ = PublicKey(bytes.fromhex(sig["C_"]))
                    r = bytes.fromhex(blinding_factors[i])
                    C = unblind_signature(C_, r, mint_pubkey)

                    batch_new_proofs.append(
                        ProofDict(
                            id=sig["id"],
                            amount=sig["amount"],
                            secret=secrets[i],
                            C=C.format(compressed=True).hex(),
                            mint=mint_url,
                        )
                    )

                # Calculate what we got back
                batch_new_balance = sum(p["amount"] for p in batch_new_proofs)
                print(
                    f"  ✅ Received {len(batch_new_proofs)} new proofs worth {batch_new_balance} sats"
                )

                if batch_new_balance != batch_balance:
                    print(
                        f"  ⚠️  Warning: Balance mismatch! Had {batch_balance}, got {batch_new_balance}"
                    )

                all_new_proofs.extend(batch_new_proofs)

            # After processing all batches, handle the accumulated results
            new_balance = sum(p["amount"] for p in all_new_proofs)
            total_refreshed += new_balance

            if len(mint_proofs) > BATCH_SIZE:
                print(
                    f"  📊 Total refreshed at {mint_url}: {len(all_new_proofs)} proofs worth {new_balance} sats"
                )

            # Delete old token events and publish new ones
            # First, find which events contain the old proofs
            events_to_delete = set()
            proof_to_event = state.proof_to_event_id or {}
            for proof in mint_proofs:
                proof_id = f"{proof['secret']}:{proof['C']}"
                if proof_id in proof_to_event:
                    events_to_delete.add(proof_to_event[proof_id])

            # Publish new proofs
            if all_new_proofs:
                print("  Publishing new proofs to relays...")
                # Add wait to ensure deletes are processed before publishing new tokens
                if events_to_delete:
                    print(f"  Deleting {len(events_to_delete)} old token events...")
                    await asyncio.sleep(0.5)  # Give relays time to process deletes

                await wallet.event_manager.publish_token_event(
                    all_new_proofs,
                    deleted_token_ids=list(events_to_delete)
                    if events_to_delete
                    else None,
                )

        except Exception as e:
            print(f"  ❌ Error refreshing proofs at {mint_url}: {e}")
            continue

    # Verify final state
    print("\nVerifying final state...")
    final_state = await wallet.fetch_wallet_state(check_proofs=True)

    print("\n📊 Refresh Summary:")
    print(f"   Initial balance: {initial_balance} sats")
    print(f"   Final balance: {final_state.balance} sats")
    print(f"   Difference: {final_state.balance - initial_balance} sats")

    if final_state.balance < initial_balance:
        print(
            "\n⚠️  WARNING: Balance decreased! Check the backup file to recover if needed."
        )

    return final_state.balance


async def main():
    """Main function."""
    # Check if backup directory was specified
    backup_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("proof_backups")

    print("🔄 Cashu Proof Refresh Tool")
    print("=" * 50)
    print(f"Backup directory: {backup_dir.absolute()}")

    # Initialize wallet
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
    ) as wallet:
        # Step 1: Backup current proofs
        backup_file, initial_balance = await backup_proofs(wallet, backup_dir)

        if backup_file is None:
            return

        # Ask for confirmation
        print(f"\n⚠️  This will refresh all {initial_balance} sats worth of proofs.")
        print("The old proofs will be invalidated and replaced with new ones.")
        response = input("\nContinue? (yes/no): ").strip().lower()

        if response != "yes":
            print("❌ Operation cancelled.")
            return

        # Step 2: Refresh proofs
        try:
            final_balance = await refresh_proofs(wallet)
            print("\n✅ Proof refresh completed successfully!")

            # Create recovery instructions if needed
            if final_balance < initial_balance:
                recovery_file = backup_dir / "RECOVERY_INSTRUCTIONS.txt"
                with open(recovery_file, "w") as f:
                    f.write("PROOF RECOVERY INSTRUCTIONS\n")
                    f.write("=========================\n\n")
                    f.write("A balance discrepancy was detected after proof refresh.\n")
                    f.write(f"Initial balance: {initial_balance} sats\n")
                    f.write(f"Final balance: {final_balance} sats\n")
                    f.write(f"Missing: {initial_balance - final_balance} sats\n\n")
                    f.write("Your original proofs are backed up in:\n")
                    f.write(f"{backup_file}\n\n")
                    f.write(
                        "To attempt recovery, you can try redeeming the backed up proofs.\n"
                    )
                print(f"\n📝 Recovery instructions written to: {recovery_file}")

        except Exception as e:
            print(f"\n❌ Error during refresh: {e}")
            print(f"Your original proofs are safely backed up in: {backup_file}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
