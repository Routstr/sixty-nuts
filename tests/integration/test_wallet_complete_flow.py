"""Complete wallet flow integration tests.

Tests the full wallet functionality against real mint and relay infrastructure.
Only runs when RUN_INTEGRATION_TESTS environment variable is set.
"""

import asyncio
import os
import pytest

from sixty_nuts.wallet import Wallet
from sixty_nuts.crypto import generate_privkey


# Skip all integration tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests only run when RUN_INTEGRATION_TESTS is set",
)


@pytest.fixture
def test_nsec():
    """Generate a test nostr private key."""
    return generate_privkey()


@pytest.fixture
def test_mint_urls():
    """Test mint URLs for integration tests."""
    return ["http://localhost:3338"]


@pytest.fixture
def test_relays():
    """Test relay URLs for integration tests."""
    return ["ws://localhost:8080"]


@pytest.fixture
async def wallet(test_nsec, test_mint_urls, test_relays):
    """Create a test wallet instance."""
    wallet = await Wallet.create(
        nsec=test_nsec,
        mint_urls=test_mint_urls,
        currency="sat",
        relays=test_relays,
        auto_init=False,  # Don't auto-initialize to avoid conflicts
    )

    # Initialize wallet explicitly
    await wallet.initialize_wallet(force=True)

    yield wallet

    # Cleanup
    await wallet.aclose()


class TestWalletBasicOperations:
    """Test basic wallet operations that require live services."""

    async def test_wallet_creation_and_initialization(
        self, test_nsec, test_mint_urls, test_relays
    ):
        """Test wallet creation and initialization with live relay connections."""
        wallet = await Wallet.create(
            nsec=test_nsec,
            mint_urls=test_mint_urls,
            currency="sat",
            relays=test_relays,
            auto_init=False,
        )

        # Check initial state
        balance = await wallet.get_balance(check_proofs=False)
        assert balance == 0

        # Initialize wallet (requires relay connection)
        initialized = await wallet.initialize_wallet(force=True)
        assert initialized is True

        # Give some time for the wallet event to propagate
        await asyncio.sleep(2)

        # Check wallet event exists (requires relay connection)
        exists, event = await wallet.check_wallet_event_exists()
        if not exists:
            # Try one more time in case of relay timing issues
            await asyncio.sleep(3)
            exists, event = await wallet.check_wallet_event_exists()

        assert exists is True, "Wallet event should exist after initialization"
        assert event is not None

        await wallet.aclose()

    async def test_balance_check_empty_wallet(self, wallet):
        """Test balance checking on empty wallet."""
        balance = await wallet.get_balance()
        assert balance == 0

    async def test_mint_quote_creation(self, wallet):
        """Test creating mint quotes (requires mint API)."""
        invoice, quote_id = await wallet.create_quote(50)

        assert invoice.startswith("lnbc")  # BOLT11 invoice
        assert len(quote_id) > 0
        # Mint might return different formats: 50n, 500n, 50000m, etc.
        assert any(
            x in invoice for x in ["50n", "500n", "50000m"]
        )  # 50 sats in various formats


class TestWalletMinting:
    """Test wallet minting operations that require mint API."""

    async def test_mint_async_flow(self, wallet):
        """Test asynchronous minting flow with auto-paying test mint."""
        # Create invoice - test mint should auto-pay
        invoice, task = await wallet.mint_async(25)
        print(f"Created invoice: {invoice}")

        # Wait for the auto-payment to complete
        try:
            # Give reasonable time for auto-payment
            paid = await asyncio.wait_for(task, timeout=30.0)
            assert paid is True, "Invoice should be auto-paid by test mint"

            # Give time for token events to propagate to relay
            await asyncio.sleep(2)

            # Verify balance increased
            balance = await wallet.get_balance()
            assert balance >= 25, f"Balance should be at least 25 sats, got {balance}"

        except asyncio.TimeoutError:
            # If timeout, cancel the task and fail
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            pytest.fail(
                "Auto-payment timeout - test mint may not be auto-paying invoices"
            )


class TestWalletTransactions:
    """Test wallet transaction operations that require mint validation."""

    async def test_send_insufficient_balance(self, wallet):
        """Test send with insufficient balance (should fail gracefully)."""
        # Empty wallet should fail to send
        with pytest.raises(Exception) as exc_info:
            await wallet.send(30)

        assert "insufficient" in str(exc_info.value).lower()

    async def test_complete_mint_send_redeem_flow(self, wallet):
        """Test complete end-to-end flow: mint → send → redeem.

        This test would have caught the balance calculation bug since it exercises
        the full proof swapping logic with actual mint validation.
        """
        # 1. Start with empty wallet
        initial_balance = await wallet.get_balance()
        assert initial_balance == 0

        # 2. Mint some tokens (fund the wallet)
        mint_amount = 100
        invoice, task = await wallet.mint_async(mint_amount)
        print(f"Created invoice for {mint_amount} sats: {invoice}")

        # Wait for auto-payment
        paid = await asyncio.wait_for(task, timeout=30.0)
        assert paid is True, "Invoice should be auto-paid by test mint"

        # Give time for token events to propagate to relay
        await asyncio.sleep(2)

        # Verify wallet is funded
        funded_balance = await wallet.get_balance()
        assert funded_balance >= mint_amount, (
            f"Expected at least {mint_amount}, got {funded_balance}"
        )
        print(f"Wallet funded with {funded_balance} sats")

        # Debug: Check wallet state details
        state = await wallet.fetch_wallet_state()
        print(
            f"\nDEBUG after mint: {len(state.proofs)} proofs, total {sum(p['amount'] for p in state.proofs)} sats"
        )
        for p in state.proofs:
            print(f"  - {p['amount']} sats")

        # 3. Send some tokens
        send_amount = 25
        token = await wallet.send(send_amount)
        assert token.startswith("cashu"), "Should receive valid Cashu token"
        print(f"\nCreated token for {send_amount} sats")

        # Check balance after send
        await asyncio.sleep(2)  # Give time for events to propagate
        state = await wallet.fetch_wallet_state()
        balance_after_send = state.balance
        print(
            f"\nDEBUG after send: {len(state.proofs)} proofs, total {balance_after_send} sats"
        )
        for p in state.proofs:
            print(f"  - {p['amount']} sats")
        print(
            f"Lost {funded_balance - balance_after_send - send_amount} sats in fees on send"
        )

        # 4. Redeem the token (simulating receiving it)
        print("\nRedeeming the sent token...")
        redeemed_amount, unit = await wallet.redeem(token)
        print(
            f"Redeemed {redeemed_amount} {unit} (fees deducted from original {send_amount})"
        )

        # Give time for events to propagate
        await asyncio.sleep(2)

        # 5. Verify final balance (accounting for fees)
        state = await wallet.fetch_wallet_state()
        final_balance = state.balance
        print(
            f"\nDEBUG after redeem: {len(state.proofs)} proofs, total {final_balance} sats"
        )
        for p in state.proofs:
            print(f"  - {p['amount']} sats")

        fees_paid = funded_balance - final_balance
        print(f"Total lost to fees: {fees_paid} sats")

        # Basic sanity checks
        assert final_balance > 0, "Should have positive balance"
        # If fees were paid, balance should be less than funded
        # If no fees (test mint might not have fees), balance could equal funded
        assert final_balance <= funded_balance, (
            "Balance should not exceed funded amount"
        )

        # The exact fee amount depends on mint configuration
        # With no fees: final_balance = funded_balance
        # With fees: final_balance < funded_balance
        if fees_paid > 0:
            print(f"✅ Paid {fees_paid} sats in fees")
        else:
            print("ℹ️  No fees charged (mint may not have fees configured)")

        print("✅ Complete mint → send → redeem flow successful!")

    async def test_multiple_send_operations(self, wallet):
        """Test multiple send operations to verify fee handling."""
        # Fund wallet
        mint_amount = 200
        invoice, task = await wallet.mint_async(mint_amount)
        paid = await asyncio.wait_for(task, timeout=30.0)
        assert paid is True

        # Give time for token events to propagate to relay
        await asyncio.sleep(2)

        initial_balance = await wallet.get_balance()
        assert initial_balance >= mint_amount

        # Perform a few small sends
        send_amounts = [10, 5, 20, 1]
        tokens = []

        for amount in send_amounts:
            try:
                print(f"\nSending {amount} sats...")
                balance_before = await wallet.get_balance()
                token = await wallet.send(amount)
                tokens.append((amount, token))

                # Give time for events to propagate
                await asyncio.sleep(1)

                balance_after = await wallet.get_balance()
                print(f"Balance: {balance_before} → {balance_after} (sent {amount})")

                # Balance should decrease by at least the sent amount
                assert balance_after <= balance_before - amount, (
                    f"Balance should decrease by at least {amount}"
                )
            except Exception as e:
                print(f"Failed to send {amount} sats: {e}")
                # Continue with other amounts

        # Redeem all tokens that were successfully sent
        total_redeemed = 0
        for expected_amount, token in tokens:
            try:
                redeemed_amount, unit = await wallet.redeem(token)
                total_redeemed += redeemed_amount
                print(f"Redeemed {redeemed_amount} {unit}")
            except Exception as e:
                print(f"Failed to redeem token: {e}")

        # Final checks
        final_balance = await wallet.get_balance()
        print(
            f"\nInitial: {initial_balance}, Final: {final_balance}, Redeemed: {total_redeemed}"
        )

        # Basic sanity checks
        assert final_balance > 0, "Should have positive balance"
        assert len(tokens) > 0, "Should have successfully sent at least one token"
        assert total_redeemed > 0, (
            "Should have successfully redeemed at least one token"
        )

        print("✅ Multiple send operations test completed!")


class TestWalletRelayOperations:
    """Test wallet operations that require relay connections."""

    async def test_relay_connections(self, wallet):
        """Test relay connection establishment."""
        # Wallet should have relay connections from initialization
        assert len(wallet.relays) > 0
        assert wallet.relay_manager is not None

        # Test that we can actually connect
        relays = await wallet.relay_manager.get_relay_connections()
        assert len(relays) > 0, "Should connect to at least one relay"

    async def test_fetch_spending_history(self, wallet):
        """Test fetching spending history from relays."""
        history = await wallet.fetch_spending_history()
        assert isinstance(history, list)
        # Fresh wallet should have minimal history

    async def test_count_token_events(self, wallet):
        """Test counting token events from relays."""
        count = await wallet.count_token_events()
        assert count >= 0  # Should be 0 for fresh wallet

    async def test_cleanup_wallet_state_dry_run(self, wallet):
        """Test wallet state cleanup (requires relay connection to fetch events)."""
        stats = await wallet.cleanup_wallet_state(dry_run=True)

        assert "total_events" in stats
        assert "valid_events" in stats
        assert "undecryptable_events" in stats
        assert "empty_events" in stats
        assert "balance" in stats

        # Should not have made any changes in dry run
        assert stats["events_consolidated"] == 0
        assert stats["events_marked_superseded"] == 0


class TestWalletMintIntegration:
    """Test operations that require actual mint API validation."""

    async def test_get_keysets_from_mint(self, wallet):
        """Test getting keysets from real mint."""
        mint = wallet._get_mint(wallet.mint_urls[0])
        keysets_resp = await mint.get_keysets()

        assert "keysets" in keysets_resp
        keysets = keysets_resp["keysets"]
        assert len(keysets) > 0, "Mint should have at least one keyset"

        # Verify keyset structure
        for keyset in keysets:
            assert "id" in keyset
            assert "unit" in keyset
            assert keyset["unit"] == "sat"  # Test mint uses sats

    async def test_get_keys_from_mint(self, wallet):
        """Test getting public keys from real mint."""
        mint = wallet._get_mint(wallet.mint_urls[0])

        # Get keysets first
        keysets_resp = await mint.get_keysets()
        keysets = keysets_resp["keysets"]

        if keysets:
            keyset_id = keysets[0]["id"]
            keys_resp = await mint.get_keys(keyset_id)

            assert "keysets" in keys_resp
            mint_keysets = keys_resp["keysets"]

            # Find our keyset
            for ks in mint_keysets:
                if ks["id"] == keyset_id:
                    assert "keys" in ks
                    keys = ks["keys"]
                    assert isinstance(keys, dict)
                    assert len(keys) > 0, "Keyset should have public keys"
                    break


class TestWalletProofValidation:
    """Test proof validation against real mint."""

    async def test_proof_state_checking_empty(self, wallet):
        """Test proof state checking with empty proofs list."""
        mint = wallet._get_mint(wallet.mint_urls[0])

        # Empty Y values should return empty states
        state_response = await mint.check_state(Ys=[])
        assert "states" in state_response
        assert len(state_response["states"]) == 0

    async def test_compute_proof_y_values(self, wallet):
        """Test Y value computation for proof validation."""
        from sixty_nuts.types import ProofDict

        mock_proofs = [
            ProofDict(
                id="test1",
                amount=10,
                secret="dGVzdA==",  # base64 "test"
                C="02a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                mint="test",
            ),
        ]

        y_values = wallet._compute_proof_y_values(mock_proofs)
        assert len(y_values) == 1
        assert len(y_values[0]) == 66  # 33 bytes * 2 hex chars = 66 chars
        assert all(c in "0123456789abcdefABCDEF" for c in y_values[0]), (
            "Should be valid hex"
        )


class TestWalletErrorHandling:
    """Test wallet error handling with live services."""

    async def test_insufficient_balance_error(self, wallet):
        """Test insufficient balance error handling."""
        balance = await wallet.get_balance()
        assert balance == 0

        # Try to send more than balance
        with pytest.raises(Exception) as exc_info:
            await wallet.send(100)

        assert "insufficient" in str(exc_info.value).lower()


if __name__ == "__main__":
    # Allow running this file directly for debugging
    import sys

    if not os.getenv("RUN_INTEGRATION_TESTS"):
        print("Set RUN_INTEGRATION_TESTS=1 to run integration tests")
        sys.exit(1)

    # Run a simple test
    async def main():
        nsec = generate_privkey()
        wallet = await Wallet.create(
            nsec=nsec,
            mint_urls=["http://localhost:3338"],
            currency="sat",
            relays=["ws://localhost:8080"],
            auto_init=False,
        )

        print("✅ Wallet created successfully")

        await wallet.initialize_wallet(force=True)
        print("✅ Wallet initialized")

        balance = await wallet.get_balance()
        print(f"✅ Balance: {balance} sats")

        await wallet.aclose()
        print("✅ Integration test completed")

    asyncio.run(main())
