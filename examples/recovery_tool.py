#!/usr/bin/env python3
"""Recovery tool for missing proofs due to wallet bugs."""

import asyncio
import json
from sixty_nuts.wallet import Wallet


async def check_missing_proofs():
    """Check for missing proofs and potential recovery options."""
    nsec = input("Enter your NSEC: ").strip()

    print("🔍 Scanning for missing proofs...")
    print("=" * 60)

    async with Wallet(nsec=nsec) as wallet:
        # 1. Get current state
        print("\n📊 Current Wallet State:")
        validated_state = await wallet.fetch_wallet_state(check_proofs=True)
        unvalidated_state = await wallet.fetch_wallet_state(check_proofs=False)

        print(f"  Validated balance: {validated_state.balance} sats")
        print(f"  Unvalidated balance: {unvalidated_state.balance} sats")
        print(
            f"  Difference: {unvalidated_state.balance - validated_state.balance} sats"
        )

        if unvalidated_state.balance > validated_state.balance:
            print(
                f"  ⚠️  You have {unvalidated_state.balance - validated_state.balance} sats in potentially spent proofs"
            )

        # 2. Check relay queue for pending proofs
        print("\n📤 Checking Relay Queue:")
        if wallet._use_queued_relays and wallet.relay_pool:
            pending_proofs = wallet.relay_pool.get_pending_proofs()
            if pending_proofs:
                pending_total = sum(p.get("amount", 0) for p in pending_proofs)
                print(
                    f"  Found {len(pending_proofs)} pending proofs worth {pending_total} sats"
                )
                print(
                    f"  💡 These proofs are queued for publishing - they may appear soon!"
                )

                # Show breakdown
                for i, proof in enumerate(pending_proofs[:5]):  # Show first 5
                    print(f"    Proof {i + 1}: {proof.get('amount', 0)} sats")
                if len(pending_proofs) > 5:
                    print(f"    ... and {len(pending_proofs) - 5} more")
            else:
                print(f"  No pending proofs found in relay queue")
        else:
            print(f"  Not using queued relays")

        # 3. Fetch raw events from all relays to check for inconsistencies
        print("\n📡 Checking All Relays for Events:")
        relays = await wallet._get_relay_connections()
        all_token_events = []

        for i, relay in enumerate(relays):
            try:
                print(f"  Checking relay {i + 1}: {relay.url}")

                # Fetch token events
                token_events = await relay.fetch_events(
                    authors=[wallet._get_pubkey()],
                    kinds=[7375],  # Token events
                    limit=50,
                )

                print(f"    Found {len(token_events)} token events")
                all_token_events.extend(token_events)

            except Exception as e:
                print(f"    ❌ Error: {e}")

        # 4. Analyze events for proof content
        print(f"\n🔍 Analyzing {len(all_token_events)} total events:")

        # Deduplicate events by ID
        unique_events = {}
        for event in all_token_events:
            unique_events[event["id"]] = event

        print(f"  Unique events: {len(unique_events)}")

        # Parse events to find proofs
        all_found_proofs = []
        parsing_errors = 0

        for event_id, event in unique_events.items():
            try:
                # Try to decrypt content
                decrypted = wallet._nip44_decrypt(event["content"])
                token_data = json.loads(decrypted)

                proofs = token_data.get("proofs", [])
                mint_url = token_data.get("mint", "unknown")
                total_amount = sum(p.get("amount", 0) for p in proofs)

                print(
                    f"    Event {event_id[:8]}...: {len(proofs)} proofs, {total_amount} sats from {mint_url}"
                )

                # Check if this event should be deleted
                deleted_ids = token_data.get("del", [])
                if deleted_ids:
                    print(f"      Deletes: {len(deleted_ids)} old events")

                all_found_proofs.extend(proofs)

            except Exception as e:
                parsing_errors += 1
                print(f"    ❌ Parse error for {event_id[:8]}...: {e}")

        if parsing_errors > 0:
            print(f"  ⚠️  {parsing_errors} events couldn't be parsed")

        # 5. Compare found proofs vs local state
        total_found_amount = sum(p.get("amount", 0) for p in all_found_proofs)
        print(f"\n📊 Proof Analysis:")
        print(
            f"  Total proofs found on relays: {len(all_found_proofs)} worth {total_found_amount} sats"
        )
        print(
            f"  Local unvalidated state: {len(unvalidated_state.proofs)} worth {unvalidated_state.balance} sats"
        )
        print(
            f"  Local validated state: {len(validated_state.proofs)} worth {validated_state.balance} sats"
        )

        # 6. Recovery recommendations
        print(f"\n🔧 Recovery Recommendations:")

        if pending_proofs:
            print(
                f"  ✅ Wait for relay queue processing ({sum(p.get('amount', 0) for p in pending_proofs)} sats pending)"
            )

        if total_found_amount > unvalidated_state.balance:
            difference = total_found_amount - unvalidated_state.balance
            print(
                f"  💡 Found {difference} extra sats on relays - try refreshing wallet state"
            )

        if unvalidated_state.balance > validated_state.balance:
            spent_amount = unvalidated_state.balance - validated_state.balance
            print(
                f"  🧹 Run cleanup tool to remove {spent_amount} sats in spent proofs"
            )
            print(f"     Command: python examples/cleanup_spent_proofs.py")

        if parsing_errors > 0:
            print(f"  🔑 Some events couldn't be decrypted - check NSEC key")

        print(f"\n📋 Next Steps:")
        print(f"  1. Wait 30 seconds for relay synchronization")
        print(f"  2. Check balance again: nuts balance --nostr-debug")
        print(f"  3. If still missing, run: python examples/refresh_proofs.py")
        print(f"  4. Contact support if proofs are permanently lost")


async def main():
    """Main recovery function."""
    try:
        await check_missing_proofs()
    except KeyboardInterrupt:
        print("\n\nRecovery scan cancelled.")
    except Exception as e:
        print(f"\n❌ Recovery scan failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
