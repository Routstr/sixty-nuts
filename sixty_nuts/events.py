"""Event management for NIP-60 wallet operations."""

from __future__ import annotations

import asyncio
import json
from typing import Literal, cast

from coincurve import PrivateKey

from sixty_nuts.wallet import ProofDict

from .relay import (
    NostrEvent,
    RelayManager,
    EventKind,
    create_event,
)
from .crypto import (
    get_pubkey,
    nip44_encrypt,
    nip44_decrypt,
)


class WalletError(Exception):
    """Base class for wallet errors."""


class EventManager:
    """Handles all Nostr event operations for the wallet."""

    def __init__(
        self,
        relay_manager: RelayManager,
        privkey: PrivateKey,
        mint_urls: list[str],
    ) -> None:
        self.relay_manager = relay_manager
        self._privkey = privkey
        self.mint_urls = mint_urls

    # ───────────────────────── Wallet Event Management ─────────────────────────

    async def check_wallet_event_exists(self) -> tuple[bool, NostrEvent | None]:
        """Check if a wallet event already exists for this wallet.

        Returns:
            Tuple of (exists, wallet_event_dict)
        """
        try:
            relays = await self.relay_manager.get_relay_connections()
            pubkey = get_pubkey(self._privkey)

            # Fetch all wallet-related events
            all_events: list[NostrEvent] = []
            event_ids_seen: set[str] = set()

            for relay in relays:
                try:
                    events = await relay.fetch_wallet_events(pubkey)
                    # Deduplicate events
                    for event in events:
                        if event["id"] not in event_ids_seen:
                            all_events.append(event)
                            event_ids_seen.add(event["id"])
                except Exception:
                    continue

            # Find the newest wallet event
            wallet_events = [e for e in all_events if e["kind"] == EventKind.Wallet]
            if wallet_events:
                # Get the newest wallet event
                newest_wallet_event = max(wallet_events, key=lambda e: e["created_at"])
                return True, newest_wallet_event

            return False, None
        except Exception:
            return False, None

    async def create_wallet_event(
        self, wallet_privkey: str, *, force: bool = False
    ) -> str:
        """Create or update the replaceable wallet event (kind 17375).

        Args:
            wallet_privkey: Wallet private key to include in event
            force: If True, create event even if one already exists

        Returns:
            The published event id

        Raises:
            WalletError: If wallet event already exists and force=False
        """
        # Check if wallet event already exists
        if not force:
            exists, existing_event = await self.check_wallet_event_exists()
            if exists and existing_event:
                raise WalletError(
                    f"Wallet event already exists (created at {existing_event['created_at']}). "
                    f"Use force=True to override or call update_wallet_event() to update it."
                )

        # Create content array with wallet metadata
        content_data = [
            ["privkey", wallet_privkey],
        ]
        for mint_url in self.mint_urls:
            content_data.append(["mint", mint_url])

        # Encrypt content
        content_json = json.dumps(content_data)
        encrypted_content = nip44_encrypt(content_json, self._privkey)

        # NIP-60 requires at least one mint tag in the tags array (unencrypted)
        # This is critical for wallet discovery!
        tags = [["mint", url] for url in self.mint_urls]

        # Create replaceable wallet event
        event = create_event(
            kind=EventKind.Wallet,
            content=encrypted_content,
            tags=tags,
        )

        # Publish unsigned event (signing handled by relay manager)
        return await self.relay_manager.publish_to_relays(event)

    async def update_wallet_event(self, wallet_privkey: str) -> str:
        """Update the wallet event with current configuration.

        This always creates a new wallet event (since they're replaceable events).

        Args:
            wallet_privkey: Wallet private key to include in event

        Returns:
            The published event id
        """
        return await self.create_wallet_event(wallet_privkey, force=True)

    async def initialize_wallet(
        self, wallet_privkey: str, *, force: bool = False
    ) -> bool:
        """Initialize wallet by checking for existing events or creating new ones.

        Args:
            wallet_privkey: Wallet private key to include in event
            force: If True, create wallet event even if one already exists

        Returns:
            True if wallet was initialized (new event created), False if already existed

        Raises:
            WalletError: If wallet event already exists and force=False
        """
        exists, _ = await self.check_wallet_event_exists()

        if exists and not force:
            # Wallet already exists, no need to create new event
            return False

        # Create new wallet event
        await self.create_wallet_event(wallet_privkey, force=force)
        return True

    async def delete_wallet_event(self, event_id: str) -> None:
        """Delete a wallet event via NIP-09 (kind 5).

        Args:
            event_id: ID of the wallet event to delete
        """
        # Create delete event
        event = create_event(
            kind=EventKind.Delete,
            content="",
            tags=[
                ["e", event_id],
                ["k", str(EventKind.Wallet)],
            ],
        )

        # Publish unsigned event (signing handled by relay manager)
        await self.relay_manager.publish_to_relays(event)

    async def delete_all_wallet_events(self) -> int:
        """Delete all wallet events for this wallet.

        Returns:
            Number of wallet events deleted
        """
        # Fetch all wallet events
        all_events = await self.relay_manager.fetch_wallet_events(
            get_pubkey(self._privkey)
        )

        # Find all wallet events and extract IDs
        wallet_events = [e for e in all_events if e["kind"] == EventKind.Wallet]
        event_ids = [event["id"] for event in wallet_events]

        # Use batch deletion
        return await self._batch_delete_events(event_ids, EventKind.Wallet)

    # ──────────────────────────── History Events ──────────────────────────────

    async def fetch_spending_history(self) -> list[dict]:
        """Fetch and decrypt spending history events.

        Returns:
            List of spending history entries with metadata
        """
        # Fetch all wallet-related events
        all_events = await self.relay_manager.fetch_wallet_events(
            get_pubkey(self._privkey)
        )

        # Find history events
        history_events = [e for e in all_events if e["kind"] == EventKind.History]

        # Sort by timestamp (newest first)
        history_events.sort(key=lambda e: e["created_at"], reverse=True)

        # Decrypt and parse history events
        parsed_history = []
        for event in history_events:
            try:
                decrypted = nip44_decrypt(event["content"], self._privkey)
                history_data = json.loads(decrypted)

                # Convert list format to dict
                history_entry = {
                    "event_id": event["id"],
                    "timestamp": event["created_at"],
                }

                for item in history_data:
                    if len(item) >= 2:
                        key = item[0]
                        value = item[1]

                        if key in ["direction", "amount"]:
                            history_entry[key] = value
                        elif key == "e":  # Event references
                            ref_type = item[3] if len(item) > 3 else "unknown"
                            if ref_type not in history_entry:
                                history_entry[ref_type] = []
                            # Get the list and append to it
                            ref_list = history_entry[ref_type]
                            if isinstance(ref_list, list):
                                ref_list.append(item[1])
                            else:
                                history_entry[ref_type] = [item[1]]

                parsed_history.append(history_entry)

            except Exception as e:
                # Skip events that can't be decrypted/parsed
                print(f"Warning: Could not parse history event {event['id']}: {e}")
                continue

        return parsed_history

    async def delete_history_event(self, event_id: str) -> None:
        """Delete a history event via NIP-09 (kind 5).

        Args:
            event_id: ID of the history event to delete
        """
        # Create delete event
        event = create_event(
            kind=EventKind.Delete,
            content="",
            tags=[
                ["e", event_id],
                ["k", str(EventKind.History)],
            ],
        )

        await self.relay_manager.publish_to_relays(event)

    async def publish_spending_history(
        self,
        *,
        direction: Literal["in", "out"],
        amount: int,
        created_token_ids: list[str] | None = None,
        destroyed_token_ids: list[str] | None = None,
        redeemed_event_id: str | None = None,
    ) -> str:
        """Publish kind 7376 spending history event and return its id."""
        # Build encrypted content
        content_data = [
            ["direction", direction],
            ["amount", str(amount)],
        ]

        # Add e-tags for created tokens (encrypted)
        if created_token_ids:
            for token_id in created_token_ids:
                content_data.append(["e", token_id, "", "created"])

        # Add e-tags for destroyed tokens (encrypted)
        if destroyed_token_ids:
            for token_id in destroyed_token_ids:
                content_data.append(["e", token_id, "", "destroyed"])

        # Encrypt content
        content_json = json.dumps(content_data)
        encrypted_content = nip44_encrypt(content_json, self._privkey)

        # Build tags (redeemed tags stay unencrypted)
        tags = []
        if redeemed_event_id:
            tags.append(["e", redeemed_event_id, "", "redeemed"])

        # Create history event
        event = create_event(
            kind=EventKind.History,
            content=encrypted_content,
            tags=tags,
        )

        return await self.relay_manager.publish_to_relays(event)

    async def clear_spending_history(self) -> int:
        """Delete all spending history events for this wallet.

        Returns:
            Number of history events deleted
        """
        # Fetch all wallet-related events
        all_events = await self.relay_manager.fetch_wallet_events(
            get_pubkey(self._privkey)
        )

        # Find history events and extract IDs
        history_events = [e for e in all_events if e["kind"] == EventKind.History]
        event_ids = [event["id"] for event in history_events]

        # Use batch deletion
        return await self._batch_delete_events(event_ids, EventKind.History)

    # ───────────────────────────── Token Events ───────────────────────────────

    async def count_token_events(self) -> int:
        """Count the number of token events for this wallet.

        Returns:
            Number of token events found
        """
        # Fetch all wallet-related events
        relays = await self.relay_manager.get_relay_connections()
        pubkey = get_pubkey(self._privkey)

        all_events: list[NostrEvent] = []
        event_ids_seen: set[str] = set()

        for relay in relays:
            try:
                events = await relay.fetch_wallet_events(pubkey)
                # Deduplicate events
                for event in events:
                    if event["id"] not in event_ids_seen:
                        all_events.append(event)
                        event_ids_seen.add(event["id"])
            except Exception:
                continue

        # Count token events
        token_events = [e for e in all_events if e["kind"] == EventKind.Token]
        return len(token_events)

    async def _split_large_token_events(
        self,
        proofs: list[ProofDict],
        mint_url: str,
        deleted_token_ids: list[str] | None = None,
    ) -> list[str]:
        """Split large token events into smaller chunks to avoid relay size limits."""
        if not proofs:
            return []

        # Maximum event size (leaving buffer for encryption overhead)
        max_size = 60000  # 60KB limit with buffer
        event_ids: list[str] = []
        current_batch: list[ProofDict] = []

        for proof in proofs:
            # Test adding this proof to current batch
            test_batch = current_batch + [proof]

            # Create test event content
            content_data = {
                "mint": mint_url,
                "proofs": test_batch,
            }

            # Add del field only to first event
            if deleted_token_ids and not event_ids:
                content_data["del"] = deleted_token_ids

            content_json = json.dumps(content_data)
            encrypted_content = nip44_encrypt(content_json, self._privkey)

            test_event = create_event(
                kind=EventKind.Token,
                content=encrypted_content,
                tags=[],
            )

            # Check if this would exceed size limit
            if (
                self.relay_manager.estimate_event_size(test_event) > max_size
                and current_batch
            ):
                # Current batch is full, create event and start new batch
                final_content_data = {
                    "mint": mint_url,
                    "proofs": current_batch,
                }

                # Add del field only to first event
                if deleted_token_ids and not event_ids:
                    final_content_data["del"] = deleted_token_ids

                final_content_json = json.dumps(final_content_data)
                final_encrypted_content = nip44_encrypt(
                    final_content_json, self._privkey
                )

                final_event = create_event(
                    kind=EventKind.Token,
                    content=final_encrypted_content,
                    tags=[],
                )

                event_id = await self.relay_manager.publish_to_relays(
                    final_event,
                    token_data=cast(dict[str, object], final_content_data),
                    priority=10,
                )
                event_ids.append(event_id)
                current_batch = [proof]
            else:
                current_batch.append(proof)

        # Add final batch if not empty
        if current_batch:
            final_content_data = {
                "mint": mint_url,
                "proofs": current_batch,
            }

            # Add del field only to first event
            if deleted_token_ids and not event_ids:
                final_content_data["del"] = deleted_token_ids

            final_content_json = json.dumps(final_content_data)
            final_encrypted_content = nip44_encrypt(final_content_json, self._privkey)

            final_event = create_event(
                kind=EventKind.Token,
                content=final_encrypted_content,
                tags=[],
            )

            event_id = await self.relay_manager.publish_to_relays(
                final_event,
                token_data=cast(dict[str, object], final_content_data),
                priority=10,
            )
            event_ids.append(event_id)

        return event_ids

    async def publish_token_event(
        self,
        proofs: list[ProofDict],
        *,
        deleted_token_ids: list[str] | None = None,
    ) -> str:
        """Publish encrypted token event (kind 7375) and return its id.

        SAFETY: This method now publishes new events BEFORE deleting old ones
        to prevent proof loss if relay publishing fails.
        """
        # Get mint URL from proofs or use default
        if proofs and proofs[0].get("mint"):
            mint_url = proofs[0]["mint"]
        else:
            mint_url = self.mint_urls[0] if self.mint_urls else None

        if not mint_url:
            raise WalletError("No mint URL available for token event")

        # Check if we need to split the event due to size
        # Create a test event to estimate size
        content_data = {
            "mint": mint_url,
            "proofs": proofs,
        }

        if deleted_token_ids:
            content_data["del"] = deleted_token_ids

        content_json = json.dumps(content_data)
        encrypted_content = nip44_encrypt(content_json, self._privkey)

        test_event = create_event(
            kind=EventKind.Token,
            content=encrypted_content,
            tags=[],
        )

        # If event is too large, split it
        if self.relay_manager.estimate_event_size(test_event) > 60000:
            event_ids = await self._split_large_token_events(
                proofs, mint_url, deleted_token_ids
            )
            return event_ids[0] if event_ids else ""

        # Event is small enough, publish as single event
        return await self.relay_manager.publish_to_relays(
            test_event,
            token_data=cast(dict[str, object], content_data),
            priority=10,  # High priority for token events
        )

    async def delete_token_event(self, event_id: str) -> None:
        """Delete a token event via NIP-09 (kind 5)."""
        # Create delete event
        event = create_event(
            kind=EventKind.Delete,
            content="",
            tags=[
                ["e", event_id],
                ["k", str(EventKind.Token)],
            ],
        )

        # Publish unsigned event (signing handled by relay manager)
        await self.relay_manager.publish_to_relays(event)

    async def clear_all_token_events(self) -> int:
        """Delete all token events for this wallet.

        ⚠️ WARNING: This deletes your actual token storage from Nostr relays!
        This will effectively set your balance to zero in terms of Nostr-stored tokens.

        Returns:
            Number of token events deleted
        """
        # Fetch all wallet-related events
        relays = await self.relay_manager.get_relay_connections()
        pubkey = get_pubkey(self._privkey)

        all_events: list[NostrEvent] = []
        event_ids_seen: set[str] = set()

        for relay in relays:
            try:
                events = await relay.fetch_wallet_events(pubkey)
                # Deduplicate events
                for event in events:
                    if event["id"] not in event_ids_seen:
                        all_events.append(event)
                        event_ids_seen.add(event["id"])
            except Exception:
                continue

        # Find token events and extract IDs
        token_events = [e for e in all_events if e["kind"] == EventKind.Token]
        event_ids = [event["id"] for event in token_events]

        # Use batch deletion
        return await self._batch_delete_events(event_ids, EventKind.Token)

    # ───────────────────────── Helper Methods ─────────────────────────────────

    async def _batch_delete_events(
        self, event_ids: list[str], event_kind: int, max_batch_size: int = 100
    ) -> int:
        """Delete events in batches to avoid oversized delete events.

        Args:
            event_ids: List of event IDs to delete
            event_kind: Kind of events being deleted (for the 'k' tag)
            max_batch_size: Maximum number of events per delete batch

        Returns:
            Number of events deleted
        """
        if not event_ids:
            return 0

        total_deleted = 0

        # Process in batches to avoid oversized events
        for i in range(0, len(event_ids), max_batch_size):
            batch = event_ids[i : i + max_batch_size]

            # Create delete event for this batch
            tags = []
            for event_id in batch:
                tags.append(["e", event_id])

            # Add the kind tag to specify what we're deleting
            tags.append(["k", str(event_kind)])

            # Create delete event
            delete_event = create_event(
                kind=EventKind.Delete,
                content="",
                tags=tags,
            )

            await self.relay_manager.publish_to_relays(delete_event)

            total_deleted += len(batch)

            # Small delay between batches to be nice to relays
            if i + max_batch_size < len(event_ids):
                await asyncio.sleep(0.1)

        return total_deleted
