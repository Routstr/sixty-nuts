import asyncio
from sixty_nuts.wallet import Wallet


async def clear_wallet():
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        # Fetch current state
        state = await wallet.fetch_wallet_state()
        print(f"Current balance: {state.balance} sats")
        print(f"Number of proofs: {len(state.proofs)}")

        if state.proofs:
            # Delete all token events
            event_ids_to_delete = set()
            proof_to_event_id = state.proof_to_event_id or {}

            for proof in state.proofs:
                proof_id = f"{proof['secret']}:{proof['C']}"
                if proof_id in proof_to_event_id:
                    event_ids_to_delete.add(proof_to_event_id[proof_id])

            print(f"\nDeleting {len(event_ids_to_delete)} token events...")
            for event_id in event_ids_to_delete:
                await wallet.delete_token_event(event_id)
                print(f"  Deleted event {event_id[:16]}...")

            # Small delay to ensure events propagate
            await asyncio.sleep(1)

            # Check balance again
            state = await wallet.fetch_wallet_state()
            print(f"\nFinal balance: {state.balance} sats")
            print(f"Number of proofs: {len(state.proofs)}")
        else:
            print("Wallet is already empty")


if __name__ == "__main__":
    asyncio.run(clear_wallet())
