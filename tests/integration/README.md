# Integration Tests

This directory contains integration tests that test the complete wallet functionality against real mint and relay infrastructure.

## Overview

The integration tests verify:

- Wallet creation and initialization
- Minting tokens (creating Lightning invoices)
- Sending and redeeming tokens
- Balance checking and proof validation
- Fee calculation and handling
- Multi-mint operations
- Error handling

## Running Integration Tests

### Method 1: Using the Integration Test Runner (Recommended)

The easiest way to run integration tests is using the automated script that manages Docker containers:

```bash
# From the project root
python tests/run_integration.py
```

This script will:

1. Start fresh Docker containers (mint + relay)
2. Wait for services to be ready
3. Set `USE_LOCAL_SERVICES=1` to use local services
4. Run all integration tests
5. Clean up containers afterward

### Method 2: Against Public Services (No Docker Required)

You can run tests directly against public services without Docker:

```bash
# Set environment variable to enable integration tests
export RUN_INTEGRATION_TESTS=1

# Run tests (will automatically use public services)
pytest tests/integration/ -v
```

This mode uses:

- **Mint**: `https://testnut.cashu.space`
- **Relays**: `wss://relay.damus.io`, `wss://relay.primal.net`, `wss://relay.nostr.band`

### Method 3: Manual Docker + pytest

If you prefer manual control over Docker services:

```bash
# Start services
docker-compose up -d

# Wait for services to be ready (check logs)
docker-compose logs -f

# In another terminal, run integration tests with local services
export RUN_INTEGRATION_TESTS=1
export USE_LOCAL_SERVICES=1
pytest tests/integration/ -v

# Clean up
docker-compose down -v
```

## Test Categories

### Basic Operations (`TestWalletBasicOperations`)

- Wallet creation and initialization
- Balance checking on empty wallet
- Mint quote creation

### Minting (`TestWalletMinting`)

- Asynchronous minting flow
- Invoice generation

### Transactions (`TestWalletTransactions`)

- Send and redeem flow
- Multiple send operations
- End-to-end token transfers

### Proof Management (`TestWalletProofManagement`)

- Proof validation
- Proof consolidation

### Fee Calculation (`TestWalletFeeCalculation`)

- Input fee calculation
- Fee handling with different rates

### Token Parsing (`TestWalletTokenParsing`)

- CashuA (V3) token parsing
- Invalid token handling

### Error Handling (`TestWalletErrorHandling`)

- Insufficient balance errors
- Invalid currency units

## Test Environment

### Docker Services

The integration tests use these Docker services (defined in `compose.yml`):

- **Cashu Mint**: `localhost:3338` - Local mint for testing token operations
- **Nostr Relay**: `localhost:8080` - Local relay for NIP-60 wallet events

### Fresh State

Each test run uses fresh containers with no persistent data, ensuring:

- Clean mint state (no existing tokens)
- Empty relay state (no existing events)
- Isolated test environment

## Test Configuration

Integration tests are controlled by environment variables:

- `RUN_INTEGRATION_TESTS=1` - Must be set to run integration tests
- `USE_LOCAL_SERVICES=1` - Optional: Use local Docker services instead of public services

### Service Selection

The tests automatically choose which services to use:

**Without `USE_LOCAL_SERVICES` (default):**

- **Mint**: `https://testnut.cashu.space` (public test mint)
- **Relays**: Public Nostr relays (damus.io, primal.net, nostr.band)
- **Requirements**: Internet connection only
- **Best for**: Quick testing, CI/CD, development without Docker

**With `USE_LOCAL_SERVICES=1`:**

- **Mint**: `http://localhost:3338` (local Docker container)
- **Relays**: `ws://localhost:8080` (local Docker container)
- **Requirements**: Docker and docker-compose
- **Best for**: Isolated testing, debugging, mint development

This ensures:

- `pytest` alone runs only unit tests
- Integration tests run only when explicitly requested
- Services are automatically selected based on environment
- No hardcoded service URLs in test code

## Debugging Integration Tests

### View Service Logs

```bash
# View all service logs
docker-compose logs -f

# View specific service
docker-compose logs -f cashu-mint
docker-compose logs -f nostr-relay
```

### Manual Service Testing

```bash
# Test mint directly
curl http://localhost:3338/v1/info

# Test relay (requires WebSocket client)
wscat -c ws://localhost:8080
```

### Running Single Tests

```bash
# Run specific test class
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_wallet_complete_flow.py::TestWalletBasicOperations -v

# Run specific test method
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_wallet_complete_flow.py::TestWalletBasicOperations::test_wallet_creation_and_initialization -v
```

## Adding New Integration Tests

When adding new integration tests:

1. Add them to the appropriate test class in `test_wallet_complete_flow.py`
2. Use the `wallet` fixture for basic tests
3. Use the `funded_wallet` fixture for tests requiring balance
4. Mark tests with `@pytest.mark.skip()` if they require manual intervention
5. Ensure tests clean up after themselves

Example test:

```python
async def test_new_feature(self, wallet):
    """Test a new wallet feature."""
    # Setup
    initial_balance = await wallet.get_balance()
    
    # Test action
    result = await wallet.new_feature()
    
    # Assertions
    assert result is not None
    
    # Cleanup (if needed)
    # ...
```

## Troubleshooting

### Services Not Starting

```bash
# Check Docker daemon
docker --version
docker-compose --version

# Check port conflicts
sudo lsof -i :3338
sudo lsof -i :8080

# Force clean state
docker-compose down -v
docker system prune -f
```

### Tests Failing

1. Check service health: `docker-compose logs`
2. Verify services are ready: `curl http://localhost:3338/v1/info`
3. Run tests with more verbose output: `pytest -vvv`
4. Check for port conflicts or firewall issues

### Memory/Performance Issues

The Docker containers are lightweight but may consume resources:

```bash
# Monitor resource usage
docker stats

# Limit resources in compose.yml if needed
```

## Notes

- Integration tests may take longer to run (30-60 seconds)
- Tests require network access for Docker image pulls
- Some tests are skipped by default (require manual intervention like paying invoices)
- The test runner automatically handles container lifecycle

## Setup

Set the environment variable to enable integration tests:

```bash
export RUN_INTEGRATION_TESTS=1
```

## Test Files

### test_wallet_complete_flow.py

Tests the complete wallet functionality against real mint and relay infrastructure.

### test_lnurl_requests.py  

Tests LNURL functionality against real services.

### test_mint_methods.py

Tests mint API methods against real mint services.

### test_relay_lookup.py

**NEW**: Comprehensive relay integration tests that test both `relay.py` and `events.py` functionality using public Nostr relays.

#### What it tests

**Basic Relay Operations:**

- Connection establishment and reconnection
- Event fetching with filters and timeouts
- Timeout handling and error recovery

**Event Publishing & Retrieval:**

- Publishing text notes and fetching them back
- Publishing NIP-60 wallet metadata events (kind 17375)
- Publishing delete events (NIP-09)
- End-to-end publish/fetch verification

**Queued Relay Operations:**

- Event queuing system with priorities
- Batch processing and callbacks
- Queue processor lifecycle management
- Pending proofs tracking for token events

**Relay Pool & Manager:**

- Multi-relay pool creation and management
- Relay discovery from kind:10019 events
- Publishing through relay managers
- Connection management and cleanup

**Event Manager Operations:**

- Wallet event creation and verification
- Token event publishing with NIP-60 format conversion
- Spending history events (kind 7376)
- Proof format conversion (hex ↔ base64)
- Event counting operations

**Error Handling:**

- Connection timeout handling
- Invalid event rejection
- Graceful failure modes

#### Public Relays Used

- `wss://relay.damus.io` - Primary test relay
- `wss://relay.primal.net` - Secondary relay  
- `wss://relay.nostr.band` - Tertiary relay

#### Key Features

- Uses fresh generated nsec keys for each test run
- Tests against real public relays (no mocking)
- Handles rate limiting gracefully with `pytest.skip()`
- Comprehensive coverage of both relay.py and events.py
- Tests both successful operations and error conditions
- Robust error detection for relay-specific responses
- Automatic fallback when relays are unavailable or rate limited

#### Running

```bash
# Run all relay integration tests
RUN_INTEGRATION_TESTS=1 python -m pytest tests/integration/test_relay_lookup.py -v

# Run specific test categories
RUN_INTEGRATION_TESTS=1 python -m pytest tests/integration/test_relay_lookup.py::TestBasicRelayOperations -v
RUN_INTEGRATION_TESTS=1 python -m pytest tests/integration/test_relay_lookup.py::TestEventPublishing -v

# Run the basic standalone test
RUN_INTEGRATION_TESTS=1 python tests/integration/test_relay_lookup.py
```

The tests are designed to be robust against public relay quirks including rate limiting, temporary unavailability, and varying relay policies. Tests that encounter rate limiting will skip gracefully rather than fail, as this is expected behavior when using public infrastructure.

**Expected Results:** ~21 passed, ~3 skipped (due to rate limiting), 0 failed

### test_wallet_complete_flow.py

Tests complete wallet operations that require both mint and relay services.

## Quick Start Examples

### Run against public services (no Docker needed)

```bash
# Simply enable integration tests - uses public services by default
export RUN_INTEGRATION_TESTS=1
pytest tests/integration/ -v
```

### Run with local Docker services

```bash
# Use the automated runner (recommended)
python tests/run_integration.py

# Or manually
docker-compose up -d
export RUN_INTEGRATION_TESTS=1
export USE_LOCAL_SERVICES=1
pytest tests/integration/ -v
docker-compose down -v
```

### Quick verification

```bash
# Test that public services work
export RUN_INTEGRATION_TESTS=1
python tests/integration/test_wallet_complete_flow.py
```
