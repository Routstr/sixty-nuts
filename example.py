import asyncio
from sixty_nuts.wallet import Wallet


async def main():
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        # Check initial balance
        state = await wallet.fetch_wallet_state()
        print(f"Initial balance: {state.balance} sats")

        # Mint 10 sats
        invoice, confirmation = await wallet.mint_async(10, timeout=600)
        print(f"\nPay this invoice:\n{invoice}")

        await confirmation
        print("\n✓ Payment received!")

        # Small delay to ensure event propagation
        await asyncio.sleep(1)

        # Check balance after minting
        state = await wallet.fetch_wallet_state()
        print(f"Balance: {state.balance} sats")
        print(f"Number of proofs: {len(state.proofs)}")
        print(f"Proofs: {state.proofs}")

        if state.balance >= 5:
            # Send 5 sats
            try:
                token = await wallet.send(5)
                print(f"\nCashu token: {token}")

                # Check final balance
                state = await wallet.fetch_wallet_state()
                print(f"\nFinal balance: {state.balance} sats")
            except Exception as e:
                print(f"\nError sending tokens: {e}")
        else:
            print("Insufficient balance to send tokens")


if __name__ == "__main__":
    asyncio.run(main())
