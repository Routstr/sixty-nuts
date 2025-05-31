import asyncio
from sixty_nuts.wallet import Wallet


async def main():
    async with Wallet(
        nsec="nsec1vl83hlk8ltz85002gr7qr8mxmsaf8ny8nee95z75vaygetnuvzuqqp5lrx"
    ) as wallet:
        # Check balance
        state = await wallet.fetch_wallet_state()
        print(f"Balance: {state.balance} sats")

        # Mint 10 sats
        invoice, confirmation = await wallet.mint_async(10, timeout=600)
        print(f"\nPay this invoice:\n{invoice}")

        await confirmation
        print("\n✓ Payment received!")

        # Send 5 sats
        token = await wallet.send(5)
        print(f"\nCashu token: {token}")


if __name__ == "__main__":
    asyncio.run(main())
