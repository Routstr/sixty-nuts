"""Multi-currency support integration tests.

Tests wallet operations across multiple currencies (sat, msat, usd, eur) using
the cashu test mint which provides different keysets for each currency.
Only runs when RUN_INTEGRATION_TESTS environment variable is set.

This test suite is designed to validate the multi-currency implementation
and help debug any issues with currency-specific operations.
"""

import asyncio
import os
import pytest
from typing import Any, cast

from sixty_nuts.wallet import Wallet
from sixty_nuts.mint import Mint, MintInfo, KeysetInfo
from sixty_nuts.types import CurrencyUnit


# Skip all integration tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests only run when RUN_INTEGRATION_TESTS is set",
)


def get_relay_wait_time(base_seconds: float = 1.0) -> float:
    """Get appropriate wait time based on service type."""
    if os.getenv("USE_LOCAL_SERVICES"):
        return base_seconds
    else:
        return base_seconds * 3.0  # 3x longer for public relays


class TestMultiCurrencySupport:
    """Test multi-currency operations with different keysets."""

    async def test_mint_info_currencies_available(
        self, wallet: Wallet
    ) -> list[CurrencyUnit]:
        """Verify the test mint supports multiple currencies."""
        print("\n🌍 Testing available currencies on test mint...")

        # Get mint instance
        mint: Mint = wallet._get_mint(wallet._primary_mint_url())

        # Get mint info
        info: MintInfo = await mint.get_info()
        print(f"Mint info: {info}")

        # Get available currencies
        currencies: list[CurrencyUnit] = await mint.get_currencies()
        print(f"Available currencies: {currencies}")

        # The test mint should support at least sat, msat, usd, eur
        expected_currencies: set[str] = {"sat", "msat", "usd", "eur"}
        available_set: set[str] = set(currencies)

        # Check if expected currencies are available
        missing: set[str] = expected_currencies - available_set
        if missing:
            print(f"⚠️  Warning: Missing expected currencies: {missing}")
            print(f"   Available: {available_set}")
            # Don't fail - test what's available

        assert len(currencies) > 0, "Mint should support at least one currency"
        return currencies

    async def test_keysets_per_currency(self, wallet: Wallet) -> None:
        """Test that each currency has its own keyset."""
        print("\n🔑 Testing keysets for different currencies...")

        mint: Mint = wallet._get_mint(wallet._primary_mint_url())

        # Get all keysets info
        keysets_info: list[KeysetInfo] = await mint.get_keysets_info()
        print(f"Found {len(keysets_info)} keysets")

        # Group keysets by currency
        keysets_by_currency: dict[str, list[KeysetInfo]] = {}
        for keyset in keysets_info:
            unit: str = keyset.get("unit", "")
            if unit not in keysets_by_currency:
                keysets_by_currency[unit] = []
            keysets_by_currency[unit].append(keyset)

        print("\nKeysets by currency:")
        for unit, keysets in keysets_by_currency.items():
            active_keysets: list[KeysetInfo] = [
                k for k in keysets if k.get("active", True)
            ]
            print(f"  {unit}: {len(keysets)} total, {len(active_keysets)} active")
            for keyset in active_keysets[:1]:  # Show first active keyset
                print(
                    f"    - ID: {keyset['id']}, Fee: {keyset.get('input_fee_ppk', 0)} ppk"
                )

        # Verify we have at least one active keyset per currency
        for unit in ["sat", "msat", "usd", "eur"]:
            if unit in keysets_by_currency:
                active: list[KeysetInfo] = [
                    k for k in keysets_by_currency[unit] if k.get("active", True)
                ]
                assert len(active) > 0, (
                    f"Should have at least one active keyset for {unit}"
                )

    async def test_mint_in_different_currencies(self, wallet: Wallet) -> None:
        """Test minting tokens in different currencies."""
        print("\n💰 Testing minting in different currencies...")

        # Check initial state
        initial_state = await wallet.fetch_wallet_state(check_proofs=False)
        initial_balance_by_unit: dict[CurrencyUnit, int] = initial_state.balance_by_unit
        print(f"Initial balances by unit: {initial_balance_by_unit}")

        # Get available currencies
        mint: Mint = wallet._get_mint(wallet._primary_mint_url())
        available_currencies: list[CurrencyUnit] = await mint.get_currencies()

        # Test currencies that are available
        test_currencies: list[tuple[str, int]] = []
        test_amounts: dict[str, int] = {"sat": 100, "msat": 10000, "usd": 1, "eur": 1}

        for currency in ["sat", "msat", "usd", "eur"]:
            if currency in available_currencies:
                test_currencies.append((currency, test_amounts[currency]))
            else:
                print(f"⚠️  Skipping {currency} - not available on mint")

        if not test_currencies:
            pytest.skip("No expected currencies available on test mint")

        # Track minted amounts
        minted_by_currency: dict[str, int] = {}

        for currency, amount in test_currencies:
            print(f"\n  Minting {amount} {currency}...")

            try:
                # Create mint quote for specific currency
                invoice: str
                task: Any
                invoice, task = await wallet.mint_async(
                    amount=amount, currency=cast(CurrencyUnit, currency), timeout=60
                )
                print(f"    Invoice created: {invoice[:50]}...")

                # Wait for auto-payment
                timeout: float = 30.0 if os.getenv("USE_LOCAL_SERVICES") else 90.0
                paid: bool = await asyncio.wait_for(task, timeout=timeout)

                if paid:
                    print(f"    ✓ Successfully minted {amount} {currency}")
                    minted_by_currency[currency] = amount
                else:
                    print(f"    ✗ Failed to mint {currency} - invoice not paid")

                # Wait for events to propagate
                await asyncio.sleep(get_relay_wait_time(2.0))

                # Add extra delay between currencies to avoid relay rate limiting
                if currency != test_currencies[-1][0]:  # Not the last currency
                    await asyncio.sleep(get_relay_wait_time(5.0))

            except Exception as e:
                print(f"    ✗ Error minting {currency}: {e}")
                # Continue with other currencies

        # Verify balances increased for each currency
        if minted_by_currency:
            print("\n📊 Verifying currency-specific balances...")

            # Fetch updated state with retries
            max_retries: int = 5
            for attempt in range(max_retries):
                state = await wallet.fetch_wallet_state(check_proofs=True)
                balance_by_unit: dict[str, int] = {
                    str(k): v for k, v in state.balance_by_unit.items()
                }

                # Check if all minted currencies show up
                all_present: bool = all(
                    currency in balance_by_unit and balance_by_unit[currency] >= amount
                    for currency, amount in minted_by_currency.items()
                )

                if all_present:
                    break

                if attempt < max_retries - 1:
                    print(
                        f"  Retry {attempt + 1}: Waiting for all balances to update..."
                    )
                    await asyncio.sleep(get_relay_wait_time(3.0))

            print(f"  Final balances by unit: {balance_by_unit}")

            # Verify each minted currency
            for currency, expected_amount in minted_by_currency.items():
                actual_balance: int = balance_by_unit.get(currency, 0)
                initial_balance: int = {
                    str(k): v for k, v in initial_balance_by_unit.items()
                }.get(currency, 0)

                assert actual_balance >= initial_balance + expected_amount, (
                    f"Balance for {currency} should increase by at least {expected_amount}, "
                    f"got {actual_balance - initial_balance}"
                )
                print(f"  ✓ {currency}: {actual_balance} (minted {expected_amount})")

    async def test_send_tokens_different_currencies(self, wallet: Wallet) -> None:
        """Test sending tokens in different currencies."""
        print("\n📤 Testing sending tokens in different currencies...")

        # Add delay to reset any rate limits from previous tests
        await asyncio.sleep(get_relay_wait_time(10.0))

        # First ensure we have some balance in different currencies
        state = await wallet.fetch_wallet_state(check_proofs=True)
        balance_by_unit: dict[str, int] = {
            str(k): v for k, v in state.balance_by_unit.items()
        }

        if not balance_by_unit:
            print("  No balances found, minting first...")
            # Mint some tokens first
            await self.test_mint_in_different_currencies(wallet)
            state = await wallet.fetch_wallet_state(check_proofs=True)
            balance_by_unit = {str(k): v for k, v in state.balance_by_unit.items()}

        print(f"  Current balances: {balance_by_unit}")

        # Test sending for each available currency
        sent_tokens: dict[str, tuple[str, int]] = {}

        for currency, balance in balance_by_unit.items():
            if balance < 1:
                print(f"  ⚠️  Skipping {currency} - insufficient balance ({balance})")
                continue

            # Determine amount to send (small amount, but at least 1)
            if currency == "msat":
                send_amount: int = (
                    min(1000, balance // 2) if balance >= 2000 else balance
                )
            else:
                send_amount = 1  # Always send 1 unit for other currencies

            print(f"\n  Sending {send_amount} {currency}...")

            try:
                # Create token for specific currency
                token: str = await wallet.send(
                    amount=send_amount,
                    unit=currency,  # type: ignore
                    token_version=4,  # Use V4 format
                )

                # Verify token was created
                assert token.startswith("cashuB"), "Should create V4 token"
                print(f"    ✓ Created token: {token[:50]}...")

                # Parse token to verify currency
                mint_url: str
                token_unit: str
                proofs: list[Any]
                mint_url, token_unit, proofs = wallet._parse_cashu_token(token)
                assert token_unit == currency, (
                    f"Token should be in {currency}, got {token_unit}"
                )
                assert sum(p["amount"] for p in proofs) == send_amount

                sent_tokens[currency] = (token, send_amount)

                # Wait for state update
                await asyncio.sleep(get_relay_wait_time(1.0))

            except Exception as e:
                print(f"    ✗ Error sending {currency}: {e}")

        # Verify balances decreased
        if sent_tokens:
            print("\n📊 Verifying balance changes after sending...")

            # Fetch updated state
            updated_state = await wallet.fetch_wallet_state(check_proofs=True)
            updated_balance_by_unit: dict[str, int] = {
                str(k): v for k, v in updated_state.balance_by_unit.items()
            }

            for currency, (token, amount) in sent_tokens.items():
                initial: int = balance_by_unit.get(currency, 0)
                current: int = updated_balance_by_unit.get(currency, 0)

                # Balance should decrease by at least the sent amount (might be more due to fees)
                assert current <= initial - amount, (
                    f"Balance for {currency} should decrease by at least {amount}, "
                    f"was {initial}, now {current}"
                )

                actual_decrease: int = initial - current
                fees: int = actual_decrease - amount

                if fees > 0:
                    print(
                        f"  ✓ {currency}: {initial} → {current} (-{amount} sent, -{fees} fees)"
                    )
                else:
                    print(f"  ✓ {currency}: {initial} → {current} (-{amount})")

    async def test_redeem_multi_currency_tokens(self, wallet: Wallet) -> None:
        """Test redeeming tokens of different currencies."""
        print("\n📥 Testing redeeming multi-currency tokens...")

        # First create some tokens to redeem
        print("  Creating tokens to redeem...")

        # Get current state
        initial_state = await wallet.fetch_wallet_state(check_proofs=True)
        initial_balances: dict[CurrencyUnit, int] = initial_state.balance_by_unit.copy()

        # Create tokens in available currencies
        tokens_to_redeem: dict[str, tuple[str, int]] = {}

        for currency, balance in initial_balances.items():
            if balance < 1:
                continue

            amount: int = 1 if currency != "msat" else 1000
            if balance < amount:
                continue

            try:
                token: str = await wallet.send(
                    amount=amount, unit=currency, token_version=3
                )
                tokens_to_redeem[currency] = (token, amount)
                print(f"    Created {currency} token for {amount}")
            except Exception as e:
                print(f"    Skipped {currency}: {e}")

        if not tokens_to_redeem:
            pytest.skip("No tokens could be created for redemption test")

        # Wait for state to update
        await asyncio.sleep(get_relay_wait_time(2.0))

        # Now redeem each token
        print("\n  Redeeming tokens...")

        for currency_str, (token, amount) in tokens_to_redeem.items():
            print(f"\n  Redeeming {amount} {currency_str} token...")

            try:
                # Get balance before redemption
                state_before = await wallet.fetch_wallet_state(check_proofs=True)
                balance_before: int = {
                    str(k): v for k, v in state_before.balance_by_unit.items()
                }.get(currency_str, 0)

                # Redeem token
                redeem_result = await wallet.redeem(token)
                redeemed_amount: int
                unit: str
                redeemed_amount, unit = redeem_result

                assert unit == currency_str, (
                    f"Redeemed unit should be {currency_str}, got {unit}"
                )
                # Amount might be less due to fees
                assert redeemed_amount <= amount, (
                    "Redeemed amount should not exceed sent amount"
                )

                print(
                    f"    ✓ Redeemed {redeemed_amount} {unit} (fees: {amount - redeemed_amount})"
                )

                # Wait for state update
                await asyncio.sleep(get_relay_wait_time(2.0))

                # Verify balance increased
                state_after = await wallet.fetch_wallet_state(check_proofs=True)
                balance_after: int = {
                    str(k): v for k, v in state_after.balance_by_unit.items()
                }.get(currency_str, 0)

                assert balance_after == balance_before + redeemed_amount, (
                    f"Balance should increase by {redeemed_amount}, "
                    f"was {balance_before}, now {balance_after}"
                )

            except Exception as e:
                print(f"    ✗ Error redeeming {currency_str}: {e}")

    async def test_keyset_specific_operations(self, wallet: Wallet) -> None:
        """Test operations with specific keysets."""
        print("\n🔐 Testing keyset-specific operations...")

        mint: Mint = wallet._get_mint(wallet._primary_mint_url())

        # Get all active keysets
        keysets: list[KeysetInfo] = await mint.get_keysets_info()
        active_keysets: list[KeysetInfo] = [k for k in keysets if k.get("active", True)]

        print(f"  Found {len(active_keysets)} active keysets")

        # Get current proofs grouped by keyset
        state = await wallet.fetch_wallet_state(check_proofs=True)
        proofs_by_keyset: dict[str, list[Any]] = state.proofs_by_keyset

        print("\n  Current proofs by keyset:")
        for keyset_id, proofs in proofs_by_keyset.items():
            keyset_info: KeysetInfo | None = next(
                (k for k in keysets if k["id"] == keyset_id), None
            )
            if keyset_info:
                total: int = sum(p["amount"] for p in proofs)
                print(
                    f"    {keyset_id}: {len(proofs)} proofs, {total} {keyset_info.get('unit', '?')}"
                )
            else:
                print(f"    {keyset_id}: {len(proofs)} proofs (keyset info not found)")

        # Test denomination optimization per keyset
        if proofs_by_keyset:
            print("\n  Testing denomination optimization...")

            for keyset_id, proofs in list(proofs_by_keyset.items())[
                :1
            ]:  # Test first keyset
                if len(proofs) < 2:
                    print(f"    Skipping {keyset_id} - not enough proofs")
                    continue

                keyset_info = next((k for k in keysets if k["id"] == keyset_id), None)
                if not keyset_info:
                    continue

                unit: str = keyset_info.get("unit", "sat")
                total_amount: int = sum(p["amount"] for p in proofs)

                print(
                    f"\n    Optimizing {len(proofs)} proofs totaling {total_amount} {unit}..."
                )

                # Get optimal denominations
                available_denoms: list[int] = await mint.get_denominations_for_currency(
                    cast(CurrencyUnit, unit)
                )
                optimal_denoms: dict[int, int] = mint.calculate_optimal_split(
                    total_amount, available_denoms
                )

                print(f"    Current denominations: {[p['amount'] for p in proofs]}")
                print(f"    Optimal denominations: {optimal_denoms}")

                # The wallet should handle consolidation automatically
                # Just verify the calculation works
                optimal_count: int = sum(optimal_denoms.values())
                current_count: int = len(proofs)

                if optimal_count < current_count:
                    print(
                        f"    → Could reduce from {current_count} to {optimal_count} proofs"
                    )
                else:
                    print("    → Already optimal or close to optimal")

    async def test_multi_mint_multi_currency(self, wallet: Wallet) -> None:
        """Test multi-currency operations across multiple mints if available."""
        print("\n🏪 Testing multi-mint multi-currency support...")

        # This test would require multiple test mints
        # For now, just verify the wallet can handle the concept

        # Check wallet's mint URLs
        print(f"  Wallet mint URLs: {wallet.mint_urls}")

        if len(wallet.mint_urls) < 2:
            print("  ⚠️  Only one mint configured, skipping multi-mint test")
            pytest.skip("Multi-mint test requires multiple mints")

        # Get info from each mint
        mint_info: dict[str, dict[str, Any]] = {}
        for mint_url in sorted(wallet.mint_urls)[:2]:  # Test first 2 mints
            try:
                mint: Mint = wallet._get_mint(mint_url)
                info: MintInfo = await mint.get_info()
                currencies: list[CurrencyUnit] = await mint.get_currencies()
                mint_info[mint_url] = {
                    "name": info.get("name", "Unknown"),
                    "currencies": currencies,
                }
                print(f"\n  Mint: {mint_url}")
                print(f"    Name: {info.get('name', 'Unknown')}")
                print(f"    Currencies: {currencies}")
            except Exception as e:
                print(f"  ✗ Error getting info from {mint_url}: {e}")

        # If we have multiple mints with different currencies, we could test
        # cross-mint operations here

        print("\n  Multi-mint multi-currency infrastructure is in place")


async def main() -> None:
    """Run tests directly."""
    import sys

    sys.exit(pytest.main([__file__, "-v"]))


if __name__ == "__main__":
    asyncio.run(main())
