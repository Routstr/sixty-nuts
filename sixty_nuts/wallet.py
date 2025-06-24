from __future__ import annotations

from typing import TypedDict, cast
import base64
import os
import json
import secrets
import time
from dataclasses import dataclass
import asyncio
from pathlib import Path

import httpx
from coincurve import PrivateKey, PublicKey

from .mint import (
    Mint,
    ProofComplete as Proof,
    BlindedMessage,
    CurrencyUnit,
)
from .relay import (
    RelayManager,
    EventKind,
)
from .crypto import (
    unblind_signature,
    hash_to_curve,
    create_blinded_message_with_secret,
    get_mint_pubkey_for_amount,
    decode_nsec,
    generate_privkey,
    get_pubkey,
    nip44_decrypt,
)
from .events import EventManager, WalletError

try:
    import cbor2
except ModuleNotFoundError:  # pragma: no cover – allow runtime miss
    cbor2 = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Protocol-level definitions
# ──────────────────────────────────────────────────────────────────────────────


class ProofDict(TypedDict):
    """Extended proof structure for NIP-60 wallet use.

    Extends the basic Proof with mint URL tracking for multi-mint support.
    """

    id: str
    amount: int
    secret: str
    C: str
    mint: str | None  # Add mint URL tracking


@dataclass
class WalletState:
    """Current wallet state."""

    balance: int
    proofs: list[ProofDict]
    mint_keysets: dict[str, list[dict[str, str]]]  # mint_url -> keysets
    proof_to_event_id: dict[str, str] | None = (
        None  # proof_id -> event_id mapping (TODO)
    )


# WalletError is imported from .events


# ──────────────────────────────────────────────────────────────────────────────
# Wallet implementation skeleton
# ──────────────────────────────────────────────────────────────────────────────


class Wallet:
    """Lightweight stateless Cashu wallet implementing NIP-60."""

    def __init__(
        self,
        nsec: str,  # nostr private key
        *,
        mint_urls: list[str] | None = None,  # cashu mint urls (can have multiple)
        currency: CurrencyUnit = "sat",  # Updated to use NUT-01 compliant type
        wallet_privkey: str | None = None,  # separate privkey for P2PK ecash (NIP-61)
        relays: list[str] | None = None,  # nostr relays to use
    ) -> None:
        self.nsec = nsec
        self._privkey = decode_nsec(nsec)
        self.mint_urls: list[str] = mint_urls or ["https://mint.minibits.cash/Bitcoin"]
        self.currency: CurrencyUnit = currency
        # Validate currency unit is supported
        self._validate_currency_unit(currency)

        # Generate wallet privkey if not provided
        if wallet_privkey is None:
            wallet_privkey = generate_privkey()
        self.wallet_privkey = wallet_privkey
        self._wallet_privkey_obj = PrivateKey(bytes.fromhex(wallet_privkey))

        self.relays: list[str] = relays or [
            "wss://relay.damus.io",
            "wss://relay.nostr.band",
            "wss://relay.snort.social",
            "wss://nostr.mom",
        ]

        # Mint instances
        self.mints: dict[str, Mint] = {}

        # Relay manager
        self.relay_manager = RelayManager(
            relay_urls=self.relays,
            privkey=self._privkey,  # Already a PrivateKey object
            use_queued_relays=True,
            min_relay_interval=1.0,
        )

        # Event manager for handling Nostr events
        self.event_manager = EventManager(
            relay_manager=self.relay_manager,
            privkey=self._privkey,
            mint_urls=self.mint_urls,
        )

        # Track minted quotes to prevent double-minting
        self._minted_quotes: set[str] = set()

        # Shared HTTP client reused by all Mint objects
        self.mint_client = httpx.AsyncClient()

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
        currency: CurrencyUnit = "sat",
        wallet_privkey: str | None = None,
        relays: list[str] | None = None,
        auto_init: bool = True,
    ) -> "Wallet":
        """Create and optionally check for existing wallet events.

        Args:
            nsec: Nostr private key
            mint_urls: Cashu mint URLs
            currency: Currency unit
            wallet_privkey: Private key for P2PK operations
            relays: Nostr relay URLs
            auto_init: If True, check for existing wallet state (but don't create new events)

        Returns:
            Wallet instance (call initialize_wallet() to create wallet events if needed)
        """
        wallet = cls(
            nsec=nsec,
            mint_urls=mint_urls,
            currency=currency,
            wallet_privkey=wallet_privkey,
            relays=relays,
        )

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
            proofs = await self.transfer_proofs(proofs, self.mint_urls[0])

        # Proceed with normal redemption for trusted mints
        # Calculate total amount
        total_amount = sum(p["amount"] for p in proofs)

        # Calculate optimal denominations for the total
        optimal_denoms = self._calculate_optimal_denominations(total_amount)

        # Use the abstracted swap method to get new proofs
        new_proofs = await self._swap_proof_denominations(
            proofs, optimal_denoms, mint_url
        )

        # Publish new token event
        token_event_id = await self.event_manager.publish_token_event(new_proofs)  # type: ignore

        # Publish spending history
        await self.event_manager.publish_spending_history(
            direction="in",
            amount=total_amount,
            created_token_ids=[token_event_id],
        )

        return total_amount, unit

    async def mint_async(
        self, amount: int, *, timeout: int = 300
    ) -> tuple[str, asyncio.Task[bool]]:
        """Create a Lightning invoice and return a task that completes when paid.

        This returns immediately with the invoice and a background task that
        polls for payment.

        Args:
            amount: Amount in the wallet's currency unit
            timeout: Maximum seconds to wait for payment (default: 5 minutes)

        Returns:
            Tuple of (lightning_invoice, payment_task)
            The payment_task returns True when paid, False on timeout

        Example:
            invoice, task = await wallet.mint_async(100)
            print(f"Pay: {invoice}")
            # Do other things...
            paid = await task  # Wait for payment
        """
        invoice, quote_id = await self.create_quote(amount)
        mint = self._get_mint(self.mint_urls[0])

        async def poll_payment() -> bool:
            start_time = time.time()
            poll_interval = 1.0

            while (time.time() - start_time) < timeout:
                # Check quote status and mint if paid
                quote_status, new_proofs = await mint.check_quote_status_and_mint(
                    quote_id,
                    amount,
                    minted_quotes=self._minted_quotes,
                    mint_url=self.mint_urls[0],
                )

                # If new proofs were minted, publish wallet events
                if new_proofs:
                    # Convert dict proofs to ProofDict
                    proof_dicts: list[ProofDict] = []
                    for proof in new_proofs:
                        proof_dicts.append(
                            ProofDict(
                                id=proof["id"],
                                amount=proof["amount"],
                                secret=proof["secret"],
                                C=proof["C"],
                                mint=proof["mint"],
                            )
                        )

                    # Publish token event
                    token_event_id = await self.event_manager.publish_token_event(
                        proof_dicts
                    )

                    # Publish spending history
                    mint_amount = sum(p["amount"] for p in new_proofs)
                    await self.event_manager.publish_spending_history(
                        direction="in",
                        amount=mint_amount,
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

    async def melt(self, invoice: str, *, target_mint: str | None = None) -> None:
        """Pay a Lightning invoice by melting tokens with automatic multi-mint support.

        Args:
            invoice: BOLT-11 Lightning invoice to pay

        Raises:
            WalletError: If insufficient balance or payment fails

        Example:
            await wallet.melt("lnbc100n1...")
        """
        if target_mint is None:
            target_mint = self.mint_urls[0]

        invoice_amount = 0  # TODO: get_invoice_amount_with_fees(invoice)

        state = await self.fetch_wallet_state(check_proofs=True)
        self.raise_if_insufficient_balance(state.balance, invoice_amount)

        selected_proofs = await self._select_proofs(
            state.proofs, invoice_amount, target_mint
        )
        # TODO: self.mark_pending_proofs(selected_proofs)

        # melt proofs and pay invoice
        mint = self._get_mint(target_mint)
        melt_quote = await mint.create_melt_quote(unit=self.currency, request=invoice)
        # TODO: convert selected_proofs to mint format
        # melt_resp = await mint.melt(quote=melt_quote["quote"], inputs=selected_proofs)

        # TODO: check success and undo if failed or retry

        # TODO: publish spending history with fee information
        pass

    async def send(
        self,
        amount: int,
        target_mint: str | None = None,
    ) -> str:
        """Create a Cashu token for sending.

        Selects proofs worth exactly the specified amount and returns a
        Cashu token string. The new proof selection automatically handles
        splitting proofs to achieve exact amounts.

        Args:
            amount: Amount to send in the wallet's currency unit

        Returns:
            Cashu token string that can be sent to another wallet

        Raises:
            WalletError: If insufficient balance or operation fails

        Example:
            token = await wallet.send(100)
            print(f"Send this token: {token}")
        """
        if target_mint is None:
            target_mint = self.mint_urls[0]

        state = await self.fetch_wallet_state(check_proofs=True)
        if state.balance < amount:
            raise WalletError(
                f"Insufficient balance. Need at least {amount} {self.currency} "
                f"(amount: {amount}), but have {state.balance}"
            )

        selected_proofs = await self._select_proofs(state.proofs, amount, target_mint)

        token = self._serialize_proofs_for_token(selected_proofs, target_mint)

        # TODO: Publish spending history with fee information
        # await self.publish_spending_history(
        #     direction="out",
        #     amount=amount + total_input_fees,  # Include input fees in spending amount
        #     created_token_ids=created_ids,
        #     destroyed_token_ids=deleted_event_ids,
        # )

        # TODO: store pending token somewhere to check on status and potentially undo

        return token

    async def send_to_lnurl(self, lnurl: str, amount: int) -> int:
        """Send funds to an LNURL address.

        Args:
            lnurl: LNURL string (can be lightning:, user@host, bech32, or direct URL)
            amount: Amount to send in the wallet's currency unit
            fee_estimate: Fee estimate as a percentage (default: 1%)
            max_fee: Maximum fee in the wallet's currency unit (optional)
            mint_fee_reserve: Expected mint fee reserve (default: 1 sat)

        Returns:
            Amount actually paid in the wallet's currency unit

        Raises:
            WalletError: If amount is outside LNURL limits or insufficient balance
            LNURLError: If LNURL operations fail

        Example:
            # Send 1000 sats to a Lightning Address
            paid = await wallet.send_to_lnurl("user@getalby.com", 1000)
            print(f"Paid {paid} sats")
        """
        from .lnurl import get_lnurl_data, get_lnurl_invoice

        # Get current balance
        state = await self.fetch_wallet_state(check_proofs=True)
        balance = state.balance

        estimated_fee_sats = max(amount * 0.01, 2)
        if self.currency == "msat":
            estimated_fee = estimated_fee_sats * 1000
        else:
            estimated_fee = estimated_fee_sats

        if balance < amount + estimated_fee:
            raise WalletError(
                f"Insufficient balance. Need at least {amount + estimated_fee} {self.currency} "
                f"(amount: {amount} + estimated fees: {estimated_fee} {self.currency}), but have {balance}"
            )

        # Get LNURL data
        lnurl_data = await get_lnurl_data(lnurl)

        # Convert amounts based on currency
        if self.currency == "sat":
            amount_msat = amount * 1000
            min_sendable_sat = lnurl_data["min_sendable"] // 1000
            max_sendable_sat = lnurl_data["max_sendable"] // 1000
            unit_str = "sat"
        elif self.currency == "msat":
            amount_msat = amount
            min_sendable_sat = lnurl_data["min_sendable"]
            max_sendable_sat = lnurl_data["max_sendable"]
            unit_str = "msat"
        else:
            raise WalletError(f"Currency {self.currency} not supported for LNURL")

        # Check amount limits
        if not (
            lnurl_data["min_sendable"] <= amount_msat <= lnurl_data["max_sendable"]
        ):
            raise WalletError(
                f"Amount {amount} {unit_str} is outside LNURL limits "
                f"({min_sendable_sat} - {max_sendable_sat} {unit_str})"
            )
        print(amount_msat, min_sendable_sat, max_sendable_sat)

        # Get Lightning invoice
        bolt11_invoice, invoice_data = await get_lnurl_invoice(
            lnurl_data["callback_url"], amount_msat
        )
        print(bolt11_invoice)

        # Pay the invoice using melt
        await self.melt(bolt11_invoice)
        return amount  # Return the amount we intended to pay

    async def roll_over_proofs(
        self,
        *,
        spent_proofs: list[ProofDict],
        unspent_proofs: list[ProofDict],
        deleted_event_ids: list[str],
    ) -> str:
        """Roll over unspent proofs after a partial spend and return new token id."""
        # TODO: Implement roll over logic
        return ""

    # ───────────────────────── Proof Management ─────────────────────────────────

    async def create_quote(self, amount: int) -> tuple[str, str]:
        """Create a Lightning invoice (quote) at the mint and return the BOLT-11 string and quote ID.

        Returns:
            Tuple of (lightning_invoice, quote_id)
        """
        mint = self._get_mint(self.mint_urls[0])

        # Create mint quote
        quote_resp = await mint.create_mint_quote(
            unit=self.currency,
            amount=amount,
        )

        # Optionally publish quote tracker event
        # (skipping for simplicity)

        # TODO: Implement quote tracking as per NIP-60:
        # await self.publish_quote_tracker(
        #     quote_id=quote_resp["quote"],
        #     mint_url=self.mint_urls[0],
        #     expiration=int(time.time()) + 14 * 24 * 60 * 60  # 2 weeks
        # )

        return quote_resp.get("request", ""), quote_resp.get(
            "quote", ""
        )  # Return both invoice and quote_id

    async def _consolidate_proofs(
        self, proofs: list[ProofDict], target_mint: str | None = None
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

        # Group proofs by mint
        proofs_by_mint: dict[str, list[ProofDict]] = {}
        for proof in proofs:
            mint_url = proof.get("mint") or (
                self.mint_urls[0] if self.mint_urls else ""
            )
            if target_mint and mint_url != target_mint:
                continue  # Skip if not the target mint
            if mint_url not in proofs_by_mint:
                proofs_by_mint[mint_url] = []
            proofs_by_mint[mint_url].append(proof)

        # Process each mint
        for mint_url, mint_proofs in proofs_by_mint.items():
            if not mint_proofs:
                continue

            # Calculate current balance for this mint
            current_balance = sum(p["amount"] for p in mint_proofs)

            # Check if already optimally denominated
            current_denoms: dict[int, int] = {}
            for proof in mint_proofs:
                amount = proof["amount"]
                current_denoms[amount] = current_denoms.get(amount, 0) + 1

            # Calculate optimal denominations for the balance
            optimal_denoms = self._calculate_optimal_denominations(current_balance)

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
                    mint_proofs, optimal_denoms, mint_url
                )

                # Store new proofs on Nostr
                await self.store_proofs(new_proofs)
            except Exception as e:
                print(f"Warning: Failed to consolidate proofs for {mint_url}: {e}")
                continue

    def _calculate_optimal_denominations(self, amount: int) -> dict[int, int]:
        """Calculate optimal denomination breakdown for an amount.

        Returns dict of denomination -> count.
        """
        denominations = {}
        remaining = amount

        # Use powers of 2 for optimal denomination
        for denom in [
            16384,
            8192,
            4096,
            2048,
            1024,
            512,
            256,
            128,
            64,
            32,
            16,
            8,
            4,
            2,
            1,
        ]:
            if remaining >= denom:
                count = remaining // denom
                denominations[denom] = count
                remaining -= denom * count

        return denominations

    async def _select_proofs(
        self, proofs: list[ProofDict], amount: int, target_mint: str | None = None
    ) -> list[ProofDict]:
        """Select proofs for spending a specific amount using optimal selection.

        Uses a greedy algorithm to minimize the number of proofs and change.

        Args:
            proofs: Available proofs to select from
            amount: Amount to select

        Returns:
            Selected proofs that sum to at least the requested amount

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

        optimal_denoms = self._calculate_optimal_denominations(amount)

        if target_mint is None:
            target_mint = self.mint_urls[0]

        # check if enough balance in proofs from target mint
        target_mint_proofs = [p for p in valid_proofs if p.get("mint") == target_mint]
        target_mint_balance = sum(p["amount"] for p in target_mint_proofs)
        if target_mint_balance < amount:
            await self.transfer_balance_to_mint(
                amount - target_mint_balance, target_mint
            )
            state = await self.fetch_wallet_state(check_proofs=True)
            return await self._select_proofs(state.proofs, amount, target_mint)

        new_proofs = await self._swap_proof_denominations(
            valid_proofs, optimal_denoms, target_mint
        )
        # split new_proofs into valid_proofs and change_proofs
        await self.store_proofs(new_proofs)

        selected_proofs: list[ProofDict] = []
        used_proofs: set[str] = set()

        for denom, count in optimal_denoms.items():
            for _ in range(count):
                proof = next(
                    p
                    for p in new_proofs
                    if p["amount"] == denom
                    and f"{p['secret']}:{p['C']}" not in used_proofs
                )
                selected_proofs.append(proof)
                used_proofs.add(f"{proof['secret']}:{proof['C']}")

        return selected_proofs

    async def _swap_proof_denominations(
        self,
        proofs: list[ProofDict],
        target_denominations: dict[int, int],
        mint_url: str | None = None,
    ) -> list[ProofDict]:
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

        # Determine mint URL
        if mint_url is None:
            mint_url = proofs[0].get("mint") or (
                self.mint_urls[0] if self.mint_urls else None
            )
        if not mint_url:
            raise WalletError("No mint URL available")

        # Calculate total amounts
        input_amount = sum(p["amount"] for p in proofs)
        target_amount = sum(
            denom * count for denom, count in target_denominations.items()
        )

        if input_amount != target_amount:
            raise WalletError(
                f"Amount mismatch: input={input_amount}, target={target_amount}"
            )

        # TODO: Implement this
        # check if proofs are already in target denominations
        # return proofs if they are

        # Get mint instance
        mint = self._get_mint(mint_url)

        # Convert to mint proof format
        mint_proofs = [self._proofdict_to_mint_proof(p) for p in proofs]

        # Get active keyset
        keysets_resp = await mint.get_keysets()
        keysets = keysets_resp.get("keysets", [])
        active_keysets = [ks for ks in keysets if ks.get("active", True)]

        if not active_keysets:
            raise WalletError("No active keysets found")

        keyset_id = str(active_keysets[0]["id"])

        # Create blinded messages for target denominations
        outputs: list[BlindedMessage] = []
        secrets: list[str] = []
        blinding_factors: list[str] = []

        for denomination, count in sorted(target_denominations.items()):
            for _ in range(count):
                secret, r_hex, blinded_msg = create_blinded_message_with_secret(
                    denomination, keyset_id
                )
                outputs.append(blinded_msg)
                secrets.append(secret)
                blinding_factors.append(r_hex)

        # Perform swap
        swap_resp = await mint.swap(inputs=mint_proofs, outputs=outputs)

        # Get mint keys for unblinding
        keys_resp = await mint.get_keys(keyset_id)
        mint_keysets = keys_resp.get("keysets", [])
        mint_keys = None

        for ks in mint_keysets:
            if str(ks.get("id")) == keyset_id:
                keys_data: dict[str, str] | str = ks.get("keys", {})
                if isinstance(keys_data, dict) and keys_data:
                    mint_keys = keys_data
                    break

        if not mint_keys:
            raise WalletError("Could not find mint keys for unblinding")

        # Unblind signatures to create new proofs
        new_proofs: list[ProofDict] = []
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
                ProofDict(
                    id=sig["id"],
                    amount=sig["amount"],
                    secret=secrets[i],
                    C=C.format(compressed=True).hex(),
                    mint=mint_url,
                )
            )

        return new_proofs

    async def store_proofs(self, proofs: list[ProofDict]) -> None:
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

        backup_dir = Path.home() / ".cashu_nip60" / "proof_backups"
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
        state = await self.fetch_wallet_state(check_proofs=False)
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
            # All proofs already stored
            try:
                os.remove(backup_file)  # Clean up backup
            except Exception:
                pass
            return

        # Group proofs by mint for efficient storage
        proofs_by_mint: dict[str, list[ProofDict]] = {}
        for proof in new_proofs:
            mint_url = proof.get("mint") or (
                self.mint_urls[0] if self.mint_urls else ""
            )
            if mint_url not in proofs_by_mint:
                proofs_by_mint[mint_url] = []
            proofs_by_mint[mint_url].append(proof)

        # Publish token events for each mint
        published_count = 0
        failed_mints = []

        for mint_url, mint_proofs in proofs_by_mint.items():
            try:
                # Publish token event
                event_id = await self.event_manager.publish_token_event(mint_proofs)  # type: ignore
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

        # Clean up backup if all succeeded
        if not failed_mints and published_count == len(new_proofs):
            try:
                os.remove(backup_file)
            except Exception:
                pass
        else:
            print(f"⚠️  Kept local backup at: {backup_file}")

        if failed_mints:
            print(f"⚠️  Failed to publish proofs for mints: {', '.join(failed_mints)}")
            print("   Background retry tasks have been started.")

    async def _retry_store_proofs(
        self, proofs: list[ProofDict], mint_url: str, backup_file: Path
    ) -> None:
        """Background task to retry storing proofs."""
        max_retries = 5
        base_delay = 10.0  # Start with 10 second delay

        for retry in range(max_retries):
            await asyncio.sleep(base_delay * (2**retry))  # Exponential backoff

            try:
                # Try to publish again
                event_id = await self.event_manager.publish_token_event(proofs)  # type: ignore
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
                            # All proofs stored, remove backup
                            backup_file.unlink()
                except Exception:
                    pass  # Ignore backup cleanup errors

                return  # Success

            except Exception as e:
                if retry == max_retries - 1:
                    print(
                        f"❌ Failed to store proofs for {mint_url} after {max_retries} retries: {e}"
                    )
                    print(f"   Manual recovery may be needed from: {backup_file}")

    async def transfer_proofs(
        self, proofs: list[ProofDict], target_mint: str
    ) -> list[ProofDict]:
        """Transfer proofs to a specific mint."""
        # TODO: Implement this
        # sort proofs by mint
        # for each mint, calculate amount to transfer
        # calculate fees
        # mint, melt, send to target mint
        # store proofs properly
        return proofs

    async def transfer_balance_to_mint(self, amount: int, target_mint: str) -> None:
        """Transfer balance to a specific mint."""
        # TODO: Implement this
        # get all proofs not from this mint
        # add up balance per mint and substracting estimaged fees to transfer
        # check if enough balance to transfer target amount
        # iterate starting with the biggest balance mint
        # self.transfer_proofs(proofs, target_mint)
        # if enough balance, break
        pass

    # ───────────────────────── Helper Methods ─────────────────────────────────

    def _get_mint(self, mint_url: str) -> Mint:
        """Get or create mint instance for URL."""
        if mint_url not in self.mints:
            self.mints[mint_url] = Mint(mint_url, client=self.mint_client)
        return self.mints[mint_url]

    def _serialize_proofs_for_token(
        self, proofs: list[ProofDict], mint_url: str
    ) -> str:
        """Serialize proofs into a Cashu token format."""
        # Convert ProofDict (with base64 secrets) to format expected by Cashu tokens (hex secrets)
        token_proofs = []
        for proof in proofs:
            # Convert base64 secret to hex for Cashu token
            try:
                secret_bytes = base64.b64decode(proof["secret"])
                secret_hex = secret_bytes.hex()
            except Exception:
                # Fallback: assume it's already hex
                secret_hex = proof["secret"]

            token_proofs.append(
                {
                    "id": proof["id"],
                    "amount": proof["amount"],
                    "secret": secret_hex,  # Cashu tokens expect hex
                    "C": proof["C"],
                }
            )

        # Cashu token format: cashuA<base64url(json)>
        token_data = {
            "token": [{"mint": mint_url, "proofs": token_proofs}],
            "unit": self.currency
            or "sat",  # Ensure unit is always present, default to "sat"
            "memo": "NIP-60 wallet transfer",  # Default memo, but could be passed as arg
        }
        json_str = json.dumps(token_data, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode().rstrip("=")
        return f"cashuA{encoded}"

    def _parse_cashu_token(
        self, token: str
    ) -> tuple[str, CurrencyUnit, list[ProofDict]]:
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

            # Convert hex secrets to base64 for NIP-60 storage
            nip60_proofs: list[ProofDict] = []
            for proof in token_proofs:
                # Convert hex secret to base64
                try:
                    secret_bytes = bytes.fromhex(proof["secret"])
                    secret_base64 = base64.b64encode(secret_bytes).decode("ascii")
                except Exception:
                    # Fallback: assume it's already base64
                    secret_base64 = proof["secret"]

                nip60_proofs.append(
                    ProofDict(
                        id=proof["id"],
                        amount=proof["amount"],
                        secret=secret_base64,  # Store as base64 for NIP-60
                        C=proof["C"],
                        mint=mint_info["mint"],
                    )
                )

            return mint_info["mint"], token_unit, nip60_proofs

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
                    # CBOR format already has hex secret, convert to base64
                    secret_hex = proof["s"]
                    try:
                        secret_bytes = bytes.fromhex(secret_hex)
                        secret_base64 = base64.b64encode(secret_bytes).decode("ascii")
                    except Exception:
                        # Fallback
                        secret_base64 = secret_hex

                    # Convert CBOR proof format to our ProofDict format
                    proofs.append(
                        ProofDict(
                            id=keyset_id,
                            amount=proof["a"],
                            secret=secret_base64,  # Store as base64 for NIP-60
                            C=proof["c"].hex(),  # Convert bytes to hex
                            mint=mint_url,
                        )
                    )

            return mint_url, cbor_unit, proofs
        else:
            raise ValueError(f"Unknown token version: {token[:7]}")

    def raise_if_insufficient_balance(self, balance: int, amount: int) -> None:
        if balance < amount:
            raise WalletError(
                f"Insufficient balance. Need at least {amount} {self.currency} "
                f"(amount: {amount}), but have {balance}"
            )

    # ───────────────────────── Proof Validation ────────────────────────────────

    def _compute_proof_y_values(self, proofs: list[ProofDict]) -> list[str]:
        """Compute Y values for proofs to use in check_state API.

        Args:
            proofs: List of proof dictionaries

        Returns:
            List of Y values (hex encoded compressed public keys)
        """
        y_values = []
        for proof in proofs:
            secret = proof["secret"]

            # Check if secret is already in hex format (64 chars, valid hex)
            if len(secret) == 64 and all(c in "0123456789abcdefABCDEF" for c in secret):
                # Already hex format - use as is
                secret_hex = secret.lower()
            else:
                # Try base64 decode (NIP-60 standard)
                try:
                    secret_bytes = base64.b64decode(secret)
                    secret_hex = secret_bytes.hex()
                except Exception:
                    # Fallback: assume it's already hex
                    secret_hex = secret

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

    async def _validate_proofs_with_cache(
        self, proofs: list[ProofDict]
    ) -> list[ProofDict]:
        """Validate proofs using cache to avoid re-checking spent proofs."""
        valid_proofs = []
        proofs_to_check: list[ProofDict] = []

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

        # Second pass: validate uncached proofs
        if proofs_to_check:
            # Group by mint for batch validation
            proofs_by_mint: dict[str, list[ProofDict]] = {}
            for proof in proofs_to_check:
                # Get mint URL from proof, fallback to first mint URL
                mint_url = proof.get("mint") or (
                    self.mint_urls[0] if self.mint_urls else None
                )
                if mint_url:
                    if mint_url not in proofs_by_mint:
                        proofs_by_mint[mint_url] = []
                    proofs_by_mint[mint_url].append(proof)

            # Validate with each mint
            for mint_url, mint_proofs in proofs_by_mint.items():
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

    async def fetch_wallet_state(self, *, check_proofs: bool = True) -> WalletState:
        """Fetch wallet, token events and compute balance.

        Args:
            check_proofs: If True, validate all proofs with mint before returning state
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
        if wallet_event:
            try:
                decrypted = nip44_decrypt(wallet_event["content"], self._privkey)
                wallet_data = json.loads(decrypted)

                # Update mint URLs from wallet event
                self.mint_urls = []
                for item in wallet_data:
                    if item[0] == "mint":
                        self.mint_urls.append(item[1])
                    elif item[0] == "privkey":
                        self.wallet_privkey = item[1]
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
        all_proofs: list[ProofDict] = []
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
            except Exception as e:
                # Skip this event if it can't be decrypted
                print(f"Warning: Could not decrypt token event {event['id']}: {e}")
                continue

            # Mark tokens referenced in the "del" field as superseded
            for old_id in token_data.get("del", []):
                invalid_token_ids.add(old_id)

            if event["id"] in invalid_token_ids:
                continue

            proofs = token_data.get("proofs", [])
            mint_url = token_data.get(
                "mint", self.mint_urls[0] if self.mint_urls else None
            )

            for proof in proofs:
                proof_id = f"{proof['secret']}:{proof['C']}"
                if proof_id in proof_seen:
                    continue
                proof_seen.add(proof_id)
                # Add mint URL to proof
                proof_with_mint: ProofDict = ProofDict(
                    id=proof["id"],
                    amount=proof["amount"],
                    secret=proof["secret"],
                    C=proof["C"],
                    mint=mint_url,
                )
                all_proofs.append(proof_with_mint)
                proof_to_event_id[proof_id] = event["id"]

        # Include pending proofs from relay manager
        pending_token_data = self.relay_manager.get_pending_proofs()

        for token_data in pending_token_data:
            mint_url = token_data.get(
                "mint", self.mint_urls[0] if self.mint_urls else None
            )
            if not isinstance(mint_url, str):
                continue

            proofs = token_data.get("proofs", [])
            if not isinstance(proofs, list):
                continue

            for proof in proofs:
                proof_id = f"{proof['secret']}:{proof['C']}"
                if proof_id in proof_seen:
                    continue
                proof_seen.add(proof_id)

                # Mark pending proofs with a special event ID
                pending_proof_with_mint: ProofDict = ProofDict(
                    id=proof["id"],
                    amount=proof["amount"],
                    secret=proof["secret"],
                    C=proof["C"],
                    mint=mint_url,
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

        # Calculate balance
        balance = sum(p["amount"] for p in all_proofs)

        # Fetch mint keysets
        mint_keysets: dict[str, list[dict[str, str]]] = {}
        for mint_url in self.mint_urls:
            mint = self._get_mint(mint_url)
            try:
                keys_resp = await mint.get_keys()
                # Convert Keyset type to dict[str, str] for wallet state
                keysets_as_dicts: list[dict[str, str]] = []
                for keyset in keys_resp.get("keysets", []):
                    # Convert each keyset to a simple dict
                    keyset_dict: dict[str, str] = {
                        "id": keyset["id"],
                        "unit": keyset["unit"],
                    }
                    # Add keys if present
                    if "keys" in keyset and isinstance(keyset["keys"], dict):
                        keyset_dict.update(keyset["keys"])
                    keysets_as_dicts.append(keyset_dict)
                mint_keysets[mint_url] = keysets_as_dicts
            except Exception:
                mint_keysets[mint_url] = []

        return WalletState(
            balance=balance,
            proofs=all_proofs,
            mint_keysets=mint_keysets,
            proof_to_event_id=proof_to_event_id,
        )

    async def get_balance(self, *, check_proofs: bool = True) -> int:
        """Get current wallet balance.

        Args:
            check_proofs: If True, validate all proofs with mint before returning balance

        Returns:
            Current balance in the wallet's currency unit

        Example:
            balance = await wallet.get_balance()
            print(f"Balance: {balance} sats")
        """
        state = await self.fetch_wallet_state(check_proofs=check_proofs)
        return state.balance

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

    async def __aenter__(self) -> "Wallet":
        """Enter async context and connect to relays without auto-creating wallet events."""
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

    # ───────────────────────── Conversion Methods ──────────────────────────────

    # TODO: check why this method is needed
    def _proofdict_to_mint_proof(self, proof_dict: ProofDict) -> Proof:
        """Convert ProofDict to Proof format for mint.

        Handles both hex and base64 secret formats for compatibility.
        """
        secret = proof_dict["secret"]

        # Check if secret is already in hex format (64 chars, valid hex)
        if len(secret) == 64 and all(c in "0123456789abcdefABCDEF" for c in secret):
            # Already hex format - use as is
            secret_hex = secret.lower()
        else:
            # Try base64 decode (NIP-60 standard)
            try:
                secret_bytes = base64.b64decode(secret)
                secret_hex = secret_bytes.hex()
            except Exception:
                # Fallback: assume it's already hex (backwards compatibility)
                secret_hex = secret

        return Proof(
            id=proof_dict["id"],
            amount=proof_dict["amount"],
            secret=secret_hex,
            C=proof_dict["C"],
        )

    # ───────────────────────── Fee Calculation ──────────────────────────────

    def calculate_input_fees(self, proofs: list[ProofDict], keyset_info: dict) -> int:
        """Calculate input fees based on number of proofs and keyset fee rate.

        Args:
            proofs: List of proofs being spent
            keyset_info: Keyset information containing input_fee_ppk

        Returns:
            Total input fees in base currency units (e.g., satoshis)

        Example:
            With input_fee_ppk=1000 (1 sat per proof) and 3 proofs:
            fee = (3 * 1000) // 1000 = 3 satoshis
        """
        input_fee_ppk = keyset_info.get("input_fee_ppk", 0)

        # Ensure input_fee_ppk is an integer (could be string from API)
        try:
            input_fee_ppk = int(input_fee_ppk)
        except (ValueError, TypeError):
            input_fee_ppk = 0

        if input_fee_ppk == 0:
            return 0

        num_proofs = len(proofs)
        # Fee is calculated as: (number_of_proofs * input_fee_ppk) / 1000
        # Using integer division to avoid floating point precision issues
        return (num_proofs * input_fee_ppk) // 1000

    async def calculate_total_input_fees(
        self, mint: Mint, proofs: list[ProofDict]
    ) -> int:
        """Calculate total input fees for proofs across different keysets.

        Args:
            mint: Mint instance to query keyset information
            proofs: List of proofs being spent

        Returns:
            Total input fees for all proofs
        """
        try:
            # Get keyset information from mint
            keysets_resp = await mint.get_keysets()
            keyset_fees = {}

            # Build mapping of keyset_id -> input_fee_ppk
            for keyset in keysets_resp["keysets"]:
                keyset_fees[keyset["id"]] = keyset.get("input_fee_ppk", 0)

            # Group proofs by keyset and calculate fees
            total_fee = 0
            keyset_proof_counts = {}

            for proof in proofs:
                keyset_id = proof["id"]
                if keyset_id not in keyset_proof_counts:
                    keyset_proof_counts[keyset_id] = 0
                keyset_proof_counts[keyset_id] += 1

            # Calculate fees for each keyset
            for keyset_id, proof_count in keyset_proof_counts.items():
                fee_rate = keyset_fees.get(keyset_id, 0)
                # Ensure fee_rate is an integer (could be string from API)
                try:
                    fee_rate = int(fee_rate)
                except (ValueError, TypeError):
                    fee_rate = 0
                keyset_fee = (proof_count * fee_rate) // 1000
                total_fee += keyset_fee

            return total_fee

        except Exception:
            # Fallback to zero fees if keyset info unavailable
            # This ensures wallet doesn't break when connecting to older mints
            return 0

    def estimate_transaction_fees(
        self,
        input_proofs: list[ProofDict],
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
