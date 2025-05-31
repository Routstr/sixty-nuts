import asyncio
import os
import secrets
from pathlib import Path

import pytest

from sixty_nuts.wallet import Wallet


def generate_random_nsec() -> str:
    """Generate a random nsec for testing."""
    private_key_bytes = secrets.token_bytes(32)
    return private_key_bytes.hex()


async def test_wallet_token_cycle():
    """Test claiming a token, checking balance, and creating a new token."""
    # Read the existing token from .cashu file
    token_file = Path(__file__).parent / ".cashu"
    if not token_file.exists():
        pytest.skip(".cashu file not found")

    token = token_file.read_text().strip()
    if not token:
        pytest.skip(".cashu file is empty")

    # Generate a random nsec for this test
    nsec = generate_random_nsec()

    # Create wallet instance
    async with Wallet(nsec=nsec) as wallet:
        # Get initial state (should be empty)
        initial_state = await wallet.fetch_wallet_state()
        assert initial_state.balance == 0, "New wallet should have 0 balance"

        # Redeem the token
        print(f"Redeeming token from .cashu file...")
        await wallet.redeem(token)

        # Small delay to ensure event propagation
        await asyncio.sleep(1)

        # Check balance after redemption
        state = await wallet.fetch_wallet_state()
        print(f"Balance after redemption: {state.balance} sats")
        assert state.balance > 0, "Balance should be positive after redemption"

        # Remember the balance for creating new token
        redeemed_amount = state.balance

        # Create a new token with the full balance
        print(f"Creating new token for {redeemed_amount} sats...")
        new_token = await wallet.send(redeemed_amount)

        # Verify balance is now 0
        await asyncio.sleep(1)
        final_state = await wallet.fetch_wallet_state()
        assert final_state.balance == 0, "Balance should be 0 after sending all funds"

        # Save the new token back to the file
        token_file.write_text(new_token)
        print(f"Saved new token to .cashu file")

        return redeemed_amount


@pytest.mark.asyncio
async def test_wallet_integration():
    """Run the wallet integration test."""
    amount = await test_wallet_token_cycle()
    assert amount > 0, "Should have successfully processed a positive amount"


if __name__ == "__main__":
    # Allow running directly for debugging
    asyncio.run(test_wallet_token_cycle())
