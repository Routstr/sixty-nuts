import asyncio
import os
import sys
from dotenv import load_dotenv
from sixty_nuts.wallet import Wallet


async def send_to_address(wallet: Wallet, address: str, amount: int):
    """Send tokens to a Lightning Address."""
    print(f"Sending {amount} sats to {address}...")

    try:
        # Send to Lightning Address (handles LNURL automatically)
        actual_paid = await wallet.send_to_lnurl(address, amount)

        print(f"✅ Successfully sent {actual_paid} sats!")
        print("   (After estimated Lightning fees)")

        # Show remaining balance
        balance = await wallet.get_balance()
        print(f"\nRemaining balance: {balance} sats")

    except Exception as e:
        print(f"❌ Failed to send: {e}")
        raise


async def main():
    """Main example."""
    if len(sys.argv) < 3:
        print("Usage: python send_to_lightning_address.py <lightning_address> <amount>")
        print("Example: python send_to_lightning_address.py user@getalby.com 100")
        return

    address = sys.argv[1]
    amount = int(sys.argv[2])

    load_dotenv()
    nsec = os.getenv("NSEC")
    if not nsec:
        print("Error: NSEC environment variable not set. Please create a .env file.")
        return

    # Initialize wallet
    async with Wallet(
        nsec=nsec,
    ) as wallet:
        # Check balance first
        balance = await wallet.get_balance()
        print(f"Current balance: {balance} sats")

        if balance < amount:
            print(f"❌ Insufficient balance! Need {amount}, have {balance}")
            return

        await send_to_address(wallet, address, amount)


if __name__ == "__main__":
    asyncio.run(main())
