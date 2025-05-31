#!/usr/bin/env python3
"""Test Nostr Relay client."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from sixty_nuts.relay import NostrRelay, RelayError, NostrEvent, NostrFilter


@pytest.fixture
async def relay():
    """Create a relay instance for testing."""
    relay = NostrRelay("wss://relay.test.com")
    yield relay
    if relay.ws and not relay.ws.closed:
        await relay.disconnect()


@pytest.fixture
def mock_websocket():
    """Create a mock websocket."""
    ws = AsyncMock()
    ws.closed = False
    ws.close_code = None  # Important: websocket state check
    return ws


class TestNostrRelay:
    """Test cases for NostrRelay class."""

    async def test_relay_initialization(self):
        """Test relay initialization."""
        relay = NostrRelay("wss://relay.test.com")
        assert relay.url == "wss://relay.test.com"
        assert relay.ws is None
        assert relay.subscriptions == {}

    @patch("sixty_nuts.relay.websockets.connect")
    async def test_connect(self, mock_connect):
        """Test relay connection."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.close_code = None

        # Make mock_connect async and return the mock_ws
        async def async_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = async_connect

        relay = NostrRelay("wss://relay.test.com")
        await relay.connect()

        assert relay.ws == mock_ws
        # Update assertion to match the actual call with parameters
        mock_connect.assert_called_once_with(
            "wss://relay.test.com", ping_interval=20, ping_timeout=10, close_timeout=10
        )

    async def test_disconnect(self, relay, mock_websocket):
        """Test relay disconnection."""
        relay.ws = mock_websocket
        mock_websocket.close = AsyncMock()  # Make close method async
        await relay.disconnect()

        mock_websocket.close.assert_called_once()

    async def test_send_not_connected(self, relay):
        """Test sending when not connected."""
        with pytest.raises(RelayError, match="Not connected to relay"):
            await relay._send(["EVENT", {}])

    async def test_send(self, relay, mock_websocket):
        """Test sending messages."""
        relay.ws = mock_websocket
        message = ["EVENT", {"id": "test"}]

        await relay._send(message)

        mock_websocket.send.assert_called_once_with(json.dumps(message))

    async def test_recv(self, relay, mock_websocket):
        """Test receiving messages."""
        relay.ws = mock_websocket
        mock_websocket.recv.return_value = '["OK", "event_id", true]'

        message = await relay._recv()

        assert message == ["OK", "event_id", True]

    @patch("sixty_nuts.relay.websockets.connect")
    async def test_publish_event(self, mock_connect, relay):
        """Test publishing an event."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.close_code = None

        # Make mock_connect async and return the mock_ws
        async def async_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = async_connect

        # Mock the response sequence
        mock_ws.recv.return_value = '["OK", "event123", true]'

        event = NostrEvent(
            id="event123",
            pubkey="pubkey123",
            created_at=1234567890,
            kind=1,
            tags=[],
            content="Test event",
            sig="sig123",
        )

        result = await relay.publish_event(event)

        assert result is True

        # Check that EVENT command was sent
        sent_data = mock_ws.send.call_args[0][0]
        sent_message = json.loads(sent_data)
        assert sent_message[0] == "EVENT"
        assert sent_message[1]["id"] == "event123"

    @patch("sixty_nuts.relay.websockets.connect")
    @patch("sixty_nuts.relay.uuid4", return_value="sub_id")
    async def test_fetch_events(self, mock_uuid, mock_connect):
        """Test fetching events."""
        mock_websocket = AsyncMock()
        mock_websocket.closed = False
        mock_websocket.close_code = None

        # Make mock_connect async and return the mock_websocket
        async def async_connect(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = async_connect

        relay = NostrRelay("wss://relay.test.com")

        # Mock the response sequence
        event1 = {
            "id": "event1",
            "pubkey": "pubkey1",
            "created_at": 1234567890,
            "kind": 1,
            "tags": [],
            "content": "Event 1",
            "sig": "sig1",
        }
        event2 = {
            "id": "event2",
            "pubkey": "pubkey1",
            "created_at": 1234567891,
            "kind": 1,
            "tags": [],
            "content": "Event 2",
            "sig": "sig2",
        }

        # Return events and then EOSE
        mock_websocket.recv.side_effect = [
            json.dumps(["EVENT", "sub_id", event1]),
            json.dumps(["EVENT", "sub_id", event2]),
            json.dumps(["EOSE", "sub_id"]),
        ]

        filters = [NostrFilter(authors=["pubkey1"], kinds=[1])]
        events = await relay.fetch_events(filters)

        assert len(events) == 2
        assert events[0]["id"] == "event1"
        assert events[1]["id"] == "event2"

        # Check that CLOSE was sent
        calls = mock_websocket.send.call_args_list
        close_sent = False
        for call in calls:
            message = json.loads(call[0][0])
            if message[0] == "CLOSE":
                close_sent = True
                break
        assert close_sent

    @patch("sixty_nuts.relay.websockets.connect")
    async def test_subscribe(self, mock_connect, relay):
        """Test subscribing to events."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.close_code = None

        # Make mock_connect async and return the mock_ws
        async def async_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = async_connect

        callback = MagicMock()
        filters = [NostrFilter(kinds=[17375])]

        sub_id = await relay.subscribe(filters, callback)

        assert sub_id in relay.subscriptions
        assert relay.subscriptions[sub_id] == callback

        # Check that REQ was sent
        sent_data = mock_ws.send.call_args[0][0]
        sent_message = json.loads(sent_data)
        assert sent_message[0] == "REQ"
        assert sent_message[1] == sub_id
        assert sent_message[2] == filters[0]

    async def test_unsubscribe(self, relay, mock_websocket):
        """Test unsubscribing."""
        relay.ws = mock_websocket
        sub_id = "test_sub"
        relay.subscriptions[sub_id] = MagicMock()

        await relay.unsubscribe(sub_id)

        assert sub_id not in relay.subscriptions

        # Check that CLOSE was sent
        sent_data = mock_websocket.send.call_args[0][0]
        sent_message = json.loads(sent_data)
        assert sent_message == ["CLOSE", sub_id]

    @patch("sixty_nuts.relay.websockets.connect")
    async def test_fetch_wallet_events(self, mock_connect, relay):
        """Test fetching wallet events."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.close_code = None

        # Make mock_connect async and return the mock_ws
        async def async_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = async_connect

        # Mock EOSE response
        mock_ws.recv.return_value = json.dumps(["EOSE", "sub_id"])

        pubkey = "test_pubkey"
        await relay.fetch_wallet_events(pubkey)

        # Check the filter was correct
        sent_data = mock_ws.send.call_args_list[0][0][0]
        sent_message = json.loads(sent_data)
        assert sent_message[0] == "REQ"
        filter_obj = sent_message[2]
        assert filter_obj["authors"] == [pubkey]
        assert filter_obj["kinds"] == [17375, 7375, 7376, 7374]

    @patch("sixty_nuts.relay.websockets.connect")
    async def test_fetch_relay_recommendations(self, mock_connect, relay):
        """Test fetching relay recommendations."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        mock_ws.close_code = None

        # Make mock_connect async and return the mock_ws
        async def async_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = async_connect

        # Mock response with relay recommendations
        event = {
            "id": "event1",
            "pubkey": "pubkey1",
            "created_at": 1234567890,
            "kind": 10019,
            "tags": [
                ["relay", "wss://relay1.com"],
                ["relay", "wss://relay2.com"],
                ["other", "ignored"],
            ],
            "content": "",
            "sig": "sig1",
        }

        mock_ws.recv.side_effect = [
            json.dumps(["EVENT", "sub_id", event]),
            json.dumps(["EOSE", "sub_id"]),
        ]

        pubkey = "pubkey1"
        relays = await relay.fetch_relay_recommendations(pubkey)

        assert len(relays) == 2
        assert "wss://relay1.com" in relays
        assert "wss://relay2.com" in relays


@pytest.mark.asyncio
async def test_relay_lifecycle():
    """Test the full lifecycle of relay operations."""
    relay = NostrRelay("wss://relay.test.com")

    # Can't actually connect without a real relay
    # Just test that the object is created properly
    assert relay.url == "wss://relay.test.com"
    assert relay.ws is None
