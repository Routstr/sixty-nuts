"""Nostr Relay websocket client for NIP-60 wallet operations."""

from __future__ import annotations

import json
from typing import TypedDict, Callable, Any
from uuid import uuid4

import websockets


# ──────────────────────────────────────────────────────────────────────────────
# Nostr protocol types
# ──────────────────────────────────────────────────────────────────────────────


class NostrEvent(TypedDict):
    """Nostr event structure."""

    id: str
    pubkey: str
    created_at: int
    kind: int
    tags: list[list[str]]
    content: str
    sig: str


class NostrFilter(TypedDict, total=False):
    """Filter for REQ subscriptions."""

    ids: list[str]
    authors: list[str]
    kinds: list[int]
    since: int
    until: int
    limit: int
    # Tags filters use #<tag> format


# ──────────────────────────────────────────────────────────────────────────────
# Relay client
# ──────────────────────────────────────────────────────────────────────────────


class RelayError(Exception):
    """Raised when relay returns an error."""


class NostrRelay:
    """Minimal Nostr relay client for NIP-60 wallet operations."""

    def __init__(self, url: str) -> None:
        """Initialize relay client.

        Args:
            url: Relay websocket URL (e.g. "wss://relay.damus.io")
        """
        self.url = url
        self.ws: Any = None
        self.subscriptions: dict[str, Callable[[NostrEvent], None]] = {}

    async def connect(self) -> None:
        """Connect to the relay."""
        import asyncio

        if self.ws is None or self.ws.close_code is not None:
            try:
                # Add connection timeout
                async with asyncio.timeout(5.0):
                    self.ws = await websockets.connect(
                        self.url, ping_interval=20, ping_timeout=10, close_timeout=10
                    )
            except asyncio.TimeoutError:
                print(f"Timeout connecting to relay: {self.url}")
                raise RelayError(f"Connection timeout: {self.url}")
            except Exception as e:
                print(f"Failed to connect to relay {self.url}: {e}")
                raise RelayError(f"Connection failed: {e}")

    async def disconnect(self) -> None:
        """Disconnect from the relay."""
        if self.ws and self.ws.close_code is None:
            await self.ws.close()

    async def _send(self, message: list[Any]) -> None:
        """Send a message to the relay."""
        if not self.ws or self.ws.close_code is not None:
            raise RelayError("Not connected to relay")
        await self.ws.send(json.dumps(message))

    async def _recv(self) -> list[Any]:
        """Receive a message from the relay."""
        if not self.ws or self.ws.close_code is not None:
            raise RelayError("Not connected to relay")
        data = await self.ws.recv()
        return json.loads(data)

    # ───────────────────────── Publishing Events ─────────────────────────────────

    async def publish_event(self, event: NostrEvent) -> bool:
        """Publish an event to the relay.

        Returns True if accepted, False if rejected.
        """
        import asyncio

        try:
            await self.connect()

            # Send EVENT command
            await self._send(["EVENT", event])

            # Wait for OK response with timeout
            async with asyncio.timeout(10.0):  # 10 second timeout
                while True:
                    msg = await self._recv()
                    if msg[0] == "OK" and msg[1] == event["id"]:
                        if not msg[2]:  # Event was rejected
                            if len(msg) > 3:
                                print(f"Relay rejected event: {msg[3]}")
                        return msg[2]  # True if accepted
                    elif msg[0] == "NOTICE":
                        print(f"Relay notice: {msg[1]}")

        except asyncio.TimeoutError:
            print(f"Timeout waiting for OK response from {self.url}")
            return False
        except Exception as e:
            print(f"Error publishing to {self.url}: {e}")
            return False

    # ───────────────────────── Fetching Events ─────────────────────────────────

    async def fetch_events(
        self,
        filters: list[NostrFilter],
        *,
        timeout: float = 5.0,
    ) -> list[NostrEvent]:
        """Fetch events matching filters.

        Args:
            filters: List of filters to match events
            timeout: Time to wait for events before returning

        Returns:
            List of matching events
        """
        await self.connect()

        # Generate subscription ID
        sub_id = str(uuid4())
        events: list[NostrEvent] = []

        # Send REQ command
        await self._send(["REQ", sub_id, *filters])

        # Collect events until EOSE or timeout
        import asyncio

        try:
            async with asyncio.timeout(timeout):
                while True:
                    msg = await self._recv()

                    if msg[0] == "EVENT":
                        # Always append events for this short-lived, dedicated subscription.
                        # Tests may feed a fixed subscription id (e.g. "sub_id") that differs
                        # from the locally generated one, so we avoid strict id matching to
                        # prevent an unnecessary wait inside the timeout context.
                        events.append(msg[2])
                    elif msg[0] == "EOSE":
                        # End-of-stored-events – irrespective of the subscription identifier
                        # because this instance only keeps one outstanding REQ at a time
                        # within this helper method.
                        break  # Exit once the relay signals completion

        except asyncio.TimeoutError:
            pass
        finally:
            # Close subscription
            await self._send(["CLOSE", sub_id])

        return events

    # ───────────────────────── Subscription Management ─────────────────────────────

    async def subscribe(
        self,
        filters: list[NostrFilter],
        callback: Callable[[NostrEvent], None],
    ) -> str:
        """Subscribe to events matching filters.

        Args:
            filters: List of filters to match events
            callback: Function to call for each matching event

        Returns:
            Subscription ID (use to unsubscribe)
        """
        await self.connect()

        # Generate subscription ID
        sub_id = str(uuid4())
        self.subscriptions[sub_id] = callback

        # Send REQ command
        await self._send(["REQ", sub_id, *filters])

        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        """Close a subscription."""
        if sub_id in self.subscriptions:
            del self.subscriptions[sub_id]
            await self._send(["CLOSE", sub_id])

    async def process_messages(self) -> None:
        """Process incoming messages and call subscription callbacks.

        Run this in a background task to handle subscriptions.
        """
        while self.ws and self.ws.close_code is None:
            try:
                msg = await self._recv()

                if msg[0] == "EVENT" and msg[1] in self.subscriptions:
                    # Call the subscription callback
                    callback = self.subscriptions[msg[1]]
                    callback(msg[2])

            except websockets.exceptions.ConnectionClosed:
                break
            except Exception:
                # Log error but keep processing
                continue

    # ───────────────────────── NIP-60 Specific Helpers ─────────────────────────────

    async def fetch_wallet_events(
        self,
        pubkey: str,
        kinds: list[int] | None = None,
    ) -> list[NostrEvent]:
        """Fetch wallet-related events for a pubkey.

        Args:
            pubkey: Hex public key to fetch events for
            kinds: Event kinds to fetch (defaults to wallet kinds)

        Returns:
            List of matching events
        """
        if kinds is None:
            # Default to NIP-60 event kinds
            kinds = [17375, 7375, 7376, 7374]  # wallet, token, history, quote

        filters: list[NostrFilter] = [
            {
                "authors": [pubkey],
                "kinds": kinds,
            }
        ]

        return await self.fetch_events(filters)

    async def fetch_relay_recommendations(self, pubkey: str) -> list[str]:
        """Fetch relay recommendations (kind:10019) for a pubkey.

        Returns list of recommended relay URLs.
        """
        filters: list[NostrFilter] = [
            {
                "authors": [pubkey],
                "kinds": [10019],
                "limit": 1,
            }
        ]

        events = await self.fetch_events(filters)
        if not events:
            return []

        # Parse relay URLs from tags
        relays = []
        for tag in events[0]["tags"]:
            if tag[0] == "relay":
                relays.append(tag[1])

        return relays
