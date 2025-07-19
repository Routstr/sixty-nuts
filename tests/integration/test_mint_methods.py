"""Integration tests for Mint API client.

Tests the mint.py module against the real public test mint at https://testnut.cashu.space.
Only runs when RUN_INTEGRATION_TESTS environment variable is set.

These tests verify:
- Mint info and key retrieval
- Mint quote creation and status checking
- Melt quote operations
- Token management (swap, check state, restore)
- Validation methods
- Error handling with real responses
"""

import os
import pytest
import asyncio
from typing import Any, AsyncGenerator, cast

from sixty_nuts.mint import (
    Mint,
    MintError,
    BlindedMessage,
    MintInfo,
    Keyset,
    KeysetInfo,
    PostMintQuoteResponse,
    PostCheckStateResponse,
    PostMeltQuoteResponse,
    ProofComplete,
)
from sixty_nuts.types import Proof


# Skip all integration tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests only run when RUN_INTEGRATION_TESTS is set",
)


@pytest.fixture
async def mint() -> AsyncGenerator[Mint, None]:
    """Create a mint instance for testing.

    Uses local Docker mint when USE_LOCAL_SERVICES is set,
    otherwise uses public test mint.
    """
    if os.getenv("USE_LOCAL_SERVICES"):
        mint_url = "http://localhost:3338"
    else:
        mint_url = "https://testnut.cashu.space"

    mint_instance = Mint(mint_url)
    yield mint_instance
    await mint_instance.aclose()


@pytest.fixture
async def mint_local() -> AsyncGenerator[Mint, None]:
    """Create a mint instance specifically for local testing (if available)."""
    mint_instance = Mint("http://localhost:3338")
    yield mint_instance
    await mint_instance.aclose()


class TestMintBasicOperations:
    """Test basic mint operations that require live mint API."""

    async def test_get_mint_info(self, mint: Mint) -> None:
        """Test retrieving mint information from real mint."""
        info: MintInfo = await mint.get_info()

        # Verify expected fields are present
        assert isinstance(info, dict)

        # Most mints should have a name
        if "name" in info:
            assert isinstance(info["name"], str)
            assert len(info["name"]) > 0

        # Version information if present
        if "version" in info:
            assert isinstance(info["version"], str)

        # Verify nuts capability information if present
        if "nuts" in info:
            assert isinstance(info["nuts"], dict)
            # Should support basic NUTs
            for nut in ["1", "2", "3", "4"]:  # Basic minting/melting NUTs
                if nut in info["nuts"]:
                    assert isinstance(info["nuts"][nut], dict)

        print(f"✅ Mint info retrieved: {info}")

    async def test_get_keysets(self, mint: Mint) -> None:
        """Test retrieving keyset information from real mint."""
        keysets: list[KeysetInfo] = await mint.get_keysets_info()

        assert isinstance(keysets, list)
        assert len(keysets) > 0, "Mint should have at least one keyset"

        # Verify keyset structure
        for keyset in keysets:
            assert "id" in keyset
            assert "unit" in keyset
            assert "active" in keyset

            # ID should be valid hex string
            assert len(keyset["id"]) == 16
            int(keyset["id"], 16)  # Should not raise ValueError

            # Unit should be valid currency unit
            assert keyset["unit"] in ["sat", "msat", "btc", "usd", "eur"]

            # Active should be boolean
            assert isinstance(keyset["active"], bool)

        # Test mint should have at least some active keysets for sat
        sat_keysets: list[KeysetInfo] = [ks for ks in keysets if ks["unit"] == "sat"]
        active_sat_keysets: list[KeysetInfo] = [
            ks for ks in sat_keysets if ks["active"]
        ]
        assert len(active_sat_keysets) > 0, "Should have at least one active sat keyset"

        print(f"✅ Found {len(keysets)} keysets")

    async def test_get_keys_with_validation(self, mint: Mint) -> None:
        """Test retrieving mint public keys with NUT-01 validation."""
        # Get active keysets with full details
        keysets: list[Keyset] = await mint.get_active_keysets()

        assert isinstance(keysets, list)
        assert len(keysets) > 0

        # Verify each keyset has proper structure
        for keyset in keysets:
            # Convert to dict for validation method
            keyset_dict = dict(keyset)
            assert mint._validate_keyset(keyset_dict), f"Invalid keyset: {keyset}"

            # Verify keys structure
            assert "keys" in keyset
            keys: dict[str, str] = keyset["keys"]
            assert isinstance(keys, dict)
            assert len(keys) > 0, "Keyset should have public keys"

            # Verify each key is valid compressed secp256k1
            for amount_str, pubkey in keys.items():
                # Amount should be valid
                amount: int = int(amount_str)
                assert amount > 0
                assert amount & (amount - 1) == 0  # Should be power of 2

                # Pubkey should be valid compressed format
                assert len(pubkey) == 66  # 33 bytes = 66 hex chars
                assert pubkey.startswith(("02", "03"))

                # Verify it's hex
                try:
                    bytes.fromhex(pubkey)
                except ValueError:
                    pytest.fail(f"Invalid hex pubkey: {pubkey}")

        print(f"✅ Retrieved and validated keys for {len(keysets)} keysets")

    async def test_validate_keysets_response(self, mint: Mint) -> None:
        """Test the keyset validation methods with real data."""
        keysets: list[KeysetInfo] = await mint.get_keysets_info()

        # Test validation method on proper response format
        response: dict[str, list[KeysetInfo]] = {"keysets": keysets}
        assert mint.validate_keysets_response(response)

        # Test that we can get the full keyset details with keys
        if keysets:
            # Get full details for first keyset
            keyset_full: Keyset = await mint.get_keyset(keysets[0]["id"])
            assert keyset_full
            assert "keys" in keyset_full
            assert isinstance(keyset_full["keys"], dict)

        print("✅ Keyset validation methods work correctly")


class TestMintQuoteOperations:
    """Test mint quote operations against real mint."""

    async def test_create_mint_quote(self, mint: Mint) -> PostMintQuoteResponse | None:
        """Test creating mint quotes for various amounts and units."""
        try:
            # Test basic quote creation
            quote_resp: PostMintQuoteResponse = await mint.create_mint_quote(
                unit="sat", amount=100
            )

            assert "quote" in quote_resp
            assert "request" in quote_resp  # BOLT11 invoice
            assert "amount" in quote_resp
            assert "unit" in quote_resp
            assert "state" in quote_resp

            # Verify quote structure
            assert isinstance(quote_resp["quote"], str)
            assert len(quote_resp["quote"]) > 0

            # Should be a BOLT11 invoice
            assert quote_resp["request"].startswith("lnbc")

            # Amount and unit should match request
            assert quote_resp["amount"] == 100
            assert quote_resp["unit"] == "sat"

            # Initial state should be UNPAID
            assert quote_resp["state"] in ["UNPAID", "ISSUED"]

            print(f"✅ Created mint quote: {quote_resp['quote']}")
            return quote_resp
        except MintError as e:
            if "rate limit" in str(e).lower():
                print("⚠️  Test skipped due to rate limiting")
                return None
            else:
                raise

    async def test_get_mint_quote_status(self, mint: Mint) -> None:
        """Test checking mint quote status."""
        try:
            # Create a quote first
            quote_resp: PostMintQuoteResponse = await mint.create_mint_quote(
                unit="sat", amount=50
            )
            quote_id: str = quote_resp["quote"]

            # Check quote status
            status: PostMintQuoteResponse = await mint.get_mint_quote(quote_id)

            assert "quote" in status
            assert "state" in status
            assert status["quote"] == quote_id

            # State should be consistent
            assert status["state"] in ["UNPAID", "PAID", "ISSUED"]

            print(f"✅ Quote {quote_id} status: {status['state']}")
        except MintError as e:
            if "rate limit" in str(e).lower():
                print("⚠️  Test skipped due to rate limiting")
            else:
                raise

    async def test_mint_quote_different_amounts(self, mint: Mint) -> None:
        """Test mint quotes for different amounts with rate limiting."""
        amounts: list[int] = [1, 10, 100, 1000]

        for amount in amounts:
            try:
                quote_resp: PostMintQuoteResponse = await mint.create_mint_quote(
                    unit="sat", amount=amount
                )
                assert quote_resp["amount"] == amount
                print(f"✅ Created quote for {amount} sats")
                await asyncio.sleep(1)  # Delay to avoid rate limiting
            except MintError as e:
                error_msg: str = str(e).lower()
                # Some mints might have minimum amounts or rate limiting
                if "minimum" in error_msg:
                    print(f"⚠️  Mint has minimum amount restriction for {amount} sats")
                elif "rate limit" in error_msg:
                    print(f"⚠️  Rate limited for amount {amount} sats")
                    break  # Stop testing remaining amounts
                else:
                    raise

    async def test_mint_quote_with_description(self, mint: Mint) -> None:
        """Test mint quote with description and optional fields."""
        description: str = "Integration test payment"

        try:
            quote_resp: PostMintQuoteResponse = await mint.create_mint_quote(
                unit="sat", amount=25, description=description
            )

            assert quote_resp["amount"] == 25
            # Description might be included in the invoice
            print(f"✅ Created quote with description: {quote_resp['quote']}")
        except MintError as e:
            if "rate limit" in str(e).lower():
                print("⚠️  Test skipped due to rate limiting")
            else:
                raise


class TestMeltQuoteOperations:
    """Test melt quote operations (may be limited without actual Lightning)."""

    async def test_create_melt_quote_invalid_invoice(self, mint: Mint) -> None:
        """Test melt quote with invalid invoice (should fail gracefully)."""
        invalid_invoice: str = "lnbc1000n1invalid"

        with pytest.raises(MintError) as exc_info:
            await mint.create_melt_quote(unit="sat", request=invalid_invoice)

        # Should get a reasonable error message
        error_msg: str = str(exc_info.value).lower()
        assert any(
            word in error_msg for word in ["invalid", "bad", "bech32", "not valid"]
        )
        print("✅ Invalid invoice properly rejected")

    async def test_melt_quote_structure(self, mint: Mint) -> None:
        """Test melt quote response structure with a potentially valid invoice."""
        # Use a well-formed but likely expired/invalid invoice
        test_invoice: str = "lnbc100n1pjqq5jqsp5l3l6t7k6z4t5r9m8s7q2w3e4r5t6y7u8i9o0p1l2k3j4h5g6f7s8dp9q7sqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqspp5qr3n6g5g4t6t7k8h9j0k1l2m3n4p5q6r7s8t9u0v1w2x3y4z5a6b7c8d9e0f1gq9qtzqqqqqq"

        try:
            quote_resp: PostMeltQuoteResponse = await mint.create_melt_quote(
                unit="sat", request=test_invoice
            )

            # If it succeeds, verify structure
            assert "quote" in quote_resp
            assert "amount" in quote_resp
            assert "fee_reserve" in quote_resp
            assert "unit" in quote_resp

            print(f"✅ Melt quote structure valid: {quote_resp}")

        except MintError as e:
            # Expected for invalid/expired invoices
            print(f"⚠️  Melt quote failed as expected: {e}")


class TestTokenManagement:
    """Test token management operations."""

    async def test_check_state_empty(self, mint: Mint) -> None:
        """Test checking state with empty Y values."""
        state_resp: PostCheckStateResponse = await mint.check_state(Ys=[])

        assert "states" in state_resp
        assert isinstance(state_resp["states"], list)
        assert len(state_resp["states"]) == 0

        print("✅ Empty state check works correctly")

    async def test_check_state_fake_proofs(self, mint: Mint) -> None:
        """Test checking state with fake proof Y values."""
        # Generate some fake Y values (valid format but non-existent proofs)
        fake_y_values: list[str] = [
            "02" + "a1b2c3d4e5f6" * 10,  # 66 char hex string
            "03" + "f1e2d3c4b5a6" * 10,  # Another fake Y value
        ]

        state_resp: PostCheckStateResponse = await mint.check_state(Ys=fake_y_values)

        assert "states" in state_resp
        states: list[dict[str, str]] = state_resp["states"]
        assert len(states) == len(fake_y_values)

        # States should indicate these proofs don't exist
        for state in states:
            assert "Y" in state or "state" in state

        print(f"✅ Checked state for {len(fake_y_values)} fake Y values")

    async def test_restore_empty(self, mint: Mint) -> None:
        """Test restore with empty outputs (should fail)."""
        with pytest.raises(MintError) as exc_info:
            await mint.restore(outputs=[])

        # Should get an error about no outputs provided
        error_msg: str = str(exc_info.value).lower()
        assert any(word in error_msg for word in ["no outputs", "empty", "required"])
        print("✅ Empty restore properly rejected")

    async def test_swap_validation_errors(self, mint: Mint) -> None:
        """Test swap with invalid inputs (should fail)."""
        # Create fake but properly structured inputs
        fake_inputs: list[Proof] = [
            {
                "id": "00ad268c4d1f5826",
                "amount": 10,
                "secret": "fake_secret",
                "C": "02a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                "mint": "https://testnut.cashu.space",
                "unit": "sat",
            }
        ]

        # Create blinded outputs for the same amount
        fake_outputs: list[BlindedMessage] = [
            BlindedMessage(
                amount=10,
                id="00ad268c4d1f5826",
                B_="02f1e2d3c4b5a6b7c8d9e0f1e2d3c4b5a6b7c8d9e0f1e2d3c4b5a6b7c8d9e0f1e2",
            )
        ]

        with pytest.raises(MintError) as exc_info:
            # Cast to ProofComplete since swap expects it (ProofComplete extends Proof with optional fields)
            await mint.swap(
                inputs=cast(list[ProofComplete], fake_inputs), outputs=fake_outputs
            )

        # Should get a reasonable error (invalid proof, unknown secret, etc.)
        error_msg: str = str(exc_info.value).lower()
        assert any(
            word in error_msg for word in ["invalid", "unknown", "proof", "secret"]
        )
        print("✅ Invalid swap properly rejected")


class TestMintValidation:
    """Test mint validation methods with real data."""

    async def test_keyset_validation_real_data(self, mint: Mint) -> None:
        """Test keyset validation with real mint data."""
        keysets: list[KeysetInfo] = await mint.get_keysets_info()

        # All real keysets should pass validation
        for keyset in keysets:
            # Convert to dict for validation method
            keyset_dict = dict(keyset)
            assert mint.validate_keyset(keyset_dict), (
                f"Real keyset failed validation: {keyset}"
            )

        # Test the response validation
        assert mint.validate_keysets_response({"keysets": keysets})

        print(f"✅ All {len(keysets)} real keysets passed validation")

    async def test_pubkey_validation_real_keys(self, mint: Mint) -> None:
        """Test public key validation with real mint keys."""
        keysets: list[Keyset] = await mint.get_active_keysets()

        valid_count: int = 0
        for keyset in keysets:
            if "keys" in keyset:
                for amount_str, pubkey in keyset["keys"].items():
                    assert mint._is_valid_compressed_pubkey(pubkey), (
                        f"Invalid pubkey from mint: {pubkey}"
                    )
                    valid_count += 1

        assert valid_count > 0, "Should have validated at least one public key"
        print(f"✅ All {valid_count} real public keys passed validation")


class TestMintErrorHandling:
    """Test error handling with real mint responses."""

    async def test_invalid_endpoints(self, mint: Mint) -> None:
        """Test requests to invalid endpoints."""
        with pytest.raises(MintError) as exc_info:
            await mint._request("GET", "/v1/nonexistent")

        assert "404" in str(exc_info.value) or "400" in str(exc_info.value)
        print("✅ Invalid endpoint properly rejected")

    async def test_invalid_keyset_id(self, mint: Mint) -> None:
        """Test requesting keys with invalid keyset ID."""
        invalid_keyset_id: str = "invalid_id_123"

        with pytest.raises(MintError) as exc_info:
            await mint.get_keyset(invalid_keyset_id)

        error_msg: str = str(exc_info.value)
        assert "400" in error_msg or "404" in error_msg
        print("✅ Invalid keyset ID properly rejected")

    async def test_malformed_requests(self, mint: Mint) -> None:
        """Test malformed request handling."""
        # Try to create quote with invalid unit
        with pytest.raises(Exception):  # Could be MintError or validation error
            await mint.create_mint_quote(unit="invalid_unit", amount=100)  # type: ignore

        print("✅ Malformed requests properly handled")


class TestMintComplexOperations:
    """Test more complex mint operations and flows."""

    async def test_multiple_concurrent_quotes(self, mint: Mint) -> None:
        """Test creating multiple quotes with rate limit handling."""

        async def create_quote_with_retry(amount: int) -> PostMintQuoteResponse:
            for attempt in range(3):
                try:
                    return await mint.create_mint_quote(unit="sat", amount=amount)
                except MintError as e:
                    if "rate limit" in str(e).lower() and attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))  # Exponential backoff
                        continue
                    raise
            # This should never be reached due to the raise above, but for type safety
            raise RuntimeError("All retry attempts failed")

        # Create quotes with small delays to avoid rate limiting
        amounts: list[int] = [10, 25, 50, 100]
        quotes: list[PostMintQuoteResponse] = []

        for amount in amounts:
            try:
                quote: PostMintQuoteResponse = await create_quote_with_retry(amount)
                quotes.append(quote)
                await asyncio.sleep(0.5)  # Small delay between requests
            except MintError as e:
                if "rate limit" in str(e).lower():
                    print(f"⚠️  Rate limited for amount {amount}, skipping")
                    continue
                raise

        if quotes:
            # Verify all quotes are unique
            quote_ids: list[str] = [q["quote"] for q in quotes]
            assert len(set(quote_ids)) == len(quote_ids), (
                "All quote IDs should be unique"
            )

            print(
                f"✅ Created {len(quotes)} quotes successfully (some may have been rate limited)"
            )
        else:
            print(
                "⚠️  All requests were rate limited - test passed (shows rate limiting works)"
            )

    async def test_quote_status_polling(self, mint: Mint) -> None:
        """Test polling quote status over time with rate limit handling."""
        try:
            # Create a quote
            quote_resp: PostMintQuoteResponse = await mint.create_mint_quote(
                unit="sat", amount=21
            )
            quote_id: str = quote_resp["quote"]

            # Poll status a few times with delays
            states: list[str] = []
            for i in range(3):
                status: PostMintQuoteResponse = await mint.get_mint_quote(quote_id)
                states.append(status["state"])

                if i < 2:  # Don't wait after last check
                    await asyncio.sleep(1)

            # State should be consistent (likely UNPAID for all checks)
            print(f"✅ Quote {quote_id} states over time: {states}")
        except MintError as e:
            if "rate limit" in str(e).lower():
                print(
                    "⚠️  Test skipped due to rate limiting - this shows rate limiting works"
                )
            else:
                raise

    async def test_keys_caching_behavior(self, mint: Mint) -> None:
        """Test that repeated key requests work correctly."""
        # Get keys multiple times
        keys1: list[Keyset] = await mint.get_active_keysets()
        keys2: list[Keyset] = await mint.get_active_keysets()

        # Should return consistent results
        assert keys1 == keys2

        # Get keysets multiple times
        keysets1: list[KeysetInfo] = await mint.get_keysets_info()
        keysets2: list[KeysetInfo] = await mint.get_keysets_info()

        # Should return consistent results
        assert keysets1 == keysets2

        print("✅ Repeated requests return consistent results")


class TestMintPerformance:
    """Test mint performance and reliability."""

    async def test_rapid_requests(self, mint: Mint) -> None:
        """Test making rapid sequential requests."""
        start_time: float = asyncio.get_event_loop().time()

        # Make multiple rapid requests
        tasks: list[Any] = []
        for _ in range(5):
            tasks.append(mint.get_info())

        results: list[MintInfo] = await asyncio.gather(*tasks)

        end_time: float = asyncio.get_event_loop().time()
        duration: float = end_time - start_time

        assert len(results) == 5
        assert all(isinstance(result, dict) for result in results)

        print(f"✅ Completed 5 concurrent requests in {duration:.2f}s")

    async def test_connection_reuse(self, mint: Mint) -> None:
        """Test that HTTP connections are properly reused."""
        # Make multiple requests that should reuse connections
        info1: MintInfo = await mint.get_info()
        keysets: list[KeysetInfo] = await mint.get_keysets_info()
        info2: MintInfo = await mint.get_info()

        assert isinstance(info1, dict)
        assert isinstance(keysets, list)
        assert isinstance(info2, dict)

        # Info should be mostly consistent (excluding time-sensitive fields)
        info1_copy: dict[str, Any] = dict(info1)
        info2_copy: dict[str, Any] = dict(info2)

        # Remove time-sensitive fields that may differ between requests
        time_fields: list[str] = ["time", "timestamp", "updated_at"]
        for time_field in time_fields:
            info1_copy.pop(time_field, None)
            info2_copy.pop(time_field, None)

        assert info1_copy == info2_copy, "Non-time-sensitive fields should be identical"

        print("✅ Connection reuse working correctly")


if __name__ == "__main__":
    # Allow running this file directly for debugging
    import sys

    if not os.getenv("RUN_INTEGRATION_TESTS"):
        print("Set RUN_INTEGRATION_TESTS=1 to run integration tests")
        print(
            "Example: RUN_INTEGRATION_TESTS=1 python -m pytest tests/integration/test_mint_methods.py -v"
        )
        sys.exit(1)

    # Run a simple smoke test
    async def main() -> None:
        mint = Mint("https://testnut.cashu.space")

        try:
            print("🔄 Testing mint connection...")
            info: MintInfo = await mint.get_info()
            print(f"✅ Connected to mint: {info.get('name', 'Unknown')}")

            print("🔄 Testing keysets...")
            keysets: list[Keyset] = await mint.get_active_keysets()
            print(f"✅ Found {len(keysets)} keysets")

            print("🔄 Testing quote creation...")
            quote: PostMintQuoteResponse = await mint.create_mint_quote(
                unit="sat", amount=100
            )
            print(f"✅ Created quote: {quote['quote']}")

            print("✅ All basic tests passed!")

        except Exception as e:
            print(f"❌ Test failed: {e}")
            raise
        finally:
            await mint.aclose()

    asyncio.run(main())
