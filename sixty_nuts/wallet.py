from __future__ import annotations


from typing import Literal, cast
import base64
import json
import secrets
import time
import asyncio
from pathlib import Path

import httpx
from coincurve import PublicKey

from .mint import Mint, ProofComplete, get_mints_from_env
from .relay import RelayClient, EventKind, get_relays_from_env
from .crypto import (
    unblind_signature,
    hash_to_curve,
    create_blinded_message_with_secret,
    get_mint_pubkey_for_amount,
    decode_nsec,
    get_pubkey,
    nip44_decrypt,
    generate_privkey,
)
from .lnurl import (
    get_lnurl_data,
    get_lnurl_invoice,
    parse_lightning_invoice_amount,
    LNURLError,
)
from .types import (
    Proof,
    WalletError,
    WalletState,
    CurrencyUnit,
    BlindedMessage,
)
from .events import EventManager

try:
    import cbor2
except ModuleNotFoundError:  # pragma: no cover – allow runtime miss
    cbor2 = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Wallet implementation
# ──────────────────────────────────────────────────────────────────────────────


class Wallet:
    """Lightweight stateless Cashu wallet implementing NIP-60."""

    def __init__(
        self,
        nsec: str,  # nostr private key
        *,
        mint_urls: list[str] | None = None,
        relay_urls: list[str] | None = None,
    ) -> None:
        self._privkey = decode_nsec(nsec)
        self.pubkey = get_pubkey(self._privkey)

        self.mint_urls: list[str] = mint_urls or get_mints_from_env()
        self.relay_urls: list[str] = relay_urls or get_relays_from_env()
        self.mints: dict[str, Mint] = {}

        # Relay manager - will be initialized with proper relays later
        self.relay_manager = RelayClient(
            relay_urls=self.relay_urls,  # May be empty initially
            privkey=self._privkey,  # Already a PrivateKey object
            use_queued_relays=True,
            min_relay_interval=1.0,
        )

        self.event_manager: EventManager = EventManager(
            relay_manager=self.relay_manager,
            privkey=self._privkey,
            mint_urls=self.mint_urls,
        )
        self._minted_quotes: set[str] = set()
        self.mint_client = httpx.AsyncClient()

        self.wallet_privkey: str | None = None

        # Cache for proof validation results to prevent re-checking spent proofs
        self._proof_state_cache: dict[
            str, dict[str, str]
        ] = {}  # proof_id -> {state, timestamp}
        self._cache_expiry = 300  # 5 minutes

        # Track known spent proofs to avoid re-validation
        self._known_spent_proofs: set[str] = set()

    @classmethod
    async def create(
        cls,
        nsec: str,
        *,
        mint_urls: list[str] | None = None,
        relay_urls: list[str] | None = None,
        auto_init: bool = True,
        prompt_for_relays: bool = True,
    ) -> "Wallet":
        """Create and optionally check for existing wallet events.

        Args:
            nsec: Nostr private key
            mint_urls: Cashu mint URLs
            relay_urls: Nostr relay URLs (if None, will discover automatically)
            auto_init: If True, check for existing wallet state (but don't create new events)
            prompt_for_relays: If True, prompt user for relays if none found

        Returns:
            Wallet instance (call initialize_wallet() to create wallet events if needed)
        """
        # Import here to avoid circular imports
        from .relay import get_relays_for_wallet

        # If no relays provided, discover them
        if not relay_urls:
            privkey = decode_nsec(nsec)
            relay_urls = await get_relays_for_wallet(
                privkey, prompt_if_needed=prompt_for_relays
            )

        wallet = cls(nsec=nsec, mint_urls=mint_urls, relay_urls=relay_urls)

        # TODO: fix this
        # # Initialize mint URLs from various sources
        # try:
        #     await wallet._initialize_mint_urls()
        # except WalletError:
        #     # If this is CLI usage, we'll handle mint selection there
        #     # For non-CLI usage, re-raise the error
        #     raise

        # # Initialize event manager now that we have mint URLs
        # await wallet._initialize_event_manager()

        if auto_init:
            try:
                # Try to connect to relays and check for existing state
                await wallet.relay_manager.get_relay_connections()
                # Try to fetch existing wallet state if it exists
                await wallet.fetch_wallet_state(check_proofs=False)
            except Exception:
                # If no wallet exists or fetch fails, that's fine
                # User can call initialize_wallet() explicitly if needed
                pass

        return wallet

    # ─────────────────────────────── Receive ──────────────────────────────────

    async def redeem(self, token: str, *, auto_swap: bool = True) -> tuple[int, str]:
        """Redeem a Cashu token into the wallet balance.

        If the token is from an untrusted mint (not in wallet's mint_urls),
        it will automatically be swapped to the wallet's primary mint.

        Args:
            token: Cashu token to redeem
            auto_swap: If True, automatically swap tokens from untrusted mints

        Returns:
            Tuple of (amount, unit) added to wallet
        """
        # Parse token
        mint_url, unit, proofs = self._parse_cashu_token(token)

        # Check if this is a trusted mint
        if auto_swap and self.mint_urls and mint_url not in self.mint_urls:
            # Token is from untrusted mint - swap to our primary mint
            return await self.remote_redeem_proofs(proofs, self._primary_mint_url())

        # Proceed with normal redemption for trusted mints
        # Calculate total amount
        total_amount = sum(p["amount"] for p in proofs)

        # Get mint instance to calculate fees
        mint = self._get_mint(mint_url)

        # Calculate input fees for these proofs
        input_fees = await self.calculate_total_input_fees(mint, proofs)

        # Calculate optimal denominations for the amount after fees
        output_amount = total_amount - input_fees
        optimal_denoms = await self._calculate_optimal_denominations(
            output_amount, mint_url, unit
        )

        # Use the abstracted swap method to get new proofs
        new_proofs = await self._swap_proof_denominations(
            proofs, optimal_denoms, mint_url, unit
        )

        # Publish new token event
        token_event_id = await self.event_manager.publish_token_event(new_proofs)  # type: ignore

        # Publish spending history
        await self.event_manager.publish_spending_history(
            direction="in",
            amount=output_amount,  # Use actual amount added after fees
            unit=unit,
            created_token_ids=[token_event_id],
        )

        return output_amount, unit  # Return actual amount added to wallet after fees

    async def mint_async(
        self,
        amount: int,
        *,
        mint_url: str | None = None,
        unit: CurrencyUnit | None = None,
        timeout: int = 300,
    ) -> tuple[str, asyncio.Task[bool]]:
        """Create a Lightning invoice and return a task that completes when paid.

        This returns immediately with the invoice and a background task that
        polls for payment.

        Args:
            amount: Amount in the wallet's currency unit
            timeout: Maximum seconds to wait for payment (default: 5 minutes)
            mint_url: Specific mint URL to use (defaults to primary mint)

        Returns:
            Tuple of (lightning_invoice, payment_task)
            The payment_task returns True when paid, False on timeout

        Example:
            invoice, task = await wallet.mint_async(100)
            print(f"Pay: {invoice}")
            # Do other things...
            paid = await task  # Wait for payment
        """
        mint = self._get_mint(mint_url or self._primary_mint_url())
        # Convert amount to base unit for the currency (e.g., dollars to cents)
        base_amount = self._convert_to_base_unit(amount, unit or "sat")
        quote_resp = await mint.create_mint_quote(amount=base_amount, unit=unit)
        quote_id, invoice = quote_resp["quote"], quote_resp["request"]

        async def poll_payment() -> bool:
            start_time = time.time()
            poll_interval = 1.0

            while (time.time() - start_time) < timeout:
                # Check quote status and mint if paid
                quote_status, new_proofs = await mint.check_quote_status_and_mint(
                    quote_id, base_amount, minted_quotes=self._minted_quotes
                )

                # If new proofs were minted, publish wallet events
                if new_proofs:
                    # Convert dict proofs to Proof
                    proof_dicts: list[Proof] = []
                    for proof in new_proofs:
                        print("DEBUG PROOFS", proof, proof["unit"], mint.url)
                        proof_dicts.append(
                            Proof(
                                id=proof["id"],
                                amount=proof["amount"],
                                secret=proof["secret"],
                                C=proof["C"],
                                mint=mint.url,
                                unit=proof["unit"],
                            )
                        )

                    # Publish token event
                    token_event_id = await self.event_manager.publish_token_event(
                        proof_dicts
                    )

                    # Publish spending history
                    mint_amount = sum(p["amount"] for p in new_proofs)
                    # Get unit from first proof (all proofs should have same unit from single mint operation)
                    mint_unit = (
                        new_proofs[0].get("unit", "sat") if new_proofs else "sat"
                    )
                    await self.event_manager.publish_spending_history(
                        direction="in",
                        amount=mint_amount,
                        unit=mint_unit,
                        created_token_ids=[token_event_id],
                    )

                if quote_status.get("paid"):
                    return True

                await asyncio.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.2, 5.0)

            return False

        # Create background task
        task = asyncio.create_task(poll_payment())
        return invoice, task

    # ─────────────────────────────── Send ─────────────────────────────────────

    async def melt(
        self,
        invoice: str,
        *,
        target_mint: str | None = None,
    ) -> None:
        """Pay a Lightning invoice by melting tokens with automatic multi-mint support.

        Args:
            invoice: BOLT-11 Lightning invoice to pay
            target_mint: Target mint URL (defaults to primary mint)

        Raises:
            WalletError: If insufficient balance or payment fails

        Example:
            await wallet.melt("lnbc100n1...")
        """
        try:
            invoice_amount_sat = parse_lightning_invoice_amount(invoice, "sat")
        except LNURLError as e:
            raise WalletError(f"Invalid Lightning invoice: {e}") from e

        state = await self.fetch_wallet_state(check_proofs=True)
        total_needed = int(invoice_amount_sat * 1.01)
        total_balance = await state.total_balance_sat()
        self.raise_if_insufficient_balance(total_balance, total_needed)

        if target_mint is None:
            mint_balances = state.mint_balances
            target_mint = max(mint_balances, key=lambda k: mint_balances[k])
            if mint_balances[target_mint] < total_needed:
                await self.transfer_balance_to_mint(
                    total_needed, from_mint=None, to_mint=target_mint
                )
                return await self.melt(invoice, target_mint=target_mint)

        # Create melt quote to get fees
        mint = self._get_mint(target_mint)
        melt_quote = await mint.create_melt_quote(request=invoice)
        fee_reserve = melt_quote.get("fee_reserve", 0)
        total_needed = invoice_amount_sat + fee_reserve
        self.raise_if_insufficient_balance(
            await state.total_balance_sat(), total_needed
        )

        # Select proofs for the total amount needed (invoice + fees)
        selected_proofs, consumed_proofs = await self._select_proofs(
            state.proofs, total_needed, target_mint, None
        )

        # Convert selected proofs to mint format
        mint_proofs = selected_proofs

        # Execute the melt operation
        # Cast to ProofComplete since melt expects it (ProofComplete extends Proof with optional fields)
        melt_resp = await mint.melt(
            quote=melt_quote["quote"], inputs=cast(list[ProofComplete], mint_proofs)
        )

        # Check if payment was successful
        if not melt_resp.get("paid", False):
            raise WalletError(
                f"Lightning payment failed. State: {melt_resp.get('state', 'unknown')}"
            )

        # Handle any change returned from the mint
        change_proofs: list[Proof] = []
        if "change" in melt_resp and melt_resp["change"]:
            print(
                "recieved change TODO pls handle this: ",
                melt_resp["change"],
                change_proofs,
            )
            # TODO: handle change
            # Convert BlindedSignatures to Proof format
            # This would require unblinding logic, but for now we'll skip change handling
            # In practice, most melts shouldn't have change if amounts are selected properly
            # await self.store_proofs(change_proofs)
            pass

        # Mark the consumed input proofs as spent
        await self._mark_proofs_as_spent(consumed_proofs)

    async def send(
        self,
        amount: int,
        *,
        mint_url: str | None = None,
        unit: CurrencyUnit = "sat",
        token_version: Literal[3, 4] = 4,  # Default to V4 (CashuB)
    ) -> str:
        """Create a Cashu token for sending.

        Selects proofs worth exactly the specified amount and returns a
        Cashu token string. The new proof selection automatically handles
        splitting proofs to achieve exact amounts.

        Args:
            amount: Amount to send in the specified currency unit
            mint_url: Target mint URL (defaults to mint with sufficient balance)
            unit: Currency unit to send (defaults to "sat")
            token_version: Token format version (3 for CashuA, 4 for CashuB)

        Returns:
            Cashu token string that can be sent to another wallet

        Raises:
            WalletError: If insufficient balance or operation fails
            ValueError: If unsupported token version

        Example:
            # Send using V4 format (default)
            token = await wallet.send(100)

            # Send using V3 format
            token = await wallet.send(100, token_version=3)

            # Send USD tokens
            token = await wallet.send(50, unit="usd")

            # Send from specific mint
            token = await wallet.send(100, mint_url="https://mint.example.com")
        """
        if token_version not in [3, 4]:
            raise ValueError(f"Unsupported token version: {token_version}. Use 3 or 4.")

        # Get wallet state once
        state = await self.fetch_wallet_state(check_proofs=True)

        # If no mint URL specified, find a mint with sufficient balance of the requested unit
        if mint_url is None:
            mint_url = await self._select_mint_for_amount(amount, unit, state.proofs)

        mint = self._get_mint(mint_url)

        # Filter proofs by the requested currency unit AND mint
        unit_proofs = [p for p in state.proofs if p.get("unit") == unit]
        mint_unit_proofs = [p for p in unit_proofs if p.get("mint") == mint.url]
        mint_unit_balance = sum(p["amount"] for p in mint_unit_proofs)

        if mint_unit_balance < amount:
            # Check if we have enough proofs of this unit at ANY mint
            total_unit_balance = sum(p["amount"] for p in unit_proofs)
            if total_unit_balance < amount:
                raise WalletError(
                    f"Insufficient {unit.upper()} balance: need {amount}, have {total_unit_balance}"
                )
            else:
                raise WalletError(
                    f"Insufficient {unit.upper()} balance at {mint.url}: need {amount}, have {mint_unit_balance}. "
                    f"Total {unit.upper()} balance across all mints: {total_unit_balance}"
                )

        selected_proofs, consumed_proofs = await self._select_proofs(
            unit_proofs, amount, mint.url, unit
        )

        token = self._serialize_proofs_for_token(
            selected_proofs, mint.url, token_version, currency=unit
        )

        await self._mark_proofs_as_spent(consumed_proofs)

        return token

    async def send_to_lnurl(
        self, lnurl: str, amount: int, *, unit: CurrencyUnit = "sat"
    ) -> int:
        """Send funds to an LNURL address.

        Args:
            lnurl: LNURL string (can be lightning:, user@host, bech32, or direct URL)
            amount: Amount to send in the specified currency unit
            unit: Currency unit to send (defaults to "sat")

        Returns:
            Amount actually paid in the specified currency unit

        Raises:
            WalletError: If amount is outside LNURL limits or insufficient balance
            LNURLError: If LNURL operations fail

        Example:
            # Send 1000 sats to a Lightning Address
            paid = await wallet.send_to_lnurl("user@getalby.com", 1000)
            print(f"Paid {paid} sats")

            # Send USD to Lightning Address
            paid = await wallet.send_to_lnurl("user@getalby.com", 50, unit="usd")
        """
        total_needed_estimated = int(amount * 1.01)
        total_balance = await self.get_balance()
        self.raise_if_insufficient_balance(total_balance, total_needed_estimated)

        lnurl_data = await get_lnurl_data(lnurl)

        if unit == "sat":
            amount_msat = amount * 1000
            min_sendable_sat = lnurl_data["min_sendable"] // 1000
            max_sendable_sat = lnurl_data["max_sendable"] // 1000
            unit_str = "sat"
        elif unit == "msat":
            amount_msat = amount
            min_sendable_sat = lnurl_data["min_sendable"]
            max_sendable_sat = lnurl_data["max_sendable"]
            unit_str = "msat"
        else:
            raise WalletError(f"Currency {unit} not supported for LNURL")

        if not (
            lnurl_data["min_sendable"] <= amount_msat <= lnurl_data["max_sendable"]
        ):
            raise WalletError(
                f"Amount {amount} {unit_str} is outside LNURL limits "
                f"({min_sendable_sat} - {max_sendable_sat} {unit_str})"
            )

        bolt11_invoice, _ = await get_lnurl_invoice(
            lnurl_data["callback_url"], amount_msat
        )

        await self.melt(bolt11_invoice)
        return amount

    # ───────────────────────── Proof Management ─────────────────────────────────

    # async def create_quote(self, amount: int, mint_url: str) -> tuple[str, str]:
    #     # TODO: Implement quote tracking as per NIP-60:
    #     # await self.publish_quote_tracker(
    #     #     quote_id=quote_resp["quote"],
    #     #     mint_url=mint_url,
    #     #     expiration=int(time.time()) + 14 * 24 * 60 * 60  # 2 weeks
    #     # )

    async def roll_over_proofs(
        self,
        *,
        spent_proofs: list[Proof],
        unspent_proofs: list[Proof],
        deleted_event_ids: list[str],
    ) -> str:
        return ""  # TODO: Implement roll over logic for wallet cleanup

    async def _consolidate_proofs(
        self, proofs: list[Proof], target_mint: str | None = None
    ) -> None:
        """Cleanup proofs by deleting events and updating wallet state.

        Consolidates proofs into optimal denominations and ensures they are
        properly stored on Nostr.

        Args:
            proofs: Proofs to consolidate (if None, consolidates all wallet proofs)
            target_mint: If provided, only consolidate proofs for this mint
        """
        # Get current wallet state if no proofs provided
        if not proofs:
            state = await self.fetch_wallet_state(check_proofs=True)
            proofs = state.proofs
            # TODO: these proofs are not used anywhere following logic needs to be updated

        # Get currency unit from the first proof
        unit = proofs[0].get("unit") or "sat"

        # Process each mint
        for mint_url, mint_proofs in state.proofs_by_mint.items():
            if not mint_proofs:
                continue

            # Calculate current balance for this mint
            current_balance = sum(p["amount"] for p in mint_proofs)

            # Check if already optimally denominated
            current_denoms: dict[int, int] = {}
            for proof in mint_proofs:
                if proof.get("unit") != unit:
                    raise WalletError(
                        f"All proofs must have the same unit. Mint {mint_url} has proofs with different units: {unit} != {proof.get('unit', 'sat')}"
                    )
                amount = proof["amount"]
                current_denoms[amount] = current_denoms.get(amount, 0) + 1

            # Calculate optimal denominations for the balance
            optimal_denoms = await self._calculate_optimal_denominations(
                current_balance, mint_url, unit
            )

            # Check if current denominations match optimal
            needs_consolidation = False
            for denom, count in optimal_denoms.items():
                if current_denoms.get(denom, 0) != count:
                    needs_consolidation = True
                    break

            if not needs_consolidation:
                continue  # Already optimal

            try:
                # Use the new abstracted swap method
                new_proofs = await self._swap_proof_denominations(
                    mint_proofs, optimal_denoms, mint_url, unit
                )

                # Store new proofs on Nostr
                await self.store_proofs(new_proofs)
            except Exception as e:
                print(f"Warning: Failed to consolidate proofs for {mint_url}: {e}")
                continue

    async def _calculate_optimal_denominations(
        self, amount: int, mint_url: str, currency: CurrencyUnit
    ) -> dict[int, int]:
        mint = self._get_mint(mint_url or self._primary_mint_url())
        currencies = await mint.get_currencies()
        if currency not in currencies:
            raise WalletError(f"Currency {currency} not supported by mint {mint.url}")
        available_denoms = await mint.get_denominations_for_currency(currency)
        return Mint.calculate_optimal_split(amount, available_denoms)

    async def _select_proofs(
        self,
        proofs: list[Proof],
        amount: int,
        target_mint: str,
        unit: CurrencyUnit | None,
    ) -> tuple[list[Proof], list[Proof]]:
        """Select proofs for spending a specific amount using optimal selection.

        Uses a greedy algorithm to minimize the number of proofs and change.

        Args:
            proofs: Available proofs to select from
            amount: Amount to select

        Returns:
            Tuple of (selected_output_proofs, consumed_input_proofs)
            - selected_output_proofs: Proofs that sum to exactly the requested amount
            - consumed_input_proofs: Original proofs that were consumed in the process

        Raises:
            WalletError: If insufficient proofs available
        """
        # Validate proofs first
        valid_proofs = await self._validate_proofs_with_cache(proofs)
        valid_available = sum(p["amount"] for p in valid_proofs)

        if valid_available < amount:
            raise WalletError(
                f"Insufficient balance: need {amount}, have {valid_available}"
            )

        # check if enough balance in proofs from target mint
        target_mint_proofs = [p for p in valid_proofs if p.get("mint") == target_mint]
        target_mint_balance = sum(p["amount"] for p in target_mint_proofs)
        if target_mint_balance < amount:
            raise WalletError(
                f"Insufficient balance at mint {target_mint}: need {amount}, have {target_mint_balance}"
            )

        # Use greedy algorithm to select minimum proofs needed
        target_mint_proofs.sort(key=lambda p: p["amount"], reverse=True)
        selected_input_proofs: list[Proof] = []
        selected_total = 0

        for proof in target_mint_proofs:
            if selected_total >= amount:
                break
            selected_input_proofs.append(proof)
            selected_total += int(proof["amount"])  # Ensure integer arithmetic

        if selected_total < amount:
            raise WalletError(
                f"Insufficient balance in target mint: need {amount}, have {target_mint_balance}"
            )

        # If we have exact amount, return the proofs
        if selected_total == amount:
            return selected_input_proofs, selected_input_proofs

        # Otherwise, we need to split proofs to get exact amount
        # Calculate expected input fees for the swap
        mint = self._get_mint(target_mint)
        input_fees = await self.calculate_total_input_fees(mint, selected_input_proofs)

        # Adjust target denominations to account for fees
        # The equation is: inputs - fees = outputs
        # So: outputs = inputs - fees = selected_total - input_fees
        output_amount = int(selected_total - input_fees)

        if unit is None:
            # TODO: this is a hack to get the default unit for the mint
            # TODO: this should be handled in more complex way where it denominates proofs per unit
            print("No unit provided, using default 'sat'")
            unit = "sat"

        # Recalculate denominations for the actual output amount
        send_denoms = await self._calculate_optimal_denominations(
            amount, target_mint, unit
        )
        change_amount = int(output_amount - amount)  # Ensure integer

        if change_amount < 0:
            raise WalletError(
                f"Insufficient amount after fees: need {amount}, have {output_amount} "
                f"(after {input_fees} sats in fees)"
            )

        change_denoms = await self._calculate_optimal_denominations(
            change_amount, target_mint, unit
        )

        # Combine send and change denominations
        target_denoms = send_denoms.copy()
        for denom, count in change_denoms.items():
            target_denoms[denom] = target_denoms.get(denom, 0) + count

        # Swap the selected proofs for the target denominations
        new_proofs = await self._swap_proof_denominations(
            selected_input_proofs, target_denoms, target_mint, unit
        )

        # Select exactly the amount needed for sending
        selected_proofs: list[Proof] = []
        change_proofs: list[Proof] = []
        used_proofs: set[str] = set()
        remaining_amount = amount

        # Select proofs to meet the exact amount
        for proof in sorted(new_proofs, key=lambda p: p["amount"], reverse=True):
            proof_id = f"{proof['secret']}:{proof['C']}"
            if proof_id in used_proofs:
                continue

            if remaining_amount > 0 and proof["amount"] <= remaining_amount:
                selected_proofs.append(proof)
                used_proofs.add(proof_id)
                remaining_amount -= proof["amount"]
            else:
                # This is change
                change_proofs.append(proof)

        if remaining_amount > 0:
            raise WalletError(
                f"Could not select exact amount: {remaining_amount} sats short"
            )

        # Store only the change proofs (not the ones we're sending!)
        await self.store_proofs(change_proofs)

        return selected_proofs, selected_input_proofs

    async def _swap_proof_denominations(
        self,
        proofs: list[Proof],
        target_denominations: dict[int, int],
        mint_url: str,
        currency: CurrencyUnit,
    ) -> list[Proof]:
        """Swap proofs to specific target denominations.

        This method abstracts the process of swapping proofs for new ones with
        specific denominations. It handles keysets, blinding, swapping, and unblinding.

        Args:
            proofs: List of proofs to swap
            target_denominations: Dict of denomination -> count
                                 e.g., {1: 5, 2: 3, 4: 1} = 5x1sat, 3x2sat, 1x4sat
            mint_url: Mint URL (defaults to first proof's mint or wallet's primary)

        Returns:
            List of new proofs with target denominations

        Raises:
            WalletError: If swap fails or amounts don't match
        """
        if not proofs:
            return []

        if not mint_url:
            raise WalletError("No mint URL available")

        # Get mint instance
        mint = self._get_mint(mint_url)

        # Calculate input fees for these proofs
        input_fees = await self.calculate_total_input_fees(mint, proofs)

        # Calculate total amounts
        input_amount = sum(p["amount"] for p in proofs)
        target_amount = sum(
            denom * count for denom, count in target_denominations.items()
        )

        # The correct balance equation is: inputs - fees = outputs
        expected_output_amount = input_amount - input_fees

        if target_amount != expected_output_amount:
            raise WalletError(
                f"Amount mismatch: input={input_amount}, fees={input_fees}, "
                f"expected_output={expected_output_amount}, target={target_amount}"
            )

        # TODO: Implement this
        # check if proofs are already in target denominations
        # return proofs if they are

        # Convert to mint proof format
        mint_proofs = proofs

        active_keysets = await mint.get_active_keysets()
        keyset = next((ks for ks in active_keysets if ks.get("unit") == currency), None)
        if not keyset:
            raise WalletError(
                f"No keyset found for currency {currency} on mint {mint.url}"
            )

        # Create blinded messages for target denominations
        outputs: list[BlindedMessage] = []
        secrets: list[str] = []
        blinding_factors: list[str] = []

        for denomination, count in sorted(target_denominations.items()):
            for _ in range(count):
                secret, r_hex, blinded_msg = create_blinded_message_with_secret(
                    denomination, keyset["id"]
                )
                outputs.append(blinded_msg)
                secrets.append(secret)
                blinding_factors.append(r_hex)

        # Perform swap
        # Cast to ProofComplete since swap expects it (ProofComplete extends Proof with optional fields)
        swap_resp = await mint.swap(
            inputs=cast(list[ProofComplete], mint_proofs), outputs=outputs
        )

        mint_keys = keyset["keys"]

        # Unblind signatures to create new proofs
        new_proofs: list[Proof] = []
        for i, sig in enumerate(swap_resp["signatures"]):
            # Get the public key for this amount
            amount = sig["amount"]
            mint_pubkey = get_mint_pubkey_for_amount(mint_keys, amount)
            if not mint_pubkey:
                raise WalletError(f"Could not find mint public key for amount {amount}")

            # Unblind the signature
            C_ = PublicKey(bytes.fromhex(sig["C_"]))
            r = bytes.fromhex(blinding_factors[i])
            C = unblind_signature(C_, r, mint_pubkey)

            new_proofs.append(
                Proof(
                    id=sig["id"],
                    amount=sig["amount"],
                    secret=secrets[
                        i
                    ],  # Already hex from create_blinded_message_with_secret
                    C=C.format(compressed=True).hex(),
                    mint=mint_url,
                    unit=currency,
                )
            )

        return new_proofs

    async def _mark_proofs_as_spent(self, spent_proofs: list[Proof]) -> None:
        """Mark proofs as spent following NIP-60 state transitions.

        This creates proper rollover events with 'del' fields to mark old events as superseded,
        ensuring wallet state consistency even on relays that don't support deletion events.

        Args:
            spent_proofs: List of proofs to mark as spent
        """
        # TODO check this
        if not spent_proofs:
            return

        # 1. Get current state to find which events contain spent proofs
        state = await self.fetch_wallet_state(
            check_proofs=False, check_local_backups=False
        )

        if not state.proof_to_event_id:
            # No mapping available, nothing to rollover
            return

        # 2. Find which events need updating (contain spent proofs)
        spent_proof_ids = {f"{p['secret']}:{p['C']}" for p in spent_proofs}
        events_with_spent_proofs: dict[str, list[Proof]] = {}

        # Group all proofs by their event IDs
        for proof in state.proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            event_id = state.proof_to_event_id.get(proof_id)

            if event_id and event_id != "__pending__":
                if event_id not in events_with_spent_proofs:
                    events_with_spent_proofs[event_id] = []
                events_with_spent_proofs[event_id].append(proof)

        # 3. Process each affected event
        events_to_delete = []
        new_event_ids = []

        for event_id, event_proofs in events_with_spent_proofs.items():
            # Check if this event contains any spent proofs
            has_spent_proofs = any(
                f"{p['secret']}:{p['C']}" in spent_proof_ids for p in event_proofs
            )

            if not has_spent_proofs:
                continue

            # Find unspent proofs from this event
            unspent_proofs = [
                p
                for p in event_proofs
                if f"{p['secret']}:{p['C']}" not in spent_proof_ids
            ]

            events_to_delete.append(event_id)

            if unspent_proofs:
                # Create new event with remaining proofs
                try:
                    new_id = await self.event_manager.publish_token_event(
                        unspent_proofs, deleted_token_ids=[event_id]
                    )
                    new_event_ids.append(new_id)
                except Exception as e:
                    print(
                        f"Warning: Failed to create rollover event for {event_id}: {e}"
                    )
                    # Continue processing other events

        # 4. Try to delete old events (best effort - don't fail if relay doesn't support it)
        for event_id in events_to_delete:
            try:
                await self.event_manager.delete_token_event(event_id)
            except Exception as e:
                # Deletion failed - that's okay, the 'del' field handles supersession
                print(
                    f"Note: Could not delete event {event_id} (relay may not support deletions): {e}"
                )

        # 5. Create spending history (optional but recommended)
        if events_to_delete or new_event_ids:
            try:
                # Group spent proofs by unit for proper history tracking
                spent_by_unit: dict[str, int] = {}
                for proof in spent_proofs:
                    unit_str = str(proof.get("unit", "sat"))  # Ensure it's a string
                    spent_by_unit[unit_str] = (
                        spent_by_unit.get(unit_str, 0) + proof["amount"]
                    )

                # Create spending history for each unit
                for unit, amount in spent_by_unit.items():
                    await self.event_manager.publish_spending_history(
                        direction="out",
                        amount=amount,
                        unit=unit,
                        created_token_ids=new_event_ids
                        if unit == list(spent_by_unit.keys())[0]
                        else None,
                        destroyed_token_ids=events_to_delete
                        if unit == list(spent_by_unit.keys())[0]
                        else None,
                    )
            except Exception as e:
                print(f"Warning: Failed to create spending history: {e}")

        # 6. Update local cache for spent proofs
        for proof in spent_proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            self._cache_proof_state(proof_id, "SPENT")

    async def store_proofs(self, proofs: list[Proof]) -> None:
        """Make sure proofs are stored on Nostr.

        This method ensures proofs are backed up to Nostr relays for recovery.
        It handles deduplication, retries, and temporary local backup.

        Args:
            proofs: List of proofs to store

        Raises:
            WalletError: If unable to publish to any relay after retries
        """
        if not proofs:
            return  # Nothing to store

        backup_dir = Path.cwd() / "proof_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        backup_file = backup_dir / f"proofs_{timestamp}_{secrets.token_hex(8)}.json"

        backup_data = {
            "timestamp": timestamp,
            "proofs": proofs,
            "mint_urls": list(set(p.get("mint", "") for p in proofs if p.get("mint"))),
        }

        try:
            with open(backup_file, "w") as f:
                json.dump(backup_data, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to create local backup: {e}")

        # Check which proofs are already stored
        state = await self.fetch_wallet_state(
            check_proofs=False, check_local_backups=False
        )
        existing_proofs = set()

        for proof in state.proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            existing_proofs.add(proof_id)

        # Filter out already stored proofs
        new_proofs = []
        for proof in proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"
            if proof_id not in existing_proofs:
                new_proofs.append(proof)

        if not new_proofs:
            return

        # Group new proofs by mint
        new_proofs_by_mint: dict[str, list[Proof]] = {}
        for proof in new_proofs:
            mint_url = proof.get("mint", "")
            if mint_url:
                if mint_url not in new_proofs_by_mint:
                    new_proofs_by_mint[mint_url] = []
                new_proofs_by_mint[mint_url].append(proof)

        # Publish token events for each mint
        published_count = 0
        failed_mints = []

        for mint_url, mint_proofs in new_proofs_by_mint.items():
            try:
                # Publish token event
                event_id = await self.event_manager.publish_token_event(mint_proofs)
                published_count += len(mint_proofs)

                # Verify event was published by fetching it
                max_retries = 3
                retry_delay = 1.0

                for retry in range(max_retries):
                    await asyncio.sleep(retry_delay)

                    # Try to fetch the event we just published
                    # Note: fetch_wallet_events doesn't support filtering by ID,
                    # so we just trust the publish succeeded
                    # In production, could implement a specific fetch by ID method
                    await asyncio.sleep(retry_delay)

                    # For now, assume successful after a delay
                    if retry > 0:  # Give it at least one retry
                        break

                    retry_delay *= 2  # Exponential backoff
                else:
                    # Failed to verify after retries
                    print(
                        f"Warning: Could not verify token event {event_id} was published"
                    )

            except Exception as e:
                print(f"Error publishing proofs for mint {mint_url}: {e}")
                failed_mints.append(mint_url)

                # Spawn background task for retry
                asyncio.create_task(
                    self._retry_store_proofs(mint_proofs, mint_url, backup_file)
                )

        if published_count > 0:
            print(f"✅ Published {published_count} new proofs to Nostr")

        if failed_mints:
            print(f"⚠️  Failed to publish proofs for mints: {', '.join(failed_mints)}")
            print("   Background retry tasks have been started.")

    async def _retry_store_proofs(
        self, proofs: list[Proof], mint_url: str, backup_file: Path
    ) -> None:
        """Background task to retry storing proofs."""
        max_retries = 5
        base_delay = 10.0  # Start with 10 second delay

        for retry in range(max_retries):
            await asyncio.sleep(base_delay * (2**retry))  # Exponential backoff

            try:
                event_id = await self.event_manager.publish_token_event(proofs)
                print(event_id)
                print(
                    f"✅ Successfully published proofs for {mint_url} on retry {retry + 1}"
                )

                # Try to clean up backup file
                try:
                    if backup_file.exists():
                        # Check if this was the last mint
                        with open(backup_file, "r") as f:
                            backup_data = json.load(f)

                        # Remove successfully stored proofs from backup
                        remaining_proofs = []
                        stored_ids = set(f"{p['secret']}:{p['C']}" for p in proofs)

                        for p in backup_data["proofs"]:
                            if f"{p['secret']}:{p['C']}" not in stored_ids:
                                remaining_proofs.append(p)

                        if remaining_proofs:
                            # Update backup with remaining proofs
                            backup_data["proofs"] = remaining_proofs
                            with open(backup_file, "w") as f:
                                json.dump(backup_data, f, indent=2)
                        else:
                            # All proofs stored - verify one more time before deletion
                            # Fetch state to ensure proofs are really on relays
                            await asyncio.sleep(2.0)  # Give relays time to propagate
                            try:
                                state = await self.fetch_wallet_state(
                                    check_proofs=False, check_local_backups=False
                                )
                                stored_proof_ids = set(
                                    f"{p['secret']}:{p['C']}" for p in state.proofs
                                )
                                all_stored = all(
                                    f"{p['secret']}:{p['C']}" in stored_proof_ids
                                    for p in proofs
                                )

                                if all_stored:
                                    backup_file.unlink()
                                    print(
                                        f"    🗑️  Verified and deleted backup: {backup_file.name}"
                                    )
                                else:
                                    print(
                                        f"    ⚠️  Keeping backup (verification failed): {backup_file.name}"
                                    )
                            except Exception as e:
                                print(
                                    f"    ⚠️  Keeping backup (verification error): {e}"
                                )
                except Exception:
                    pass  # Ignore backup cleanup errors

                return  # Success

            except Exception as e:
                if retry == max_retries - 1:
                    print(
                        f"❌ Failed to store proofs for {mint_url} after {max_retries} retries: {e}"
                    )
                    print(f"   Manual recovery may be needed from: {backup_file}")

    async def remote_redeem_proofs(
        self,
        proofs: list[Proof],
        target_mint: str,
        target_unit: CurrencyUnit = "sat",
    ) -> tuple[int, CurrencyUnit]:
        if not proofs:
            return 0, "sat"

        # Assume all proofs from same mint
        source_mint_url = proofs[0]["mint"]
        if not all(p["mint"] == source_mint_url for p in proofs):
            raise WalletError("Proofs must be from the same mint for transfer")

        source_mint = Mint(source_mint_url)

        total_amount_sats = await sats_value_of_proofs(proofs)
        if total_amount_sats == 0:
            return 0, "sat"

        invoice, mint_task = await self.mint_async(
            total_amount_sats, mint_url=target_mint
        )

        melt_quote = await source_mint.create_melt_quote(
            request=invoice,
        )
        fee_reserve = melt_quote.get("fee_reserve", 0)
        total_needed = total_amount_sats + fee_reserve
        self.raise_if_insufficient_balance(await self.get_balance(), total_needed)

        melt_resp = await source_mint.melt(
            quote=melt_quote["quote"], inputs=cast(list[ProofComplete], proofs)
        )

        # Check if payment was successful
        if not melt_resp.get("paid", False):
            raise WalletError(
                f"Lightning payment failed. State: {melt_resp.get('state', 'unknown')}"
            )

        await mint_task

        return total_amount_sats, target_unit

    async def transfer_balance_to_mint(
        self,
        amount_sats: int,
        *,
        to_mint: str,
        from_mint: str | None = None,
        target_unit: CurrencyUnit | None = None,
        exclude_mints: list[str] = [],
    ) -> tuple[int, CurrencyUnit]:
        state = await self.fetch_wallet_state(check_proofs=True)

        if not from_mint:
            mint_sats_balances: dict[str, int] = {}
            for mint in set(p["mint"] for p in state.proofs if "mint" in p):
                if mint in exclude_mints or mint == to_mint:
                    continue
                mint_proofs = [p for p in state.proofs if p["mint"] == mint]
                mint_sats = await sats_value_of_proofs(mint_proofs)
                if mint_sats > 0:
                    mint_sats_balances[mint] = mint_sats
            if not mint_sats_balances:
                raise WalletError("No source mints with balance available")
            from_mint = max(mint_sats_balances, key=lambda k: mint_sats_balances[k])

        proofs_from_mint = [p for p in state.proofs if p.get("mint") == from_mint]
        total_sats_from_mint = await sats_value_of_proofs(proofs_from_mint)
        transfer_sats = min(amount_sats, total_sats_from_mint)
        if transfer_sats <= 0:
            raise WalletError(
                f"Insufficient balance. Need at least {amount_sats}, but have 0 in available mints"
            )
        if not target_unit:
            target_unit = "sat"

        invoice, async_task = await self.mint_async(
            transfer_sats, mint_url=to_mint, unit=target_unit
        )

        await self.melt(invoice, target_mint=from_mint)

        await async_task

        remaining = amount_sats - transfer_sats
        if remaining > 0:
            await self.transfer_balance_to_mint(
                remaining,
                to_mint=to_mint,
                from_mint=None,
                target_unit=target_unit,
                exclude_mints=cast(list[str], exclude_mints + [from_mint]),
            )

        return amount_sats, target_unit

    # ───────────────────────── Helper Methods ─────────────────────────────────

    def _get_mint(self, mint_url: str) -> Mint:
        """Get or create mint instance for URL."""
        if mint_url not in self.mints:
            self.mints[mint_url] = Mint(mint_url)
        return self.mints[mint_url]

    def _serialize_proofs_for_token(
        self,
        proofs: list[Proof],
        mint_url: str,
        token_version: int,
        currency: CurrencyUnit,
    ) -> str:
        """Serialize proofs into a Cashu token format (V3 or V4)."""
        if token_version == 3:
            return self._serialize_proofs_v3(proofs, mint_url, currency)
        elif token_version == 4:
            return self._serialize_proofs_v4(proofs, mint_url, currency)
        else:
            raise ValueError(f"Unsupported token version: {token_version}")

    def _serialize_proofs_v3(
        self, proofs: list[Proof], mint_url: str, currency: CurrencyUnit
    ) -> str:
        """Serialize proofs into CashuA (V3) token format."""
        # Proofs are already stored with hex secrets internally
        token_proofs = []
        for proof in proofs:
            token_proofs.append(
                {
                    "id": proof["id"],
                    "amount": proof["amount"],
                    "secret": proof["secret"],  # Already hex
                    "C": proof["C"],
                }
            )

        # CashuA token format: cashuA<base64url(json)>
        token_data = {
            "token": [{"mint": mint_url, "proofs": token_proofs}],
            "unit": currency,
            "memo": "NIP-60 wallet transfer",
        }
        json_str = json.dumps(token_data, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode().rstrip("=")
        return f"cashuA{encoded}"

    def _serialize_proofs_v4(
        self, proofs: list[Proof], mint_url: str, currency: CurrencyUnit
    ) -> str:
        """Serialize proofs into CashuB (V4) token format using CBOR."""
        if cbor2 is None:
            raise ImportError("cbor2 library required for CashuB (V4) tokens")

        # Group proofs by keyset ID for V4 format
        proofs_by_keyset: dict[str, list[Proof]] = {}
        for proof in proofs:
            keyset_id = proof["id"]
            if keyset_id not in proofs_by_keyset:
                proofs_by_keyset[keyset_id] = []
            proofs_by_keyset[keyset_id].append(proof)

        # Build V4 token structure
        tokens = []
        for keyset_id, keyset_proofs in proofs_by_keyset.items():
            # Convert keyset ID from hex string to bytes
            keyset_id_bytes = bytes.fromhex(keyset_id)

            # Convert proofs to V4 format
            v4_proofs = []
            for proof in keyset_proofs:
                v4_proofs.append(
                    {
                        "a": proof["amount"],  # amount
                        "s": proof["secret"],  # secret (already hex string)
                        "c": bytes.fromhex(proof["C"]),  # C as bytes
                    }
                )

            tokens.append(
                {
                    "i": keyset_id_bytes,  # keyset id as bytes
                    "p": v4_proofs,  # proofs array
                }
            )

        # CashuB token structure
        token_data = {
            "m": mint_url,  # mint URL
            "u": currency,  # unit
            "t": tokens,  # tokens array
        }

        # Encode with CBOR and base64url
        cbor_bytes = cbor2.dumps(token_data)
        encoded = base64.urlsafe_b64encode(cbor_bytes).decode().rstrip("=")
        return f"cashuB{encoded}"

    def _parse_cashu_token(self, token: str) -> tuple[str, CurrencyUnit, list[Proof]]:
        """Parse Cashu token and return (mint_url, unit, proofs)."""
        if not token.startswith("cashu"):
            raise ValueError("Invalid token format")

        # Check token version
        if token.startswith("cashuA"):
            # Version 3 - JSON format
            encoded = token[6:]  # Remove "cashuA"
            # Add correct padding – (-len) % 4 equals 0,1,2,3
            encoded += "=" * ((-len(encoded)) % 4)

            decoded = base64.urlsafe_b64decode(encoded).decode()
            token_data = json.loads(decoded)

            # Extract mint and proofs from JSON format
            mint_info = token_data["token"][0]
            # Safely get unit, defaulting to "sat" if not present (as per Cashu V3 common practice)
            unit_str = token_data.get("unit", "sat")
            # Cast to CurrencyUnit - validate it's a known unit
            token_unit: CurrencyUnit = cast(CurrencyUnit, unit_str)
            token_proofs = mint_info["proofs"]

            # Return proofs with hex secrets (standard Cashu format)
            parsed_proofs: list[Proof] = []
            for proof in token_proofs:
                parsed_proofs.append(
                    Proof(
                        id=proof["id"],
                        amount=proof["amount"],
                        secret=proof["secret"],  # Already hex in Cashu tokens
                        C=proof["C"],
                        mint=mint_info["mint"],
                        unit=token_unit,
                    )
                )

            return mint_info["mint"], token_unit, parsed_proofs

        elif token.startswith("cashuB"):
            # Version 4 - CBOR format
            if cbor2 is None:
                raise ImportError("cbor2 library required for cashuB tokens")

            encoded = token[6:]  # Remove "cashuB"
            # Add padding for base64
            encoded += "=" * ((-len(encoded)) % 4)

            decoded_bytes = base64.urlsafe_b64decode(encoded)
            token_data = cbor2.loads(decoded_bytes)

            # Extract from CBOR format - different structure
            # 'm' = mint URL, 'u' = unit, 't' = tokens array
            mint_url = token_data["m"]
            unit_str = token_data["u"]
            # Cast to CurrencyUnit
            cbor_unit: CurrencyUnit = cast(CurrencyUnit, unit_str)
            proofs = []

            # Each token in 't' has 'i' (keyset id) and 'p' (proofs)
            for token_entry in token_data["t"]:
                keyset_id = token_entry["i"].hex()  # Convert bytes to hex
                for proof in token_entry["p"]:
                    # CBOR format already has hex secret
                    # Convert CBOR proof format to our Proof format
                    proofs.append(
                        Proof(
                            id=keyset_id,
                            amount=proof["a"],
                            secret=proof["s"],  # Already hex in CBOR format
                            C=proof["c"].hex(),  # Convert bytes to hex
                            mint=mint_url,
                            unit=cbor_unit,
                        )
                    )

            return mint_url, cbor_unit, proofs
        else:
            raise ValueError(f"Unknown token version: {token[:7]}")

    def raise_if_insufficient_balance(self, balance: int, amount: int) -> None:
        if balance < amount:
            raise WalletError(
                f"Insufficient balance. Need at least {amount} sat, but have {balance} sat"
            )

    # ───────────────────────── Proof Validation ────────────────────────────────

    def _compute_proof_y_values(self, proofs: list[Proof]) -> list[str]:
        """Compute Y values for proofs to use in check_state API.

        Args:
            proofs: List of proof dictionaries

        Returns:
            List of Y values (hex encoded compressed public keys)
        """
        y_values = []
        for proof in proofs:
            secret_hex = proof["secret"]  # Already hex internally

            # Hash to curve point using UTF-8 bytes of hex string (Cashu standard)
            secret_utf8_bytes = secret_hex.encode("utf-8")
            Y = hash_to_curve(secret_utf8_bytes)
            # Convert to compressed hex format
            y_hex = Y.format(compressed=True).hex()
            y_values.append(y_hex)
        return y_values

    def _is_proof_state_cached(self, proof_id: str) -> tuple[bool, str | None]:
        """Check if proof state is cached and still valid."""
        if proof_id in self._proof_state_cache:
            cache_entry = self._proof_state_cache[proof_id]
            timestamp = float(cache_entry.get("timestamp", 0))
            if time.time() - timestamp < self._cache_expiry:
                return True, cache_entry.get("state")
        return False, None

    def _cache_proof_state(self, proof_id: str, state: str) -> None:
        """Cache proof state with timestamp."""
        self._proof_state_cache[proof_id] = {
            "state": state,
            "timestamp": str(time.time()),
        }

        # Track spent proofs separately for faster lookup
        if state == "SPENT":
            self._known_spent_proofs.add(proof_id)

    def clear_spent_proof_cache(self) -> None:
        """Clear the spent proof cache to prevent memory growth."""
        self._proof_state_cache.clear()
        self._known_spent_proofs.clear()

    async def _validate_proofs_with_cache(self, proofs: list[Proof]) -> list[Proof]:
        """Validate proofs using cache to avoid re-checking spent proofs."""
        valid_proofs = []
        proofs_to_check: list[Proof] = []

        # First pass: check cache and filter out known spent proofs
        for proof in proofs:
            proof_id = f"{proof['secret']}:{proof['C']}"

            # Skip known spent proofs immediately
            if proof_id in self._known_spent_proofs:
                continue

            is_cached, cached_state = self._is_proof_state_cached(proof_id)
            if is_cached:
                if cached_state == "UNSPENT":
                    valid_proofs.append(proof)
                # SPENT proofs are filtered out (don't add to valid_proofs)
            else:
                proofs_to_check.append(proof)

        if proofs_to_check:
            for mint_url, mint_proofs in self._sort_proofs_by_mint(
                proofs_to_check
            ).items():
                try:
                    mint = self._get_mint(mint_url)
                    y_values = self._compute_proof_y_values(mint_proofs)
                    state_response = await mint.check_state(Ys=y_values)

                    for i, proof in enumerate(mint_proofs):
                        proof_id = f"{proof['secret']}:{proof['C']}"
                        if i < len(state_response["states"]):
                            state_info = state_response["states"][i]
                            state = state_info.get("state", "UNKNOWN")

                            # Cache the result
                            self._cache_proof_state(proof_id, state)

                            # Only include unspent proofs
                            if state == "UNSPENT":
                                valid_proofs.append(proof)

                        else:
                            # No state info - assume valid but don't cache
                            valid_proofs.append(proof)

                except Exception:
                    # If validation fails, include proofs but don't cache
                    valid_proofs.extend(mint_proofs)

        return valid_proofs

    async def fetch_wallet_state(
        self, *, check_proofs: bool = True, check_local_backups: bool = True
    ) -> WalletState:
        """Fetch wallet, token events and compute balance.

        Args:
            check_proofs: If True, validate all proofs with mint before returning state
            check_local_backups: If True, scan local backups for missing proofs
        """
        # Clear spent proof cache to ensure fresh validation
        if check_proofs:
            self.clear_spent_proof_cache()

        # Fetch all wallet-related events
        all_events = await self.relay_manager.fetch_wallet_events(
            get_pubkey(self._privkey)
        )

        # Find the newest wallet event (replaceable events should use latest timestamp)
        wallet_events = [e for e in all_events if e["kind"] == EventKind.Wallet]
        wallet_event = None
        if wallet_events:
            # Sort by created_at timestamp and take the newest
            wallet_event = max(wallet_events, key=lambda e: e["created_at"])

        # Parse wallet metadata
        # TODO this should not always fetch the wallet event
        if wallet_event:
            try:
                decrypted = nip44_decrypt(wallet_event["content"], self._privkey)
                wallet_data = json.loads(decrypted)

                # Parse wallet event data
                event_mint_urls = []
                for item in wallet_data:
                    if item[0] == "mint":
                        event_mint_urls.append(item[1])
                    elif item[0] == "privkey":
                        self.wallet_privkey = item[1]

                # If wallet event contains mint URLs, use those as the source of truth
                # This replaces any mint URLs from constructor or environment
                if event_mint_urls:
                    # Deduplicate mint URLs from wallet event (in case they were stored with duplicates)
                    self.mint_urls = list(dict.fromkeys(event_mint_urls))
                    # Also update the event manager with the correct mint URLs
                    self.event_manager.mint_urls = self.mint_urls
            except Exception as e:
                # Skip wallet event if it can't be decrypted
                print(f"Warning: Could not decrypt wallet event: {e}")

        # Collect token events
        token_events = [e for e in all_events if e["kind"] == EventKind.Token]

        # Track deleted token events
        deleted_ids = set()
        for event in all_events:
            if event["kind"] == EventKind.Delete:
                for tag in event["tags"]:
                    if tag[0] == "e":
                        deleted_ids.add(tag[1])

        # Aggregate unspent proofs taking into account NIP-60 roll-overs and avoiding duplicates
        all_proofs: list[Proof] = []
        proof_to_event_id: dict[str, str] = {}

        # Index events newest → oldest so that when we encounter a replacement first we can ignore the ones it deletes later
        token_events_sorted = sorted(
            token_events, key=lambda e: e["created_at"], reverse=True
        )

        invalid_token_ids: set[str] = set(deleted_ids)
        proof_seen: set[str] = set()

        for event in token_events_sorted:
            if event["id"] in invalid_token_ids:
                continue

            try:
                decrypted = nip44_decrypt(event["content"], self._privkey)
                token_data = json.loads(decrypted)
            except Exception:
                # Skip this event if it can't be decrypted - likely from old key or corrupted
                continue

            # Mark tokens referenced in the "del" field as superseded
            del_ids = token_data.get("del", [])
            if del_ids:
                for old_id in del_ids:
                    invalid_token_ids.add(old_id)

            # Check again if this event was marked invalid by a newer event
            if event["id"] in invalid_token_ids:
                continue

            proofs = token_data.get("proofs", [])
            mint_url = token_data.get("mint")
            if not mint_url:
                raise WalletError("No mint URL found in token event")

            for proof in proofs:
                # Convert from NIP-60 format (base64) to internal format (hex)
                # NIP-60 stores secrets as base64, but internally we use hex
                secret = proof["secret"]
                try:
                    # Try to decode from base64 (NIP-60 format)
                    secret_bytes = base64.b64decode(secret)
                    hex_secret = secret_bytes.hex()
                except Exception:
                    # If it fails, assume it's already hex (backwards compatibility)
                    hex_secret = secret

                proof_id = f"{hex_secret}:{proof['C']}"
                if proof_id in proof_seen:
                    continue
                proof_seen.add(proof_id)

                # Add mint URL to proof with hex secret
                # Get unit from proof if available, otherwise default to "sat"
                proof_unit = cast(
                    CurrencyUnit, proof.get("unit", proof.get("u", "sat"))
                )
                proof_with_mint: Proof = Proof(
                    id=proof["id"],
                    amount=proof["amount"],
                    secret=hex_secret,  # Store as hex internally
                    C=proof["C"],
                    mint=mint_url,
                    unit=proof_unit,
                )
                all_proofs.append(proof_with_mint)
                proof_to_event_id[proof_id] = event["id"]

        # Include pending proofs from relay manager
        pending_token_data = self.relay_manager.get_pending_proofs()

        for token_data in pending_token_data:
            mint_url = token_data.get("mint")
            if not mint_url or not isinstance(mint_url, str):
                raise WalletError("No mint URL found in pending token event")

            proofs = token_data.get("proofs", [])
            if not isinstance(proofs, list):
                continue

            for proof in proofs:
                # Convert from NIP-60 format (base64) to internal format (hex)
                secret = proof["secret"]
                try:
                    # Try to decode from base64 (NIP-60 format)
                    secret_bytes = base64.b64decode(secret)
                    hex_secret = secret_bytes.hex()
                except Exception:
                    # If it fails, assume it's already hex
                    hex_secret = secret

                proof_id = f"{hex_secret}:{proof['C']}"
                if proof_id in proof_seen:
                    continue
                proof_seen.add(proof_id)

                # Get unit from proof if available, otherwise default to "sat"
                proof_unit = cast(
                    CurrencyUnit, proof.get("unit", proof.get("u", "sat"))
                )

                # Mark pending proofs with a special event ID
                pending_proof_with_mint: Proof = Proof(
                    id=proof["id"],
                    amount=proof["amount"],
                    secret=hex_secret,  # Store as hex internally
                    C=proof["C"],
                    mint=mint_url,
                    unit=proof_unit,
                )
                all_proofs.append(pending_proof_with_mint)
                proof_to_event_id[proof_id] = "__pending__"  # Special marker

        # Validate proofs using cache system if requested
        if check_proofs and all_proofs:
            # Don't validate pending proofs (they haven't been published yet)
            non_pending_proofs = [
                p
                for p in all_proofs
                if proof_to_event_id.get(f"{p['secret']}:{p['C']}", "") != "__pending__"
            ]
            pending_proofs = [
                p
                for p in all_proofs
                if proof_to_event_id.get(f"{p['secret']}:{p['C']}", "") == "__pending__"
            ]

            # Validate only non-pending proofs
            validated_proofs = await self._validate_proofs_with_cache(
                non_pending_proofs
            )

            # Add back pending proofs (assume they're valid)
            all_proofs = validated_proofs + pending_proofs

        # Check local backups for missing proofs if requested
        if check_local_backups:
            backup_dir = Path.cwd() / "proof_backups"
            if backup_dir.exists() and any(backup_dir.glob("proofs_*.json")):
                # Check if we've recently checked backups (within last 60 seconds)
                last_check_file = backup_dir / ".last_check"
                should_check = True

                try:
                    if last_check_file.exists():
                        last_check_time = float(last_check_file.read_text().strip())
                        if time.time() - last_check_time < 60:
                            should_check = False
                except Exception:
                    pass

                if not should_check:
                    # Skip backup check if we just did it
                    return WalletState(
                        proofs=all_proofs, proof_to_event_id=proof_to_event_id
                    )
                # Check if we have any backup files with missing proofs
                existing_proof_ids = set(f"{p['secret']}:{p['C']}" for p in all_proofs)

                # Quick scan to see if there might be missing proofs
                has_missing = False
                for backup_file in backup_dir.glob("proofs_*.json"):
                    try:
                        with open(backup_file, "r") as f:
                            backup_data = json.load(f)
                        backup_proofs = backup_data.get("proofs", [])

                        for proof in backup_proofs:
                            proof_id = f"{proof['secret']}:{proof['C']}"
                            if proof_id not in existing_proof_ids:
                                has_missing = True
                                break
                    except Exception:
                        continue

                    if has_missing:
                        break

                # If we found missing proofs, run the recovery scan
                if has_missing:
                    print("\n⚠️  Detected local proof backups not synced to Nostr")
                    recovery_stats = await self.scan_and_recover_local_proofs(
                        auto_publish=True
                    )

                    # Update last check timestamp
                    try:
                        last_check_file = backup_dir / ".last_check"
                        last_check_file.write_text(str(time.time()))
                    except Exception:
                        pass

                    # If we recovered proofs, re-fetch the state to include them
                    if recovery_stats["recovered"] > 0:
                        print("🔄 Re-fetching wallet state after recovery...")
                        # Recursive call without check_local_backups to avoid infinite loop
                        return await self.fetch_wallet_state(
                            check_proofs=check_proofs, check_local_backups=False
                        )
                    elif (
                        recovery_stats["missing_from_nostr"] > 0
                        and recovery_stats["recovered"] == 0
                    ):
                        # All missing proofs were invalid/spent - clean up backup files
                        print(
                            "🧹 All proofs in backups are spent/invalid, cleaning up..."
                        )
                        await self._cleanup_spent_proof_backups()

        return WalletState(proofs=all_proofs, proof_to_event_id=proof_to_event_id)

    async def get_balance(
        self, unit: CurrencyUnit | None = None, *, check_proofs: bool = True
    ) -> int:
        """Get current wallet balance.

        Args:
            check_proofs: If True, validate all proofs with mint before returning balance

        Returns:
            Current balance in the wallet's currency unit

        Example:
            balance = await wallet.get_balance()
            print(f"Balance: {balance} sats")
        """
        state = await self.fetch_wallet_state(
            check_proofs=check_proofs, check_local_backups=True
        )
        if unit:
            if unit not in state.balance_by_unit:
                raise WalletError(f"Unsupported currency unit: {unit}")
            return state.balance_by_unit[unit]
        return await state.total_balance_sat()

    async def get_balance_by_mint(self, mint_url: str) -> int:
        """Get balance for a specific mint."""
        state = await self.fetch_wallet_state(check_proofs=True)
        return sum(p["amount"] for p in state.proofs if p["mint"] == mint_url)

    # ─────────────────────────────── Cleanup ──────────────────────────────────

    async def aclose(self) -> None:
        """Close underlying HTTP clients."""
        await self.mint_client.aclose()

        # Close relay manager connections
        await self.relay_manager.disconnect_all()

        # Close mint clients
        for mint in self.mints.values():
            await mint.aclose()

    # ───────────────────────── Async context manager ──────────────────────────

    async def __aenter__(self) -> Wallet:
        """Enter async context and connect to relays without auto-creating wallet events."""
        # Discover relays if none are set
        if not self.relay_urls:
            try:
                from .relay import get_relays_for_wallet

                self.relay_urls = await get_relays_for_wallet(
                    self._privkey, prompt_if_needed=True
                )
                # Update relay manager with discovered relays
                self.relay_manager.relay_urls = self.relay_urls
            except Exception:
                # If relay discovery fails, continue with empty relays
                # This allows offline operations
                pass

        # Just connect to relays, don't auto-create wallet events
        # Users must explicitly call initialize_wallet() or create_wallet_event()
        try:
            await self.relay_manager.get_relay_connections()
        except Exception:
            # If we can't connect to relays, that's okay -
            # user might just want to do offline operations
            pass
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401  (simple return)
        await self.aclose()

    # ───────────────────────── Fee Calculation ──────────────────────────────
    def calculate_input_fees(self, proofs: list[Proof], keyset_info: dict) -> int:
        """Calculate input fees based on number of proofs and keyset fee rate.

        Args:
            proofs: List of proofs being spent
            keyset_info: Keyset information containing input_fee_ppk

        Returns:
            Total input fees in base currency units (e.g., satoshis)

        Example:
            With input_fee_ppk=1000 (1 sat per proof) and 3 proofs:
            fee = (3 * 1000 + 999) // 1000 = 3 satoshis
        """
        input_fee_ppk = keyset_info.get("input_fee_ppk", 0)

        # Ensure input_fee_ppk is an integer (could be string from API)
        try:
            input_fee_ppk = int(input_fee_ppk)
        except (ValueError, TypeError):
            input_fee_ppk = 0

        if input_fee_ppk == 0:
            return 0

        # Sum up fees for all proofs and use ceiling division
        sum_fees = len(proofs) * input_fee_ppk
        return (sum_fees + 999) // 1000

    async def calculate_total_input_fees(self, mint: Mint, proofs: list[Proof]) -> int:
        """Calculate total input fees for proofs across different keysets.

        Args:
            mint: Mint instance to query keyset information
            proofs: List of proofs being spent

        Returns:
            Total input fees for all proofs
        """
        try:
            # Get keyset information from mint
            keysets = await mint.get_keysets_info()
            keyset_fees = {}

            # Build mapping of keyset_id -> input_fee_ppk
            for keyset in keysets:
                keyset_fees[keyset["id"]] = keyset.get("input_fee_ppk", 0)

            # Sum fees for each proof based on its keyset
            sum_fees = 0
            for proof in proofs:
                keyset_id = proof["id"]
                fee_rate = keyset_fees.get(keyset_id, 0)
                # Ensure fee_rate is an integer (could be string from API)
                try:
                    fee_rate = int(fee_rate)
                except (ValueError, TypeError):
                    fee_rate = 0
                sum_fees += fee_rate

            # Use ceiling division to round up fees (matches mint behavior)
            return (sum_fees + 999) // 1000

        except Exception:
            # Fallback to zero fees if keyset info unavailable
            # This ensures wallet doesn't break when connecting to older mints
            return 0

    def estimate_transaction_fees(
        self,
        input_proofs: list[Proof],
        keyset_info: dict,
        lightning_fee_reserve: int = 0,
    ) -> tuple[int, int]:
        """Estimate total transaction fees including input fees and lightning fees.

        Args:
            input_proofs: Proofs being spent as inputs
            keyset_info: Keyset information for input fee calculation
            lightning_fee_reserve: Lightning network fee reserve from melt quote

        Returns:
            Tuple of (input_fees, total_fees)
        """
        input_fees = self.calculate_input_fees(input_proofs, keyset_info)
        total_fees = input_fees + lightning_fee_reserve

        return input_fees, total_fees

    # ───────────────────────── Currency Validation ────────────────────────────

    def _convert_to_base_unit(self, amount: int, unit: CurrencyUnit) -> int:
        """Convert amount to the base unit (smallest denomination) for the currency.

        Args:
            amount: Amount in user-friendly units (e.g., dollars for USD)
            unit: Currency unit

        Returns:
            Amount in base units (e.g., cents for USD, satoshis for BTC)
        """
        # For fiat currencies, convert to cents (multiply by 100)
        if unit in ["usd", "eur", "gbp", "cad", "chf", "aud", "jpy", "cny", "inr"]:
            return amount * 100
        # For crypto, most are already in base units
        elif unit in ["sat", "msat"]:
            return amount
        elif unit == "btc":
            return amount * 100_000_000  # Convert BTC to satoshis
        # For stablecoins, typically use cents as well
        elif unit in ["usdt", "usdc", "dai"]:
            return amount * 100
        else:
            # Default to no conversion for unknown units
            return amount

    def _convert_from_base_unit(self, amount: int, unit: CurrencyUnit) -> float:
        """Convert amount from base unit to user-friendly units.

        Args:
            amount: Amount in base units (e.g., cents for USD)
            unit: Currency unit

        Returns:
            Amount in user-friendly units (e.g., dollars for USD)
        """
        # For fiat currencies, convert from cents (divide by 100)
        if unit in ["usd", "eur", "gbp", "cad", "chf", "aud", "jpy", "cny", "inr"]:
            return amount / 100
        # For crypto, most are already in user-friendly units
        elif unit in ["sat", "msat"]:
            return float(amount)
        elif unit == "btc":
            return amount / 100_000_000  # Convert satoshis to BTC
        # For stablecoins, typically use cents as well
        elif unit in ["usdt", "usdc", "dai"]:
            return amount / 100
        else:
            # Default to no conversion for unknown units
            return float(amount)

    async def get_mints_supporting_unit(self, unit: CurrencyUnit) -> list[str]:
        """Get list of mint URLs that support the specified currency unit.

        Args:
            unit: Currency unit to check support for

        Returns:
            List of mint URLs that support the unit
        """
        supporting_mints = []

        for mint_url in self.mint_urls:
            try:
                mint = self._get_mint(mint_url)
                keysets = await mint.get_keysets_info()

                # Check if any keyset supports the requested unit
                for keyset in keysets:
                    if keyset.get("unit") == unit:
                        supporting_mints.append(mint_url)
                        break

            except Exception as e:
                # Only print error if it's not a connection error
                error_msg = str(e).lower()
                if "connection" not in error_msg and "timeout" not in error_msg:
                    print(f"Error checking mint {mint_url}: {e}")
                continue

        return supporting_mints

    def _validate_currency_unit(self, unit: CurrencyUnit) -> None:
        """Validate currency unit is supported per NUT-01.

        Args:
            unit: Currency unit to validate

        Raises:
            ValueError: If currency unit is not supported
        """
        # Type checking ensures unit is valid CurrencyUnit at compile time
        # This method can be extended for runtime validation if needed
        if unit not in [
            "btc",
            "sat",
            "msat",
            "usd",
            "eur",
            "gbp",
            "jpy",
            "auth",
            "usdt",
            "usdc",
            "dai",
        ]:
            raise ValueError(f"Unsupported currency unit: {unit}")

    # ───────────────────────── Public Helper Methods ──────────────────────────

    def _get_pubkey(self) -> str:
        """Get the Nostr public key for this wallet."""
        return get_pubkey(self._privkey)

    def _generate_wallet_privkey(self) -> str:
        """Generate a new wallet private key for P2PK operations.

        This should only be called when creating a new wallet event for the first time.

        Returns:
            Hex-encoded private key
        """
        return generate_privkey()

    def _primary_mint_url(self) -> str:
        """Get the primary mint URL (first one when sorted).

        Returns:
            Primary mint URL

        Raises:
            WalletError: If no mint URLs configured
        """
        if not self.mint_urls:
            raise WalletError("No mint URLs configured")
        return sorted(self.mint_urls)[0]  # Use sorted order for consistency

    async def _select_mint_for_amount(
        self, amount: int, unit: CurrencyUnit, proofs: list[Proof] | None = None
    ) -> str:
        """Select the best mint that has sufficient balance for the given amount and unit.

        Args:
            amount: Amount needed in the specified unit
            unit: Currency unit
            proofs: Optional list of proofs to use (if None, fetches wallet state)

        Returns:
            Mint URL with sufficient balance

        Raises:
            WalletError: If no mint has sufficient balance
        """
        # Get proofs if not provided
        if proofs is None:
            state = await self.fetch_wallet_state(check_proofs=True)
            proofs = state.proofs

        # Filter proofs by the requested currency unit
        unit_proofs = [p for p in proofs if p.get("unit") == unit]

        if not unit_proofs:
            raise WalletError(
                f"Insufficient {unit.upper()} balance: No {unit.upper()} balance available"
            )

        # Calculate balances per mint for this unit
        mint_unit_balances: dict[str, int] = {}
        for proof in unit_proofs:
            proof_mint = proof.get("mint", "")
            if proof_mint:
                mint_unit_balances[proof_mint] = (
                    mint_unit_balances.get(proof_mint, 0) + proof["amount"]
                )

        # Find mints with sufficient balance
        suitable_mints = [
            m for m, balance in mint_unit_balances.items() if balance >= amount
        ]

        if not suitable_mints:
            total_unit_balance = sum(p["amount"] for p in unit_proofs)
            mint_details = ", ".join(
                f"{m}: {b}" for m, b in sorted(mint_unit_balances.items())
            )
            raise WalletError(
                f"Insufficient {unit.upper()} balance: need {amount}, have {total_unit_balance} "
                f"(distributed across mints: {mint_details})"
            )

        # Select the mint with the highest balance of this unit
        return max(suitable_mints, key=lambda m: mint_unit_balances[m])

    def _sort_proofs_by_mint(self, proofs: list[Proof]) -> dict[str, list[Proof]]:
        return {
            mint_url: [proof for proof in proofs if proof["mint"] == mint_url]
            for mint_url in set(proof["mint"] for proof in proofs)
        }

    async def _cleanup_spent_proof_backups(self) -> int:
        """Clean up backup files that only contain spent/invalid proofs.

        Returns:
            Number of backup files cleaned up
        """
        backup_dir = Path.cwd() / "proof_backups"
        if not backup_dir.exists():
            return 0

        # Get current valid proofs and known spent proofs
        state = await self.fetch_wallet_state(
            check_proofs=False, check_local_backups=False
        )
        valid_proof_ids = set(f"{p['secret']}:{p['C']}" for p in state.proofs)

        cleaned_count = 0
        backup_files = list(backup_dir.glob("proofs_*.json"))

        for backup_file in backup_files:
            try:
                with open(backup_file, "r") as f:
                    backup_data = json.load(f)

                backup_proofs = backup_data.get("proofs", [])
                if not backup_proofs:
                    # Empty backup file, remove it
                    backup_file.unlink()
                    cleaned_count += 1
                    print(f"   🗑️  Deleted empty backup: {backup_file.name}")
                    continue

                # Check if all proofs are spent/invalid
                all_invalid = True
                for proof in backup_proofs:
                    proof_id = f"{proof['secret']}:{proof['C']}"
                    if proof_id in valid_proof_ids:
                        # At least one valid proof, keep the backup
                        all_invalid = False
                        break

                if all_invalid:
                    # All proofs are spent/invalid, check with mint to be sure
                    valid_proofs = await self._validate_proofs_with_cache(backup_proofs)
                    if not valid_proofs:
                        # Confirmed all proofs are spent/invalid
                        backup_file.unlink()
                        cleaned_count += 1
                        print(
                            f"   🗑️  Deleted backup with only spent proofs: {backup_file.name}"
                        )

            except Exception as e:
                print(f"   ⚠️  Error processing backup {backup_file.name}: {e}")

        return cleaned_count

    async def scan_and_recover_local_proofs(
        self, *, auto_publish: bool = False
    ) -> dict[str, int]:
        """Scan local proof backups and recover any missing from Nostr.

        This method checks the local proof_backups directory for backup files
        and compares them against what's stored on Nostr. Any missing proofs
        can be automatically published to Nostr.

        Args:
            auto_publish: If True, automatically publish missing proofs to Nostr

        Returns:
            Dictionary with recovery statistics:
            - total_backup_files: Number of backup files found
            - total_proofs_in_backups: Total proofs across all backups
            - missing_from_nostr: Number of proofs not found on Nostr
            - recovered: Number of proofs successfully recovered
            - failed: Number of proofs that failed to recover
        """
        stats = {
            "total_backup_files": 0,
            "total_proofs_in_backups": 0,
            "missing_from_nostr": 0,
            "recovered": 0,
            "failed": 0,
        }

        backup_dir = Path.cwd() / "proof_backups"
        if not backup_dir.exists():
            return stats

        print("🔍 Scanning local proof backups...")

        try:
            # Get current state from Nostr WITHOUT checking local backups to avoid recursion
            state = await self.fetch_wallet_state(
                check_proofs=False, check_local_backups=False
            )
            existing_proofs = set()
            for proof in state.proofs:
                proof_id = f"{proof['secret']}:{proof['C']}"
                existing_proofs.add(proof_id)
        except Exception as e:
            print(f"❌ Error fetching wallet state: {e}")
            return stats

        # Scan all backup files
        backup_files = list(backup_dir.glob("proofs_*.json"))
        all_backup_proofs: dict[str, Proof] = {}  # proof_id -> proof
        backup_proofs_by_mint: dict[str, list[Proof]] = {}

        for backup_file in backup_files:
            try:
                with open(backup_file, "r") as f:
                    backup_data = json.load(f)

                proofs = backup_data.get("proofs", [])
                for proof in proofs:
                    proof_id = f"{proof['secret']}:{proof['C']}"
                    if proof_id not in all_backup_proofs:
                        all_backup_proofs[proof_id] = proof

                        # Group by mint
                        mint_url = proof.get("mint", "")
                        if mint_url:
                            if mint_url not in backup_proofs_by_mint:
                                backup_proofs_by_mint[mint_url] = []
                            backup_proofs_by_mint[mint_url].append(proof)

            except Exception as e:
                print(f"⚠️  Error reading backup file {backup_file}: {e}")

        # Find missing proofs
        missing_proof_ids = set(all_backup_proofs.keys()) - existing_proofs
        missing_proofs: list[Proof] = []

        for proof_id in missing_proof_ids:
            missing_proofs.append(all_backup_proofs[proof_id])

        stats = {
            "total_backup_files": len(backup_files),
            "total_proofs_in_backups": len(all_backup_proofs),
            "missing_from_nostr": len(missing_proofs),
            "recovered": 0,
            "failed": 0,
        }

        print(f"📊 Found {len(backup_files)} backup files")
        print(f"   📦 Total proofs in backups: {len(all_backup_proofs)}")
        print(f"   ✅ Already on Nostr: {len(all_backup_proofs) - len(missing_proofs)}")
        print(f"   ❌ Missing from Nostr: {len(missing_proofs)}")

        if not missing_proofs:
            print("✨ All proofs are already backed up on Nostr!")
            return stats

        if not auto_publish:
            print("\n💡 To recover missing proofs, run with auto_publish=True")
            return stats

        # Validate missing proofs before publishing
        print("\n🔐 Validating missing proofs with mints...")
        valid_missing_proofs = await self._validate_proofs_with_cache(missing_proofs)
        invalid_count = len(missing_proofs) - len(valid_missing_proofs)

        if invalid_count > 0:
            print(f"   ⚠️  {invalid_count} proofs are already spent or invalid")

        if not valid_missing_proofs:
            print("❌ No valid proofs to recover")
            # Clean up backup files that only contain spent proofs
            if auto_publish and len(missing_proofs) > 0:
                print("🧹 Cleaning up backup files with only spent proofs...")
                cleaned = await self._cleanup_spent_proof_backups()
                if cleaned > 0:
                    print(f"   ✅ Cleaned up {cleaned} backup files")
            return stats

        # Group valid missing proofs by mint
        missing_by_mint: dict[str, list[Proof]] = {}
        for proof in valid_missing_proofs:
            mint_url = proof.get("mint", "")
            if mint_url:
                if mint_url not in missing_by_mint:
                    missing_by_mint[mint_url] = []
                missing_by_mint[mint_url].append(proof)

        # Publish missing proofs to Nostr
        print(f"\n📤 Publishing {len(valid_missing_proofs)} missing proofs to Nostr...")

        for mint_url, mint_proofs in missing_by_mint.items():
            try:
                event_id = await self.event_manager.publish_token_event(mint_proofs)
                stats["recovered"] += len(mint_proofs)
                print(f"   ✅ Published {len(mint_proofs)} proofs for {mint_url}")
                print(f"      Event ID: {event_id}")

                # Also create spending history for recovery (group by unit)
                # Group recovered proofs by unit
                recovered_by_unit: dict[str, int] = {}
                for proof in mint_proofs:
                    unit_str = str(proof.get("unit", "sat"))
                    recovered_by_unit[unit_str] = (
                        recovered_by_unit.get(unit_str, 0) + proof["amount"]
                    )

                # Create spending history for each unit
                for recovery_unit, recovery_amount in recovered_by_unit.items():
                    await self.event_manager.publish_spending_history(
                        direction="in",
                        amount=recovery_amount,
                        unit=recovery_unit,
                        created_token_ids=[event_id]
                        if recovery_unit == list(recovered_by_unit.keys())[0]
                        else None,
                    )

            except Exception as e:
                print(f"   ❌ Failed to publish proofs for {mint_url}: {e}")
                stats["failed"] += len(mint_proofs)

        # Clean up successfully recovered backup files (with verification)
        if stats["recovered"] > 0 and stats["failed"] == 0:
            print("\n🔍 Verifying recovered proofs before cleaning up backups...")

            # Re-fetch state to ensure all recovered proofs are really on relays
            await asyncio.sleep(2.0)  # Give relays time to propagate

            try:
                verification_state = await self.fetch_wallet_state(
                    check_proofs=False, check_local_backups=False
                )
                stored_proof_ids = set(
                    f"{p['secret']}:{p['C']}" for p in verification_state.proofs
                )

                # Check each backup file individually
                for backup_file in backup_files:
                    try:
                        with open(backup_file, "r") as f:
                            backup_data = json.load(f)

                        backup_proofs = backup_data.get("proofs", [])
                        all_verified = True

                        for proof in backup_proofs:
                            proof_id = f"{proof['secret']}:{proof['C']}"
                            if proof_id not in stored_proof_ids:
                                # Check if it's spent (which is okay)
                                if proof_id not in self._known_spent_proofs:
                                    all_verified = False
                                    break

                        if all_verified:
                            backup_file.unlink()
                            print(f"   ✅ Verified and deleted: {backup_file.name}")
                        else:
                            print(
                                f"   ⚠️  Keeping backup (not all proofs verified): {backup_file.name}"
                            )

                    except Exception as e:
                        print(f"   ⚠️  Error processing {backup_file.name}: {e}")

            except Exception as e:
                print(f"   ❌ Verification failed, keeping all backups: {e}")

        print(f"\n✨ Recovery complete! Recovered {stats['recovered']} proofs")
        return stats


async def sats_value_of_proofs(proofs: list[Proof]) -> int:
    """Get the total value of proofs in sats."""
    total_sats = 0
    for proof in proofs:
        if proof["unit"] == "sat":
            total_sats += proof["amount"]
        elif proof["unit"] == "msat":
            total_sats += proof["amount"] // 1000
        else:
            exchange_rate = await Mint(proof["mint"]).mint_exchange_rate(proof["unit"])
            total_sats += int(proof["amount"] * exchange_rate)
    return total_sats
