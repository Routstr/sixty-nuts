"""Tests for TempWallet functionality."""

import pytest
from sixty_nuts import TempWallet


@pytest.mark.asyncio
async def test_temp_wallet_creates_random_keys() -> None:
    """Test that TempWallet generates random keys each time."""
    # Create two temp wallets
    wallet1 = TempWallet(currency="sat")
    wallet2 = TempWallet(currency="sat")

    # Get their public keys
    pubkey1 = wallet1._get_pubkey()
    pubkey2 = wallet2._get_pubkey()

    # Verify they have different keys
    assert pubkey1 != pubkey2
    assert len(pubkey1) == 64  # 32 bytes hex encoded
    assert len(pubkey2) == 64

    # Clean up
    await wallet1.aclose()
    await wallet2.aclose()


@pytest.mark.asyncio
async def test_temp_wallet_no_nsec_required() -> None:
    """Test that TempWallet can be created without providing an NSEC."""
    # Create wallet without any NSEC parameter
    wallet = TempWallet(
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        currency="sat",
    )

    # Verify it has a valid public key
    pubkey = wallet._get_pubkey()
    assert pubkey is not None
    assert len(pubkey) == 64

    # Verify it has an nsec (internally generated)
    assert wallet.nsec is not None
    assert wallet.nsec.startswith("nsec")

    await wallet.aclose()


@pytest.mark.asyncio
async def test_temp_wallet_factory_method() -> None:
    """Test the async factory method for TempWallet."""
    # Create wallet using factory method
    wallet = await TempWallet.create(
        mint_urls=["https://mint.minibits.cash/Bitcoin"],
        currency="sat",
        auto_init=False,  # Don't connect to relays
    )

    # Verify it works
    assert wallet._get_pubkey() is not None

    await wallet.aclose()


@pytest.mark.asyncio
async def test_temp_wallet_context_manager() -> None:
    """Test TempWallet works with async context manager."""
    async with TempWallet(currency="sat") as wallet:
        # Verify wallet is functional
        pubkey = wallet._get_pubkey()
        assert pubkey is not None
        assert len(pubkey) == 64

    # Wallet should be closed after context exit
    # (no direct way to test this, but no errors is good)
