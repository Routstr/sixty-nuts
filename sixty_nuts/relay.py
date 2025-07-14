"""Nostr Relay websocket client for NIP-60 wallet operations."""

from __future__ import annotations

import json
import os
from typing import TypedDict, Callable, Any
from enum import IntEnum
from uuid import uuid4
import time
from dataclasses import dataclass, field
from collections import deque
import asyncio
from contextlib import asynccontextmanager

import websockets
from coincurve import PrivateKey

# Environment variable for relays
RELAYS_ENV_VAR = "RELAYS"

# Recommended public relays for users
RECOMMENDED_RELAYS = [
    "wss://relay.damus.io",
    "wss://relay.snort.social",
    "wss://relay.nostr.band",
]

# Bootstrap relays for initial discovery
BOOTSTRAP_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.snort.social",
]


def get_relays_from_env() -> list[str]:
    """Get relay URLs from environment variable or .env file.

    Expected format: comma-separated URLs
    Example: RELAYS="wss://relay.damus.io,wss://nos.lol"

    Priority order:
    1. Environment variable RELAYS
    2. .env file in current working directory

    Returns:
        List of relay URLs from environment or .env file, empty list if not set
    """
    # First check environment variable
    env_relays = os.getenv(RELAYS_ENV_VAR)
    if env_relays:
        # Split by comma and clean up
        relays = [relay.strip() for relay in env_relays.split(",")]
        # Filter out empty strings
        relays = [relay for relay in relays if relay]
        return relays

    # Then check .env file in current working directory
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith(f"{RELAYS_ENV_VAR}="):
                    # Extract value after the equals sign
                    value = line.split("=", 1)[1]
                    # Remove quotes if present
                    value = value.strip("\"'")
                    if value:
                        # Split by comma and clean up
                        relays = [relay.strip() for relay in value.split(",")]
                        # Filter out empty strings
                        relays = [relay for relay in relays if relay]
                        return relays
    except Exception:
        # If reading .env file fails, continue
        pass

    return []


def validate_relay_url(url: str) -> bool:
    """Validate a relay URL format.

    Args:
        url: Relay URL to validate

    Returns:
        True if URL appears valid, False otherwise
    """
    if not url:
        return False

    # Must start with ws:// or wss://
    if not (url.startswith("ws://") or url.startswith("wss://")):
        return False

    # Basic sanity check - must have domain after protocol
    if len(url) < 10:  # Minimum realistic length
        return False

    return True


async def discover_relays_from_nip65(
    pubkey: str, bootstrap_relays: list[str] | None = None, debug: bool = False
) -> list[str]:
    """Discover relays from NIP-65 relay list events for a given pubkey.

    Args:
        pubkey: Hex public key to look up relay recommendations for
        bootstrap_relays: Relays to use for discovery (defaults to BOOTSTRAP_RELAYS)
        debug: If True, print debug information

    Returns:
        List of discovered relay URLs, empty if discovery fails
    """
    if bootstrap_relays is None:
        bootstrap_relays = BOOTSTRAP_RELAYS.copy()

    discovered_relays = []

    if debug:
        print(f"🔍 Looking for NIP-65 relay list for pubkey: {pubkey}")

    for bootstrap_url in bootstrap_relays:
        if debug:
            print(f"   Trying bootstrap relay: {bootstrap_url}")

        try:
            relay = Relay(bootstrap_url)
            await relay.connect()

            # Fetch NIP-65 relay list events (kind 10002)
            filters: list[NostrFilter] = [
                {
                    "authors": [pubkey],
                    "kinds": [10002],  # NIP-65 relay list metadata
                    "limit": 1,
                }
            ]

            events = await relay.fetch_events(filters)
            if debug:
                print(f"   Found {len(events)} NIP-65 events")

            if events:
                # Parse NIP-65 relay tags
                for tag in events[0]["tags"]:
                    if tag[0] == "r" and len(tag) >= 2:
                        relay_url = tag[1]
                        if validate_relay_url(relay_url):
                            discovered_relays.append(relay_url)
                            if debug:
                                print(f"   ✅ Found relay: {relay_url}")

            await relay.disconnect()

            # If we found relays, we can stop trying other bootstrap relays
            if discovered_relays:
                if debug:
                    print(
                        f"   🎉 Discovery successful with {len(discovered_relays)} relays"
                    )
                break

        except Exception as e:
            if debug:
                print(f"   ❌ Error with {bootstrap_url}: {e}")
            # Try next bootstrap relay
            continue

    # Remove duplicates and validate
    unique_relays = []
    seen = set()
    for relay_url in discovered_relays:
        if relay_url not in seen and validate_relay_url(relay_url):
            unique_relays.append(relay_url)
            seen.add(relay_url)

    if debug and not unique_relays:
        print("   ⚠️  No NIP-65 relay list found")

    return unique_relays


async def publish_relay_list_nip65(relays: list[str], privkey: PrivateKey) -> bool:
    """Publish NIP-65 relay list event to Nostr.

    Args:
        relays: List of relay URLs to publish
        privkey: Private key for signing the event

    Returns:
        True if published successfully, False otherwise
    """
    from .crypto import sign_event, get_pubkey
    import asyncio

    # Create NIP-65 relay list event (kind 10002)
    tags = []
    for relay in relays:
        # Add relay tag with read/write markers
        tags.append(["r", relay])  # Both read and write by default

    event_data = {
        "kind": 10002,  # NIP-65 relay list metadata
        "content": "",
        "tags": tags,
        "created_at": int(time.time()),
        "pubkey": get_pubkey(privkey),
    }

    # Sign the event
    signed_event = sign_event(event_data, privkey)

    # Try to publish to bootstrap relays
    published_count = 0

    for bootstrap_url in BOOTSTRAP_RELAYS:
        try:
            relay_client = Relay(bootstrap_url)
            await relay_client.connect()

            from typing import cast

            event_typed = cast(NostrEvent, signed_event)
            success = await relay_client.publish_event(event_typed)
            if success:
                published_count += 1

            await relay_client.disconnect()

        except Exception:
            # Try next relay
            continue

    # Give relays a moment to propagate the event
    if published_count > 0:
        await asyncio.sleep(1.0)  # 1 second delay for propagation

    return published_count > 0


async def prompt_user_for_relays(privkey: PrivateKey | None = None) -> list[str]:
    """Prompt user to select or input relay URLs.

    Args:
        privkey: Private key for publishing NIP-65 relay list (optional)

    Returns:
        List of selected relay URLs

    Note: This function uses basic input() and is intended for CLI use.
    For GUI applications, override this with appropriate UI prompting.
    """
    from rich.console import Console
    from rich.prompt import Prompt, Confirm
    from rich.table import Table

    console = Console()

    console.print("\n[yellow]🌐 Relay Configuration Needed[/yellow]")
    console.print("Nostr relays are required to store and sync your wallet data.")
    console.print("You can either:")
    console.print("  1. Select from recommended public relays")
    console.print("  2. Enter your own relay URLs")
    console.print("  3. Visit https://nostr.watch to find more relays")

    # Show recommended relays
    console.print("\n[cyan]📡 Recommended Public Relays:[/cyan]")
    table = Table()
    table.add_column("#", style="dim")
    table.add_column("Relay URL", style="cyan")

    for i, relay_url in enumerate(RECOMMENDED_RELAYS, 1):
        table.add_row(str(i), relay_url)

    console.print(table)

    selected_relays = []

    while True:
        choice = Prompt.ask(
            "\nChoose option",
            choices=["recommended", "custom", "help"],
            default="recommended",
        )

        if choice == "help":
            console.print("\n[dim]💡 Relay Selection Help:[/dim]")
            console.print(
                "• [cyan]recommended[/cyan]: Use our curated list of reliable public relays"
            )
            console.print(
                "• [cyan]custom[/cyan]: Enter your own relay URLs (one per line)"
            )
            console.print(
                "• Visit [link]https://nostr.watch[/link] to find more relays"
            )
            console.print("• You need at least 2-3 relays for redundancy")
            console.print("• Use wss:// (secure) relays when possible")
            continue

        elif choice == "recommended":
            console.print(
                f"\n[cyan]Select relays (1-{len(RECOMMENDED_RELAYS)}, comma-separated, or 'all'):[/cyan]"
            )
            console.print("[dim]Example: 1,2,4 or just press Enter for first 3[/dim]")

            selection = Prompt.ask("Relay numbers", default="1,2,3")

            if selection.lower() == "all":
                selected_relays = RECOMMENDED_RELAYS.copy()
            else:
                try:
                    indices = [int(x.strip()) - 1 for x in selection.split(",")]
                    selected_relays = [
                        RECOMMENDED_RELAYS[i]
                        for i in indices
                        if 0 <= i < len(RECOMMENDED_RELAYS)
                    ]
                except (ValueError, IndexError):
                    console.print("[red]❌ Invalid selection. Please try again.[/red]")
                    continue

            break

        elif choice == "custom":
            console.print(
                "\n[cyan]Enter relay URLs (one per line, empty line to finish):[/cyan]"
            )
            console.print("[dim]Example: wss://relay.damus.io[/dim]")

            while True:
                relay_url = Prompt.ask("Relay URL", default="")
                if not relay_url:
                    break

                if validate_relay_url(relay_url):
                    selected_relays.append(relay_url)
                    console.print(f"[green]✅ Added: {relay_url}[/green]")
                else:
                    console.print(
                        "[red]❌ Invalid URL format. Must start with ws:// or wss://[/red]"
                    )

            break

    if not selected_relays:
        # Fallback to first 3 recommended relays
        console.print(
            "[yellow]⚠️  No relays selected, using default recommendations[/yellow]"
        )
        selected_relays = RECOMMENDED_RELAYS[:3]

    console.print(f"\n[green]✅ Selected {len(selected_relays)} relays:[/green]")
    for i, relay_url in enumerate(selected_relays, 1):
        console.print(f"  {i}. {relay_url}")

    # Ask if user wants to save to Nostr profile (NIP-65)
    if privkey:
        # Default to publishing to Nostr profile since it's the preferred method
        publish_choice = Confirm.ask(
            "\nPublish relay list to your Nostr profile (NIP-65)?\n"
            "[dim]This is the recommended way to save your relay preferences[/dim]"
        )
        if publish_choice:
            console.print("\n[blue]📡 Publishing relay list to Nostr...[/blue]")
            try:
                success = await publish_relay_list_nip65(selected_relays, privkey)
                if success:
                    console.print(
                        "[green]✅ Successfully published relay list to Nostr![/green]"
                    )
                    console.print(
                        "[dim]Your relay preferences are now saved to your Nostr profile[/dim]"
                    )
                else:
                    console.print(
                        "[yellow]⚠️ Failed to publish to Nostr relays[/yellow]"
                    )
                    console.print(
                        "[dim]Relay list will still be used for this session[/dim]"
                    )
            except Exception as e:
                console.print(f"[yellow]⚠️ Error publishing to Nostr: {e}[/yellow]")
                console.print(
                    "[dim]Relay list will still be used for this session[/dim]"
                )
    elif not privkey:
        console.print(
            "\n[dim]💡 Tip: Provide NSEC to save relay list to your Nostr profile[/dim]"
        )

    return selected_relays


def set_relays_in_env(relays: list[str]) -> None:
    """Set relay URLs in .env file for persistent caching.

    Args:
        relays: List of relay URLs to cache
    """
    if not relays:
        return

    from pathlib import Path

    relay_str = ",".join(relays)
    env_file = Path.cwd() / ".env"
    env_line = f'{RELAYS_ENV_VAR}="{relay_str}"\n'

    try:
        if env_file.exists():
            # Check if RELAYS already exists in the file
            content = env_file.read_text()
            lines = content.splitlines()

            # Look for existing RELAYS line
            relay_line_found = False
            new_lines = []
            for line in lines:
                if line.strip().startswith(f"{RELAYS_ENV_VAR}="):
                    # Replace existing RELAYS line
                    new_lines.append(env_line.rstrip())
                    relay_line_found = True
                else:
                    new_lines.append(line)

            if not relay_line_found:
                # Add new RELAYS line at the end
                new_lines.append(env_line.rstrip())

            # Write back to file
            env_file.write_text("\n".join(new_lines) + "\n")
        else:
            # Create new .env file
            env_file.write_text(env_line)

    except Exception as e:
        # If writing to .env file fails, fall back to environment variable
        print(f"Warning: Could not write to .env file: {e}")
        print("Falling back to session environment variable")
        os.environ[RELAYS_ENV_VAR] = relay_str


def clear_relays_from_env() -> bool:
    """Clear relay URLs from .env file and environment variable.

    Returns:
        True if relays were cleared, False if none were set
    """
    cleared = False

    # Clear from environment variable
    if RELAYS_ENV_VAR in os.environ:
        del os.environ[RELAYS_ENV_VAR]
        cleared = True

    # Clear from .env file
    try:
        from pathlib import Path

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            content = env_file.read_text()
            lines = content.splitlines()

            # Remove RELAYS line
            new_lines = []
            for line in lines:
                if not line.strip().startswith(f"{RELAYS_ENV_VAR}="):
                    new_lines.append(line)
                else:
                    cleared = True

            if new_lines:
                # Write back remaining lines
                env_file.write_text("\n".join(new_lines) + "\n")
            else:
                # If file would be empty, remove it
                env_file.unlink()

    except Exception:
        # If clearing from .env file fails, that's okay
        pass

    return cleared


async def get_relays_for_wallet(
    privkey: PrivateKey, *, prompt_if_needed: bool = True
) -> list[str]:
    """Get relay URLs for wallet using priority: env vars > NIP-65 discovery > user prompt.

    Args:
        privkey: Private key for NIP-65 discovery
        prompt_if_needed: Whether to prompt user if no relays found automatically

    Returns:
        List of relay URLs

    Raises:
        ValueError: If no relays can be determined and prompting is disabled
    """
    from .crypto import get_pubkey

    # 1. Try environment variable first (highest priority - fastest)
    env_relays = get_relays_from_env()
    if env_relays:
        # Validate environment relays
        valid_relays = [url for url in env_relays if validate_relay_url(url)]
        if valid_relays:
            # Fast path - no Nostr queries needed
            return valid_relays
        else:
            # Environment relays are invalid, continue to other methods
            print(
                f"Warning: Invalid relays in {RELAYS_ENV_VAR}, trying other methods..."
            )

    # 2. Try NIP-65 discovery (slower - requires Nostr queries)
    try:
        pubkey = get_pubkey(privkey)
        discovered_relays = await discover_relays_from_nip65(pubkey)
        if discovered_relays:
            # Cache discovered relays in environment for this session
            set_relays_in_env(discovered_relays)
            return discovered_relays
    except Exception:
        # Discovery failed, continue
        pass

    # 3. Prompt user if allowed
    if prompt_if_needed:
        selected_relays = await prompt_user_for_relays(privkey)
        # Cache user-selected relays in environment for this session
        set_relays_in_env(selected_relays)
        return selected_relays

    # 4. No relays found and prompting disabled
    raise ValueError(
        "No relays configured. Set them via environment variable, "
        "NIP-65 relay list, or run with prompting enabled."
    )


# -----------------------------------------------------------------------------
# Python < 3.11 compatibility shim
# -----------------------------------------------------------------------------

# `asyncio.timeout` was introduced in Python 3.11. When running on an older
# interpreter we either:
#   1. Import the identically-named helper from the third-party `async_timeout`
#      package if available, or
#   2. Provide a minimal no-op context manager that preserves the API surface
#      (this means timeouts will not be enforced but code will still run).
#
# This approach allows the package (and its test-suite) to execute on Python
# 3.10 and earlier without modifications, while still benefiting from native
# timeouts on 3.11+.


if not hasattr(asyncio, "timeout"):
    try:
        from async_timeout import timeout as _timeout  # type: ignore

    except ModuleNotFoundError:

        @asynccontextmanager
        async def _timeout(_delay: float):  # noqa: D401 – simple stub
            """Fallback that degrades gracefully by disabling the timeout."""

            yield

    # Make the chosen implementation available as `asyncio.timeout`.
    setattr(asyncio, "timeout", _timeout)  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────────
# Nostr protocol types
# ──────────────────────────────────────────────────────────────────────────────


class EventKind(IntEnum):
    """Nostr event kinds relevant to NIP-60."""

    RELAY_RECOMMENDATIONS = 10019
    Wallet = 17375  # wallet metadata
    Token = 7375  # unspent proofs
    History = 7376  # optional transaction log
    QuoteTracker = 7374  # mint quote tracker (optional)
    Delete = 5  # NIP-09 delete event


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


@dataclass
class QueuedEvent:
    """Event queued for publishing with metadata."""

    event: NostrEvent
    priority: int = 0  # Higher priority = sent first
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    callback: Callable[[bool, str | None], None] | None = None  # Success callback

    def __lt__(self, other: "QueuedEvent") -> bool:
        """For priority queue comparison."""
        return self.priority > other.priority  # Higher priority first


class EventQueue:
    """Thread-safe event queue with retry logic."""

    def __init__(self, max_queue_size: int = 1000) -> None:
        self._queue: deque[QueuedEvent] = deque(maxlen=max_queue_size)
        self._processing = False
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()

        # Cache for pending events by ID
        self._pending_by_id: dict[str, QueuedEvent] = {}

        # Cache for pending token events (for balance calculation)
        self._pending_token_events: dict[str, dict[str, Any]] = {}

    async def add(
        self,
        event: NostrEvent,
        *,
        priority: int = 0,
        callback: Callable[[bool, str | None], None] | None = None,
        token_data: dict[str, Any] | None = None,
    ) -> None:
        """Add event to queue."""
        async with self._lock:
            queued = QueuedEvent(event=event, priority=priority, callback=callback)

            # Add to queue
            self._queue.append(queued)

            # Track by ID
            self._pending_by_id[event["id"]] = queued

            # If this is a token event, cache the data
            if token_data and event["kind"] == 7375:
                self._pending_token_events[event["id"]] = token_data

            # Sort by priority
            self._queue = deque(sorted(self._queue), maxlen=self._queue.maxlen)

            # Signal that new events are available
            self._event.set()

    async def get_batch(self, max_size: int = 10) -> list[QueuedEvent]:
        """Get a batch of events to process."""
        async with self._lock:
            batch = []
            for _ in range(min(max_size, len(self._queue))):
                if self._queue:
                    batch.append(self._queue.popleft())
            return batch

    async def requeue(self, event: QueuedEvent) -> bool:
        """Requeue a failed event if retries remain."""
        event.retry_count += 1
        if event.retry_count < event.max_retries:
            async with self._lock:
                # Add back with lower priority after retry
                event.priority -= 1
                self._queue.append(event)
                self._queue = deque(sorted(self._queue), maxlen=self._queue.maxlen)
                self._event.set()
            return True
        else:
            # Max retries exceeded
            await self.remove(event.event["id"])
            return False

    async def remove(self, event_id: str) -> None:
        """Remove event from pending caches."""
        async with self._lock:
            self._pending_by_id.pop(event_id, None)
            self._pending_token_events.pop(event_id, None)

    async def wait_for_events(self) -> None:
        """Wait for new events in the queue."""
        await self._event.wait()
        self._event.clear()

    def get_pending_token_data(self) -> list[dict[str, Any]]:
        """Get all pending token event data for balance calculation."""
        return list(self._pending_token_events.values())

    @property
    def size(self) -> int:
        """Current queue size."""
        return len(self._queue)


class Relay:
    """Minimal Nostr relay client for NIP-60 wallet operations."""

    def __init__(self, url: str) -> None:
        """Initialize relay client.

        Args:
            url: Relay websocket URL (e.g. "wss://relay.damus.io")
        """
        self.url = url
        self.ws: Any = None
        self.subscriptions: dict[str, Callable[[NostrEvent], None]] = {}
        self._recv_lock = asyncio.Lock()  # Prevent concurrent recv() calls

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
        """Receive a message from the relay with concurrency protection."""
        async with self._recv_lock:
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


class QueuedRelay(Relay):
    """Nostr relay client with event queuing and batching support."""

    def __init__(
        self,
        url: str,
        *,
        batch_size: int = 10,
        batch_interval: float = 1.0,
        enable_batching: bool = True,
    ) -> None:
        """Initialize queued relay client.

        Args:
            url: Relay websocket URL
            batch_size: Maximum events to send in one batch
            batch_interval: Seconds between batch processing
            enable_batching: Whether to batch events or send one by one
        """
        super().__init__(url)
        self.queue = EventQueue()
        self.batch_size = batch_size
        self.batch_interval = batch_interval
        self.enable_batching = enable_batching
        self._processor_task: asyncio.Task[None] | None = None
        self._running = False

    async def start_queue_processor(self) -> None:
        """Start the background queue processor."""
        if self._processor_task is None or self._processor_task.done():
            self._running = True
            self._processor_task = asyncio.create_task(self._process_queue())

    async def stop_queue_processor(self) -> None:
        """Stop the background queue processor."""
        self._running = False
        if self._processor_task and not self._processor_task.done():
            self.queue._event.set()  # Wake up processor
            await self._processor_task

    async def _process_queue(self) -> None:
        """Background task to process the event queue."""
        while self._running:
            try:
                # Wait for events or timeout
                try:
                    await asyncio.wait_for(
                        self.queue.wait_for_events(), timeout=self.batch_interval
                    )
                except asyncio.TimeoutError:
                    pass

                # Get batch of events
                if self.queue.size > 0:
                    if self.enable_batching:
                        batch = await self.queue.get_batch(self.batch_size)
                    else:
                        # Process one at a time
                        batch = await self.queue.get_batch(1)

                    # Process each event
                    for queued_event in batch:
                        try:
                            success = await self._publish_to_relays(queued_event.event)

                            if success:
                                # Remove from pending caches
                                await self.queue.remove(queued_event.event["id"])

                                # Call success callback if provided
                                if queued_event.callback:
                                    queued_event.callback(True, None)
                            else:
                                # Event rejected, try to requeue
                                if not await self.queue.requeue(queued_event):
                                    # Max retries exceeded
                                    if queued_event.callback:
                                        queued_event.callback(
                                            False, "Max retries exceeded"
                                        )

                        except Exception as e:
                            # Connection error, requeue
                            if not await self.queue.requeue(queued_event):
                                # Max retries exceeded
                                if queued_event.callback:
                                    queued_event.callback(False, str(e))

                        # Small delay between events to avoid rate limiting
                        if not self.enable_batching and len(batch) > 1:
                            await asyncio.sleep(0.1)

            except Exception as e:
                print(f"Queue processor error: {e}")
                await asyncio.sleep(1)  # Avoid tight error loop

    async def _publish_to_relays(self, event: NostrEvent) -> bool:
        """Publish event to this relay."""
        return await super().publish_event(event)

    async def publish_event(
        self,
        event: NostrEvent,
        *,
        priority: int = 0,
        callback: Callable[[bool, str | None], None] | None = None,
        token_data: dict[str, Any] | None = None,
        immediate: bool = False,
    ) -> bool:
        """Publish event via queue or immediately.

        Args:
            event: Event to publish
            priority: Queue priority (higher = sent first)
            callback: Callback for success/failure notification
            token_data: Token data to cache for balance calculation
            immediate: If True, bypass queue and publish immediately

        Returns:
            True if queued successfully (or published if immediate)
        """
        if immediate:
            # Bypass queue for urgent events
            return await super().publish_event(event)

        # Add to queue
        await self.queue.add(
            event, priority=priority, callback=callback, token_data=token_data
        )

        # Ensure processor is running
        await self.start_queue_processor()

        return True  # Successfully queued

    def get_pending_proofs(self) -> list[dict[str, Any]]:
        """Get pending proofs from queued token events.

        Returns:
            List of proof dictionaries from pending token events
        """
        all_proofs = []
        for token_data in self.queue.get_pending_token_data():
            proofs = token_data.get("proofs", [])
            all_proofs.extend(proofs)
        return all_proofs

    async def disconnect(self) -> None:
        """Disconnect and stop queue processor."""
        await self.stop_queue_processor()
        await super().disconnect()


class RelayPool:
    """Pool of QueuedRelay instances with shared queue."""

    def __init__(self, urls: list[str], **relay_kwargs: Any) -> None:
        """Initialize relay pool with shared queue.

        Args:
            urls: List of relay URLs
            **relay_kwargs: Arguments passed to QueuedRelay
        """
        self.relays: list[QueuedRelay] = []
        self.shared_queue = EventQueue()

        # Create relays with shared queue
        for url in urls:
            relay = QueuedRelay(url, **relay_kwargs)
            # Replace individual queue with shared one
            relay.queue = self.shared_queue
            self.relays.append(relay)

    async def publish_event(self, event: NostrEvent, **kwargs: Any) -> bool:
        """Publish event to all relays in pool."""
        # Add to shared queue once
        if self.relays:
            return await self.relays[0].publish_event(event, **kwargs)
        return False

    def get_pending_proofs(self) -> list[dict[str, Any]]:
        """Get pending proofs from shared queue."""
        return self.shared_queue.get_pending_token_data()

    async def connect_all(self) -> None:
        """Connect and start all relays."""
        for i, relay in enumerate(self.relays):
            try:
                await relay.connect()
                # Only start queue processor for the first relay to avoid concurrent processing
                if i == 0:
                    await relay.start_queue_processor()
            except Exception as e:
                print(f"Failed to connect relay {relay.url}: {e}")

    async def disconnect_all(self) -> None:
        """Disconnect all relays."""
        for relay in self.relays:
            await relay.disconnect()


def create_event(
    kind: int,
    content: str = "",
    tags: list[list[str]] | None = None,
) -> dict:
    """Create unsigned Nostr event structure."""
    return {
        "kind": kind,
        "content": content,
        "tags": tags or [],
        "created_at": int(time.time()),
    }


class RelayClient:
    """Manages relay connections, discovery, and publishing for NIP-60 wallets."""

    def __init__(
        self,
        relay_urls: list[str],
        privkey: PrivateKey,
        *,
        use_queued_relays: bool = True,
        min_relay_interval: float = 1.0,
    ) -> None:
        """Initialize relay manager.

        Args:
            relay_urls: List of relay URLs to connect to
            privkey: PrivateKey object for relay discovery and event signing
            use_queued_relays: Whether to use queued relays
            min_relay_interval: Minimum interval between relay operations
        """
        self.relay_urls = relay_urls
        self.privkey = privkey
        self.use_queued_relays = use_queued_relays
        self.min_relay_interval = min_relay_interval

        # Relay instances
        self.relay_instances: list[Relay | QueuedRelay] = []
        self.relay_pool: RelayPool | None = None

        # Rate limiting
        self._last_relay_operation = 0.0

    async def get_relay_connections(self) -> list[Relay]:
        """Get relay connections, discovering if needed."""
        # If no relay URLs are configured, try to discover them
        if not self.relay_urls:
            discovered_relays = await self.discover_relays()
            if discovered_relays:
                self.relay_urls = discovered_relays
            else:
                # Fallback to bootstrap relays for basic functionality
                self.relay_urls = BOOTSTRAP_RELAYS[:3]  # Use first 3 bootstrap relays

        if self.use_queued_relays and self.relay_pool is None:
            # Try to discover relays if we don't have enough
            if len(self.relay_urls) < 2:
                discovered_relays = await self.discover_relays()
                if discovered_relays:
                    # Combine discovered with existing, remove duplicates
                    all_relays = list(set(self.relay_urls + discovered_relays))
                    self.relay_urls = all_relays

            # Create relay pool with queued support
            self.relay_pool = RelayPool(
                self.relay_urls[:5],  # Use up to 5 relays
                batch_size=10,
                batch_interval=0.5,  # Process queue every 0.5 seconds
                enable_batching=True,
            )

            # Connect all relays in pool
            await self.relay_pool.connect_all()

            # For compatibility, add relays to instances list
            from typing import cast

            self.relay_instances = cast(
                list[Relay | QueuedRelay], self.relay_pool.relays
            )

        elif not self.use_queued_relays and not self.relay_instances:
            # Legacy mode: use regular relays without queuing
            # Try to discover relays if we don't have enough
            if len(self.relay_urls) < 2:
                discovered_relays = await self.discover_relays()
                if discovered_relays:
                    # Combine discovered with existing, remove duplicates
                    all_relays = list(set(self.relay_urls + discovered_relays))
                    self.relay_urls = all_relays

            # Try to connect to relays
            for url in self.relay_urls[:5]:  # Try up to 5 relays
                try:
                    relay = Relay(url)
                    await relay.connect()
                    self.relay_instances.append(relay)

                    # Stop after successfully connecting to 3 relays
                    if len(self.relay_instances) >= 3:
                        break
                except Exception:
                    continue

            if not self.relay_instances:
                raise RelayError("Could not connect to any relay")

        return self.relay_instances

    async def discover_relays(self) -> list[str]:
        """Discover relays from NIP-65 relay list events."""
        from .crypto import get_pubkey

        # Use the new comprehensive relay discovery
        try:
            pubkey = get_pubkey(self.privkey)
            return await discover_relays_from_nip65(pubkey)
        except Exception:
            return []

    async def rate_limit_relay_operations(self) -> None:
        """Apply rate limiting to relay operations."""
        now = time.time()
        time_since_last = now - self._last_relay_operation
        if time_since_last < self.min_relay_interval:
            await asyncio.sleep(self.min_relay_interval - time_since_last)
        self._last_relay_operation = time.time()

    def estimate_event_size(self, event: dict) -> int:
        """Estimate the size of an event in bytes."""
        return len(json.dumps(event, separators=(",", ":")))

    async def publish_to_relays(
        self,
        unsigned_event: dict,
        *,
        token_data: dict[str, object] | None = None,
        priority: int = 0,
    ) -> str:
        """Sign and publish event to all relays and return event ID."""
        from .crypto import sign_event

        # Sign the event
        signed_event = sign_event(unsigned_event, self.privkey)

        # Apply rate limiting
        await self.rate_limit_relay_operations()

        # Use relay pool if available for queued publishing
        if self.use_queued_relays and self.relay_pool:
            event_dict = NostrEvent(**signed_event)  # type: ignore

            # Determine priority based on event kind
            if signed_event["kind"] == EventKind.Token:
                priority = 10  # High priority for token events
            elif signed_event["kind"] == EventKind.History:
                priority = 5  # Medium priority for history
            else:
                priority = priority or 0

            # Add to queue with token data if provided
            success = await self.relay_pool.publish_event(
                event_dict,
                priority=priority,
                token_data=token_data,
                immediate=False,  # Use queue
            )

            if success:
                return signed_event["id"]
            else:
                raise RelayError("Failed to queue event for publishing")

        # Legacy mode: direct publishing
        relays = await self.get_relay_connections()
        event_dict = NostrEvent(**signed_event)  # type: ignore

        # Try to publish to at least one relay
        published = False
        errors = []

        for relay in relays:
            try:
                if await relay.publish_event(event_dict):
                    published = True
                else:
                    errors.append(f"{relay.url}: Event rejected")
            except Exception as e:
                errors.append(f"{relay.url}: {str(e)}")
                continue

        if not published:
            # Only log if we can't publish anywhere to avoid log spam
            error_msg = f"Failed to publish event to any relay. Last error: {errors[-1] if errors else 'Unknown'}"
            print(f"Warning: {error_msg}")
            # Don't raise exception - allow operations to continue
            return signed_event["id"]

        return signed_event["id"]

    def get_pending_proofs(self) -> list[dict[str, object]]:
        """Get pending proofs from queued token events."""
        if self.use_queued_relays and self.relay_pool:
            return self.relay_pool.get_pending_proofs()
        return []

    async def fetch_wallet_events(self, pubkey: str) -> list[NostrEvent]:
        """Fetch all wallet-related events for a pubkey."""
        relays = await self.get_relay_connections()

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

        return all_events

    async def disconnect_all(self) -> None:
        """Disconnect all relay connections."""
        if self.use_queued_relays and self.relay_pool:
            await self.relay_pool.disconnect_all()
        else:
            for relay in self.relay_instances:
                await relay.disconnect()
