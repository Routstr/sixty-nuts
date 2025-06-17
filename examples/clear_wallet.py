import asyncio
import os
from dotenv import load_dotenv
from sixty_nuts.wallet import Wallet


async def clear_wallet():
    """Clear all tokens from a wallet."""
    load_dotenv()
    nsec = os.getenv("NSEC")
    if not nsec:
        print("Error: NSEC environment variable not set. Please create a .env file.")
        return

    async with Wallet(
        nsec=nsec,
    ) as wallet:
        print("Clearing wallet tokens...")

        # Check current balance
        balance = await wallet.get_balance()
        print(f"Current balance: {balance} sats")

        if balance == 0:
            print("Wallet is already empty")
            return

        # Get wallet state, which includes proof_to_event_id mapping
        state = await wallet.fetch_wallet_state()

        # Collect event IDs to delete from the proofs
        event_ids_to_delete = set()
        if state.proofs:
            for proof in state.proofs:
                proof_id = f"{proof['secret']}:{proof['C']}" # Reconstruct proof_id for lookup
                if proof_id in state.proof_to_event_id:
                    event_ids_to_delete.add(state.proof_to_event_id[proof_id])

        if not event_ids_to_delete:
            print("No token events found to delete from current proofs.")
            return

        print(f"Deleting {len(event_ids_to_delete)} unique token events...")
        for event_id in event_ids_to_delete:
            try:
                await wallet.delete_token_event(event_id)
                await asyncio.sleep(0.5)  # Rate limiting
            except Exception as e:
                print(f"Error deleting event {event_id}: {e}")

        # Verify final state
        await asyncio.sleep(2)  # Wait for propagation
        final_balance = await wallet.get_balance()
        print(f"Final balance: {final_balance} sats")


if __name__ == "__main__":
    asyncio.run(clear_wallet())
