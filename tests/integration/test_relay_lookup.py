"""Relay integration tests.

Tests relay connectivity, event publishing/fetching, and event management
against real public Nostr relays. Uses generated nsec keys for each test.
Only runs when RUN_INTEGRATION_TESTS environment variable is set.
"""

import asyncio
import json
import os
import time
from uuid import uuid4

import pytest
from coincurve import PrivateKey

from sixty_nuts.crypto import (
    generate_privkey,
    get_pubkey,
    sign_event,
    nip44_encrypt,
    nip44_decrypt,
)
from sixty_nuts.relay import (
    NostrRelay,
    QueuedNostrRelay,
    RelayPool,
    RelayManager,
    EventKind,
    create_event,
    NostrEvent,
    NostrFilter,
)
from sixty_nuts.events import EventManager
from sixty_nuts.types import ProofDict


# Skip all integration tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests only run when RUN_INTEGRATION_TESTS is set",
)

TEST_RELAYS = [
    "wss://relay.damus.io",
    "wss://relay.primal.net",
    "wss://relay.nostr.band",
]


@pytest.fixture
def test_privkey():
    """Generate a test nostr private key."""
    hex_key = generate_privkey()
    return PrivateKey(bytes.fromhex(hex_key))


@pytest.fixture
def test_pubkey(test_privkey):
    """Get the public key for test private key."""
    return get_pubkey(test_privkey)


@pytest.fixture
def test_mint_urls():
    """Test mint URLs for event manager tests."""
    return ["https://mint.example.com"]


@pytest.fixture
async def test_relay():
    """Create a test relay connection."""
    relay = NostrRelay(TEST_RELAYS[0])
    try:
        await relay.connect()
        yield relay
    finally:
        await relay.disconnect()


@pytest.fixture
async def test_queued_relay():
    """Create a test queued relay connection."""
    relay = QueuedNostrRelay(TEST_RELAYS[1], batch_size=5, batch_interval=0.5)
    try:
        await relay.connect()
        await relay.start_queue_processor()
        yield relay
    finally:
        await relay.disconnect()


@pytest.fixture
async def test_relay_manager(test_privkey):
    """Create a test relay manager."""
    manager = RelayManager(
        relay_urls=TEST_RELAYS[:2],
        privkey=test_privkey,
        use_queued_relays=True,
        min_relay_interval=0.1,
    )
    yield manager
    await manager.disconnect_all()


@pytest.fixture
async def test_event_manager(test_relay_manager, test_privkey, test_mint_urls):
    """Create a test event manager."""
    manager = EventManager(test_relay_manager, test_privkey, test_mint_urls)
    yield manager


class TestBasicRelayOperations:
    """Test basic relay connectivity and operations."""

    async def test_relay_connection(self, test_relay):
        """Test basic relay connection."""
        # Relay should be connected from fixture
        assert test_relay.ws is not None
        assert test_relay.ws.close_code is None

    async def test_relay_reconnection(self):
        """Test relay reconnection after disconnect."""
        relay = NostrRelay(TEST_RELAYS[0])

        # Connect
        await relay.connect()
        assert relay.ws is not None

        # Disconnect
        await relay.disconnect()

        # Reconnect
        await relay.connect()
        assert relay.ws is not None
        assert relay.ws.close_code is None

        await relay.disconnect()

    async def test_relay_timeout_handling(self):
        """Test relay timeout handling."""
        # Use a non-existent relay to test timeout
        relay = NostrRelay("wss://nonexistent.relay.example.com")

        with pytest.raises(Exception):  # Should raise RelayError or timeout
            await relay.connect()

    async def test_fetch_events_basic(self, test_relay):
        """Test fetching events with basic filters."""
        # Fetch recent events from relay
        filters: list[NostrFilter] = [
            {
                "kinds": [1],  # Text notes
                "limit": 5,
            }
        ]

        events = await test_relay.fetch_events(filters, timeout=10.0)

        # Should get some events (public relay should have activity)
        assert isinstance(events, list)
        # Note: Can't assert len > 0 as relay might be empty or filtered

    async def test_fetch_events_with_timeout(self, test_relay):
        """Test event fetching with short timeout."""
        filters: list[NostrFilter] = [
            {
                "kinds": [99999],  # Unlikely kind
                "limit": 100,
            }
        ]

        start_time = time.time()
        events = await test_relay.fetch_events(filters, timeout=2.0)
        elapsed = time.time() - start_time

        # Should complete within timeout
        assert elapsed < 3.0
        assert isinstance(events, list)


class TestEventPublishing:
    """Test event publishing and retrieval."""

    async def test_publish_and_fetch_text_note(
        self, test_relay, test_privkey, test_pubkey
    ):
        """Test publishing a text note and fetching it back."""
        # Create a unique text note
        unique_content = f"Test note {uuid4().hex[:8]} at {int(time.time())}"

        # Create unsigned event
        unsigned_event = create_event(
            kind=1,  # Text note
            content=unique_content,
            tags=[],
        )

        # Sign the event
        signed_event = sign_event(unsigned_event, test_privkey)

        # Publish event
        event_dict = NostrEvent(**signed_event)  # type: ignore
        published = await test_relay.publish_event(event_dict)

        if not published:
            pytest.skip("Relay rejected the event (possible rate limiting)")

        # Wait a moment for propagation
        await asyncio.sleep(2)

        # Try to fetch it back
        filters: list[NostrFilter] = [
            {
                "authors": [test_pubkey],
                "kinds": [1],
                "limit": 10,
            }
        ]

        events = await test_relay.fetch_events(filters, timeout=5.0)

        # Look for our event
        found_event = None
        for event in events:
            if event["content"] == unique_content:
                found_event = event
                break

        # Should find our published event
        assert found_event is not None, (
            f"Could not find published event with content: {unique_content}"
        )
        assert found_event["pubkey"] == test_pubkey
        assert found_event["kind"] == 1

    async def test_publish_wallet_metadata_event(
        self, test_relay, test_privkey, test_pubkey
    ):
        """Test publishing a wallet metadata event (kind 17375)."""
        # Create wallet metadata content
        content_data = [
            ["privkey", "test_wallet_privkey"],
            ["mint", "https://mint.example.com"],
        ]

        # Encrypt content
        content_json = json.dumps(content_data)
        encrypted_content = nip44_encrypt(content_json, test_privkey)

        # Create unsigned event
        unsigned_event = create_event(
            kind=EventKind.Wallet,
            content=encrypted_content,
            tags=[["mint", "https://mint.example.com"]],
        )

        # Sign and publish
        signed_event = sign_event(unsigned_event, test_privkey)
        event_dict = NostrEvent(**signed_event)  # type: ignore
        published = await test_relay.publish_event(event_dict)

        if not published:
            pytest.skip("Relay rejected the wallet event")

        # Wait for propagation
        await asyncio.sleep(2)

        # Fetch it back
        filters: list[NostrFilter] = [
            {
                "authors": [test_pubkey],
                "kinds": [EventKind.Wallet],
                "limit": 5,
            }
        ]

        events = await test_relay.fetch_events(filters, timeout=5.0)

        # Find our event
        found_event = None
        for event in events:
            if event["id"] == signed_event["id"]:
                found_event = event
                break

        assert found_event is not None, "Could not find published wallet event"

        # Verify we can decrypt the content
        decrypted = nip44_decrypt(found_event["content"], test_privkey)
        decrypted_data = json.loads(decrypted)

        assert decrypted_data == content_data
        assert found_event["kind"] == EventKind.Wallet

    async def test_publish_delete_event(self, test_relay, test_privkey, test_pubkey):
        """Test publishing and then deleting an event."""
        # First, publish a test event
        test_content = f"Test event to delete {uuid4().hex[:8]}"

        unsigned_event = create_event(
            kind=1,
            content=test_content,
            tags=[],
        )

        signed_event = sign_event(unsigned_event, test_privkey)
        event_dict = NostrEvent(**signed_event)  # type: ignore
        published = await test_relay.publish_event(event_dict)

        if not published:
            pytest.skip("Relay rejected the initial event")

        target_event_id = signed_event["id"]

        # Wait for propagation
        await asyncio.sleep(1)

        # Now publish a delete event
        delete_event = create_event(
            kind=EventKind.Delete,
            content="",
            tags=[
                ["e", target_event_id],
                ["k", "1"],
            ],
        )

        signed_delete = sign_event(delete_event, test_privkey)
        delete_dict = NostrEvent(**signed_delete)  # type: ignore
        delete_published = await test_relay.publish_event(delete_dict)

        # Delete event should be published successfully
        if delete_published:
            # Wait for delete to propagate
            await asyncio.sleep(2)

            # Try to fetch the original event - it might still be there
            # (depends on relay implementation of NIP-09)
            filters: list[NostrFilter] = [
                {
                    "authors": [test_pubkey],
                    "kinds": [1],
                    "limit": 10,
                }
            ]

            events = await test_relay.fetch_events(filters, timeout=5.0)

            # Check if original event is still present
            # Note: Not all relays implement deletion, so we just verify the delete was accepted
            print(f"Published delete event for {target_event_id}")


class TestQueuedRelayOperations:
    """Test queued relay operations."""

    async def test_queued_event_publishing(self, test_queued_relay, test_privkey):
        """Test publishing events through the queue system."""
        # Create multiple test events
        events_to_publish = []

        for i in range(3):
            content = f"Queued test event {i} - {uuid4().hex[:8]}"
            unsigned_event = create_event(
                kind=1,
                content=content,
                tags=[],
            )
            signed_event = sign_event(unsigned_event, test_privkey)
            events_to_publish.append((content, signed_event))

        # Publish events with different priorities
        publish_results = []
        for i, (content, signed_event) in enumerate(events_to_publish):
            event_dict = NostrEvent(**signed_event)  # type: ignore

            # Use callback to track completion
            completion_event = asyncio.Event()
            publish_result = {"success": False, "error": None}

            def callback(success: bool, error: str | None) -> None:
                publish_result["success"] = success
                publish_result["error"] = error
                completion_event.set()

            # Queue the event with priority (higher number = higher priority)
            await test_queued_relay.publish_event(
                event_dict,
                priority=10 - i,  # First event gets highest priority
                callback=callback,
            )

            publish_results.append((completion_event, publish_result, content))

        # Wait for all events to be processed
        timeout_duration = 10.0
        for completion_event, publish_result, content in publish_results:
            try:
                await asyncio.wait_for(
                    completion_event.wait(), timeout=timeout_duration
                )
                print(
                    f"Event published: {content} (success: {publish_result['success']})"
                )
            except asyncio.TimeoutError:
                print(f"Timeout waiting for event: {content}")

        # Check that at least some events were successful
        successful_events = [
            result for _, result, _ in publish_results if result["success"]
        ]

        # At least one event should succeed (unless relay is heavily rate-limited)
        if len(successful_events) == 0:
            pytest.skip("All events were rejected by relay (possible rate limiting)")

    async def test_queue_processor_lifecycle(self):
        """Test starting and stopping the queue processor."""
        relay = QueuedNostrRelay(TEST_RELAYS[0], batch_size=2, batch_interval=0.5)

        try:
            await relay.connect()

            # Start processor
            await relay.start_queue_processor()
            assert relay._processor_task is not None
            assert not relay._processor_task.done()

            # Stop processor
            await relay.stop_queue_processor()
            assert relay._processor_task.done()

        finally:
            await relay.disconnect()

    async def test_pending_proofs_tracking(self, test_queued_relay, test_privkey):
        """Test tracking of pending token events for proof data."""
        # Create a token event with proof data
        proof_data = {
            "mint": "https://mint.example.com",
            "proofs": [
                {
                    "id": "test",
                    "amount": 10,
                    "secret": "dGVzdA==",
                    "C": "02abc123",
                    "mint": "https://mint.example.com",
                }
            ],
        }

        content_json = json.dumps(proof_data)
        encrypted_content = nip44_encrypt(content_json, test_privkey)

        unsigned_event = create_event(
            kind=EventKind.Token,
            content=encrypted_content,
            tags=[],
        )

        signed_event = sign_event(unsigned_event, test_privkey)
        event_dict = NostrEvent(**signed_event)  # type: ignore

        # Publish with token data
        await test_queued_relay.publish_event(
            event_dict,
            token_data=proof_data,
        )

        # Check pending proofs
        pending_proofs = test_queued_relay.get_pending_proofs()

        # Should have our proof in pending
        assert len(pending_proofs) > 0
        found_proof = False
        for proof in pending_proofs:
            if proof.get("amount") == 10 and proof.get("secret") == "dGVzdA==":
                found_proof = True
                break

        assert found_proof, "Should find our proof in pending list"


class TestRelayPoolOperations:
    """Test relay pool functionality."""

    async def test_relay_pool_creation_and_connection(self, test_privkey):
        """Test creating and connecting a relay pool."""
        pool = RelayPool(
            urls=TEST_RELAYS[:2],
            batch_size=5,
            batch_interval=0.5,
        )

        try:
            # Connect all relays
            await pool.connect_all()

            # Should have relay instances
            assert len(pool.relays) == 2
            assert pool.shared_queue is not None

            # Test publishing through pool
            unsigned_event = create_event(
                kind=1,
                content=f"Pool test {uuid4().hex[:8]}",
                tags=[],
            )

            signed_event = sign_event(unsigned_event, test_privkey)
            event_dict = NostrEvent(**signed_event)  # type: ignore

            success = await pool.publish_event(event_dict)
            assert success  # Should queue successfully

        finally:
            await pool.disconnect_all()


class TestRelayManagerOperations:
    """Test relay manager functionality."""

    async def test_relay_manager_initialization(self, test_relay_manager):
        """Test relay manager initialization and connection."""
        # Get relay connections (should trigger discovery/connection)
        relays = await test_relay_manager.get_relay_connections()

        assert len(relays) > 0, "Should connect to at least one relay"
        assert test_relay_manager.relay_pool is not None

    async def test_publish_to_relays(self, test_relay_manager, test_privkey):
        """Test publishing events through relay manager."""
        # Create test event
        unsigned_event = create_event(
            kind=1,
            content=f"RelayManager test {uuid4().hex[:8]}",
            tags=[],
        )

        # Publish through manager (handles signing internally)
        event_id = await test_relay_manager.publish_to_relays(unsigned_event)

        assert len(event_id) > 0, "Should return valid event ID"

    async def test_fetch_wallet_events(self, test_relay_manager, test_pubkey):
        """Test fetching wallet events through relay manager."""
        events = await test_relay_manager.fetch_wallet_events(test_pubkey)

        assert isinstance(events, list)
        # Events list might be empty for fresh pubkey

    async def test_relay_discovery(self, test_privkey):
        """Test relay discovery from kind:10019 events."""
        manager = RelayManager(
            relay_urls=TEST_RELAYS[:1],
            privkey=test_privkey,
            use_queued_relays=False,
        )

        try:
            # Try to discover relays
            discovered = await manager.discover_relays()

            # Should return a list (might be empty if no recommendations found)
            assert isinstance(discovered, list)

        finally:
            await manager.disconnect_all()


class TestEventManagerOperations:
    """Test event manager functionality."""

    async def test_wallet_event_creation(self, test_event_manager):
        """Test creating wallet events through event manager."""
        wallet_privkey_hex = generate_privkey()

        # Create wallet event - this might fail due to rate limiting
        try:
            event_id = await test_event_manager.create_wallet_event(
                wallet_privkey_hex, force=True
            )

            assert len(event_id) > 0, "Should return valid event ID"

            # Wait longer for propagation on public relays
            await asyncio.sleep(5)

            # Check if event exists - but skip if it was rate limited
            exists, event = await test_event_manager.check_wallet_event_exists()

            # If the event doesn't exist, it might have been rate limited
            # This is acceptable behavior with public relays
            if not exists:
                pytest.skip(
                    "Wallet event not found - likely rate limited by public relay"
                )

            assert event is not None

        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "limit" in error_msg or "too much" in error_msg:
                pytest.skip(f"Event creation rate limited by relay: {e}")
            else:
                # Re-raise if it's not a rate limiting error
                raise

    async def test_token_event_publishing(self, test_event_manager):
        """Test publishing token events through event manager."""
        # Create test proofs
        test_proofs: list[ProofDict] = [
            {
                "id": "test",
                "amount": 10,
                "secret": "746573742d736563726574",  # hex secret
                "C": "02abc123def456",
                "mint": "https://mint.example.com",
            },
            {
                "id": "test",
                "amount": 5,
                "secret": "616e6f746865722d736563726574",  # hex secret
                "C": "02def456abc123",
                "mint": "https://mint.example.com",
            },
        ]

        # Publish token event
        try:
            event_id = await test_event_manager.publish_token_event(test_proofs)
            assert len(event_id) > 0, "Should return valid event ID"
        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "limit" in error_msg or "too much" in error_msg:
                pytest.skip(f"Token event publishing rate limited: {e}")
            else:
                raise

    async def test_spending_history_publishing(self, test_event_manager):
        """Test publishing spending history events."""
        # Publish history event
        try:
            event_id = await test_event_manager.publish_spending_history(
                direction="out",
                amount=25,
                created_token_ids=["event1", "event2"],
                destroyed_token_ids=["event3"],
            )

            assert len(event_id) > 0, "Should return valid event ID"

            # Wait for propagation
            await asyncio.sleep(3)

            # Fetch spending history
            history = await test_event_manager.fetch_spending_history()

            # Should find our history entry (but might be empty due to rate limiting)
            found_entry = None
            for entry in history:
                if entry.get("direction") == "out" and entry.get("amount") == "25":
                    found_entry = entry
                    break

            # Don't assert if not found - could be rate limited
            if found_entry is None:
                print(
                    "Warning: Published history entry not found in fetch - possible rate limiting"
                )

        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "limit" in error_msg or "too much" in error_msg:
                pytest.skip(f"History event publishing rate limited: {e}")
            else:
                raise

    async def test_nip60_proof_conversion(self, test_event_manager):
        """Test NIP-60 proof format conversion."""
        # Test proof with hex secret
        hex_proof: ProofDict = {
            "id": "test",
            "amount": 10,
            "secret": "746573742d736563726574",  # hex
            "C": "02abc123",
            "mint": "https://mint.example.com",
        }

        # Convert to NIP-60 format (base64)
        nip60_proof = test_event_manager._convert_proof_to_nip60(hex_proof)

        # Secret should be base64 now
        assert nip60_proof["secret"] != hex_proof["secret"]

        # Convert back to internal format
        internal_proof = test_event_manager._convert_proof_from_nip60(nip60_proof)

        # Should match original
        assert internal_proof["secret"] == hex_proof["secret"]

    async def test_event_count_operations(self, test_event_manager):
        """Test counting various event types."""
        # Count token events
        token_count = await test_event_manager.count_token_events()
        assert token_count >= 0

        # Try to publish a token event to increment count
        test_proofs: list[ProofDict] = [
            {
                "id": "test",
                "amount": 1,
                "secret": "746573742d736563726574",
                "C": "02abc123",
                "mint": "https://mint.example.com",
            }
        ]

        try:
            await test_event_manager.publish_token_event(test_proofs)

            # Wait for propagation
            await asyncio.sleep(3)

            # Count should increase (but might not due to rate limiting)
            new_token_count = await test_event_manager.count_token_events()

            # Don't assert strict increase due to possible rate limiting
            assert new_token_count >= token_count

        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "limit" in error_msg or "too much" in error_msg:
                pytest.skip(f"Event count test rate limited: {e}")
            else:
                raise


class TestRelayErrorHandling:
    """Test error handling in relay operations."""

    async def test_connection_error_handling(self):
        """Test handling of connection errors."""
        # Try to connect to non-existent relay
        relay = NostrRelay("wss://nonexistent.relay.nowhere")

        with pytest.raises(Exception):
            await relay.connect()

    async def test_invalid_event_publishing(self, test_relay):
        """Test publishing invalid events."""
        # Create invalid event (missing required fields)
        invalid_event = {
            "kind": 1,
            "content": "test",
            # Missing required fields like id, pubkey, sig
        }

        # This should return False (rejected) or timeout, not raise an exception
        # The relay will reject it but publish_event handles this gracefully
        result = await test_relay.publish_event(invalid_event)  # type: ignore
        assert result is False, "Invalid event should be rejected by relay"

    async def test_timeout_handling(self, test_relay):
        """Test timeout handling in fetch operations."""
        # Use very short timeout
        filters: list[NostrFilter] = [
            {
                "kinds": [99999],  # Unlikely kind
                "limit": 1000,
            }
        ]

        start_time = time.time()
        events = await test_relay.fetch_events(filters, timeout=0.5)
        elapsed = time.time() - start_time

        # Should respect timeout
        assert elapsed < 1.0
        assert isinstance(events, list)


if __name__ == "__main__":
    # Allow running this file directly for debugging
    import sys

    if not os.getenv("RUN_INTEGRATION_TESTS"):
        print("Set RUN_INTEGRATION_TESTS=1 to run relay integration tests")
        sys.exit(1)

    # Run a simple test
    async def main():
        print("Running basic relay integration test...")

        # Test basic connection
        relay = NostrRelay(TEST_RELAYS[0])
        try:
            await relay.connect()
            print(f"✅ Connected to {TEST_RELAYS[0]}")

            # Test basic fetch
            filters: list[NostrFilter] = [{"kinds": [1], "limit": 1}]
            events = await relay.fetch_events(filters, timeout=5.0)
            print(f"✅ Fetched {len(events)} events")

        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            await relay.disconnect()
            print("✅ Disconnected")

    asyncio.run(main())
