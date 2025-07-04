"""Centralized configuration for all integration tests.

This module provides fixtures and configuration that apply to all integration tests.
It ensures that:
1. Tests only use the appropriate test mints (localhost for Docker, testnut.cashu.space otherwise)
2. Environment variables don't interfere with test mint selection
3. Wallet instances are properly isolated for testing
"""

import os
import pytest
import shutil
from pathlib import Path

from sixty_nuts.wallet import Wallet
from sixty_nuts.crypto import generate_privkey


# Skip all integration tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests only run when RUN_INTEGRATION_TESTS is set",
)


def get_relay_wait_time(base_seconds: float = 2.0) -> float:
    """Get appropriate wait time based on service type.

    Args:
        base_seconds: Base wait time for local services

    Returns:
        Wait time in seconds (longer for public relays due to rate limiting)
    """
    if os.getenv("USE_LOCAL_SERVICES"):
        return base_seconds
    else:
        # Public relays need longer waits due to rate limiting
        return base_seconds * 3.0  # 3x longer for public relays


@pytest.fixture
def test_nsec():
    """Generate a test nostr private key."""
    return generate_privkey()


@pytest.fixture
def test_mint_urls():
    """Test mint URLs for integration tests.

    Uses local Docker mint when USE_LOCAL_SERVICES is set,
    otherwise uses public test mint.

    Returns only the appropriate test mint URL to prevent wallet
    from accumulating other mints.
    """
    if os.getenv("USE_LOCAL_SERVICES"):
        return ["http://localhost:3338"]
    else:
        return ["https://testnut.cashu.space"]


@pytest.fixture
def test_relays():
    """Test relay URLs for integration tests.

    Uses local Docker relay when USE_LOCAL_SERVICES is set,
    otherwise uses public relays.
    """
    if os.getenv("USE_LOCAL_SERVICES"):
        return ["ws://localhost:8080"]
    else:
        return [
            "wss://relay.damus.io",
            "wss://relay.nostr.band",
        ]


@pytest.fixture(autouse=True)
def clean_proof_backups():
    """Clean proof backups before each test to ensure isolated state.

    This prevents tests from recovering proofs from previous runs,
    which would cause unexpected non-zero balances.
    """
    backup_dir = Path.cwd() / "proof_backups"
    test_backup_dir = Path.cwd() / "test_proof_backups"

    # Move existing backups to temporary location if they exist
    if backup_dir.exists():
        if test_backup_dir.exists():
            shutil.rmtree(test_backup_dir)
        shutil.move(str(backup_dir), str(test_backup_dir))

    yield

    # Restore original backups after test
    if test_backup_dir.exists():
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.move(str(test_backup_dir), str(backup_dir))


@pytest.fixture
async def wallet(test_nsec, test_mint_urls, test_relays):
    """Create a test wallet instance with controlled mint configuration.

    This fixture ensures that:
    1. Only test mints are used (no environment mints or popular mints)
    2. Wallet is properly initialized for testing
    3. Resources are cleaned up after test
    """
    # Store original environment to restore later
    original_env_mints = os.environ.get("CASHU_MINTS")

    # Clear environment mints to prevent interference
    if "CASHU_MINTS" in os.environ:
        del os.environ["CASHU_MINTS"]

    try:
        # Create wallet with explicit test configuration
        wallet = await Wallet.create(
            nsec=test_nsec,
            mint_urls=test_mint_urls,
            currency="sat",
            relays=test_relays,
            auto_init=False,  # Don't auto-initialize to avoid conflicts
        )

        # Force wallet to use ONLY our test mints
        # This overrides any mints from environment or wallet events
        wallet.mint_urls = set(test_mint_urls)

        # Re-initialize event manager with controlled mint set
        await wallet._initialize_event_manager()

        # Override wallet methods to disable local backup checking during tests
        original_fetch_wallet_state = wallet.fetch_wallet_state

        async def fetch_wallet_state_no_backups(**kwargs):
            kwargs["check_local_backups"] = False
            return await original_fetch_wallet_state(**kwargs)

        wallet.fetch_wallet_state = fetch_wallet_state_no_backups

        # Initialize wallet explicitly
        await wallet.initialize_wallet(force=True)

        yield wallet

    finally:
        # Cleanup
        await wallet.aclose()

        # Restore original environment
        if original_env_mints is not None:
            os.environ["CASHU_MINTS"] = original_env_mints


@pytest.fixture
async def clean_wallet(test_nsec, test_mint_urls, test_relays):
    """Create a clean test wallet instance without initialization.

    This fixture is for tests that need to control wallet initialization
    themselves or test initialization behavior.
    """
    # Store original environment to restore later
    original_env_mints = os.environ.get("CASHU_MINTS")

    # Clear environment mints to prevent interference
    if "CASHU_MINTS" in os.environ:
        del os.environ["CASHU_MINTS"]

    try:
        # Create wallet with explicit test configuration
        wallet = await Wallet.create(
            nsec=test_nsec,
            mint_urls=test_mint_urls,
            currency="sat",
            relays=test_relays,
            auto_init=False,
        )

        # Force wallet to use ONLY our test mints
        wallet.mint_urls = set(test_mint_urls)

        # Re-initialize event manager with controlled mint set
        await wallet._initialize_event_manager()

        # Override wallet methods to disable local backup checking during tests
        original_fetch_wallet_state = wallet.fetch_wallet_state

        async def fetch_wallet_state_no_backups(**kwargs):
            kwargs["check_local_backups"] = False
            return await original_fetch_wallet_state(**kwargs)

        wallet.fetch_wallet_state = fetch_wallet_state_no_backups

        yield wallet

    finally:
        # Cleanup
        await wallet.aclose()

        # Restore original environment
        if original_env_mints is not None:
            os.environ["CASHU_MINTS"] = original_env_mints


# Re-export commonly used functions for convenience
__all__ = [
    "get_relay_wait_time",
    "test_nsec",
    "test_mint_urls",
    "test_relays",
    "wallet",
    "clean_wallet",
]
