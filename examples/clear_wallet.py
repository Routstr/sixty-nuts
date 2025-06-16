import asyncio
from sixty_nuts.wallet import Wallet


async def clear_wallet():
    """Clear all tokens from a wallet."""
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx",
    ) as wallet:
        print("Clearing wallet tokens...")

        # Check current balance
        balance = await wallet.get_balance()
        print(f"Current balance: {balance} sats")

        if balance == 0:
            print("Wallet is already empty")
            return

        # Get all token events
        state = await wallet.fetch_wallet_state()
        token_events = state.token_events

        if not token_events:
            print("No token events found")
            return

        # Delete all token events
        print(f"Deleting {len(token_events)} token events...")
        for event in token_events:
            try:
                await wallet.delete_token_event(event["id"])
                await asyncio.sleep(0.5)  # Rate limiting
            except Exception as e:
                print(f"Error deleting event: {e}")

        # Verify final state
        await asyncio.sleep(2)  # Wait for propagation
        final_balance = await wallet.get_balance()
        print(f"Final balance: {final_balance} sats")


if __name__ == "__main__":
    asyncio.run(clear_wallet())
